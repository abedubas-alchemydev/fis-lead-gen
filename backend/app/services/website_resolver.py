"""On-demand firm-website resolver chain (Apollo -> Hunter -> SerpAPI).

Powers the lazy resolution flow that fires from the master-list firm
detail page when ``broker_dealer.website`` is null. The mass backfill
that shipped earlier in PR #233 was abandoned in favor of this lazy
on-demand model: only firms a user actually visits get resolved, and
the answer is cached on the row so the chain runs at most once per firm.

Chain
-----
  1. Apollo ``/v1/organizations/search`` (existing client; reused as-is)
  2. Hunter ``/v2/companies/find`` (firm-name -> domain)
  3. SerpAPI ``/search.json`` (last-resort Google search; takes the
     first organic result that clears the same _validate() gate the
     other tiers go through)

Validation
----------
Each candidate URL must clear three gates before it's accepted:
  a. HEAD reachability (200/301/302) — capped at 5s so a slow server
     can't hold up the chain
  b. Domain not on the blocklist (linkedin/sec.gov/finra.org/news/social)
  c. Page ``<title>`` contains a normalized firm-name token

Stops at the first valid candidate; never falls past SerpAPI.

Provider-error semantics
------------------------
Mirrors the review-queue rule from CLAUDE.md: when every attempted
provider errors out (5xx/429-after-retries / SerpAPI non-2xx), the
caller should NOT overwrite ``website`` with NULL. The function returns
``(None, None, 'all_providers_errored')`` so the endpoint can leave the
column unchanged + return the reason. A clean miss (chain ran, zero
valid candidates) returns ``(None, None, 'no_valid_candidate')`` and
the endpoint persists NULL.
"""

from __future__ import annotations

import logging
import re
from typing import Final
from urllib.parse import urlparse

import httpx

from app.services.apollo import ApolloClient, ApolloError
from app.services.hunter import HunterClient, HunterError
from app.services.serpapi import SerpAPIClient, SerpAPIError

logger = logging.getLogger(__name__)


# Domains we never accept as a firm's primary website. Kept defensive
# even though the chain isn't open-web search — Apollo and Hunter both
# return social profiles or aggregator links from time to time.
DOMAIN_BLOCKLIST: Final = frozenset(
    {
        "linkedin.com",
        "www.linkedin.com",
        "sec.gov",
        "www.sec.gov",
        "finra.org",
        "www.finra.org",
        "brokercheck.finra.org",
        "bloomberg.com",
        "www.bloomberg.com",
        "reuters.com",
        "www.reuters.com",
        "wsj.com",
        "www.wsj.com",
        "nytimes.com",
        "www.nytimes.com",
        "youtube.com",
        "www.youtube.com",
        "facebook.com",
        "www.facebook.com",
        "twitter.com",
        "www.twitter.com",
        "x.com",
        "www.x.com",
        "instagram.com",
        "www.instagram.com",
    }
)


_VALIDATE_TIMEOUT_S: Final = 5.0
_FIRM_TOKEN_LEN: Final = 8
_TITLE_RE: Final = re.compile(r"<title[^>]*>([^<]*)</title>", re.IGNORECASE)
_NON_ALPHA_RE: Final = re.compile(r"[^a-z]")


async def resolve_website(
    firm_name: str,
    crd: str | None,
    apollo: ApolloClient,
    hunter: HunterClient | None,
    serpapi: SerpAPIClient | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Run the resolver chain for ``firm_name``.

    Returns
    -------
    (website, source, reason)
      - On success: ``(url, 'apollo'|'hunter'|'serpapi', None)``
      - On clean miss (chain ran, no valid candidate): ``(None, None, 'no_valid_candidate')``
      - On total provider failure: ``(None, None, 'all_providers_errored: ...')``

    ``hunter`` and ``serpapi`` may be ``None`` when their respective API
    keys aren't configured; the chain skips that tier and falls through
    to a clean miss / partial-error case.
    """
    firm_token = _firm_token(firm_name)
    errors: list[str] = []
    providers_attempted = 0

    # Tier 1 — Apollo organizations/search
    providers_attempted += 1
    try:
        org = await apollo.search_organization(firm_name, crd)
        if org is not None:
            candidate = _candidate_from_apollo(org)
            if candidate and await _validate(candidate, firm_token):
                return (candidate, "apollo", None)
    except ApolloError as exc:
        errors.append(f"apollo: {exc}")
    except Exception as exc:  # pragma: no cover - belt + braces
        errors.append(f"apollo: {exc}")

    # Tier 2 — Hunter companies/find
    if hunter is not None:
        providers_attempted += 1
        try:
            company = await hunter.find_company(firm_name)
            if company is not None and company.domain:
                candidate = f"https://{company.domain}"
                if await _validate(candidate, firm_token):
                    return (candidate, "hunter", None)
        except HunterError as exc:
            errors.append(f"hunter: {exc}")
        except Exception as exc:  # pragma: no cover - belt + braces
            errors.append(f"hunter: {exc}")

    # Tier 3 — SerpAPI Google search (last resort)
    # Walks the top-5 organic results so a strong-but-not-first hit can
    # still win once the first few get rejected by blocklist/title checks
    # (e.g. LinkedIn or a news article ranking above the firm's own site).
    if serpapi is not None:
        providers_attempted += 1
        try:
            results = await serpapi.search_firm(firm_name)
            for result in results[:5]:
                if await _validate(result.url, firm_token):
                    return (result.url, "serpapi", None)
        except SerpAPIError as exc:
            errors.append(f"serpapi: {exc}")
        except Exception as exc:  # pragma: no cover - belt + braces
            errors.append(f"serpapi: {exc}")

    if errors and len(errors) == providers_attempted:
        return (
            None,
            None,
            "all_providers_errored: " + "; ".join(errors),
        )
    return (None, None, "no_valid_candidate")


def _candidate_from_apollo(org: object) -> str | None:
    """Extract a usable URL from an ``ApolloOrganization``.

    Apollo populates ``website_url`` on most matches; some plans drop it
    and only return ``primary_domain``, so we fall back to a bare
    ``https://<domain>`` build. Returns ``None`` when neither is set.
    """
    website = getattr(org, "website_url", None)
    if website:
        return _ensure_scheme(str(website).strip())
    domain = getattr(org, "domain", None)
    if domain:
        return f"https://{str(domain).strip().lower()}"
    return None


def _ensure_scheme(url: str) -> str:
    if not url:
        return url
    if "://" in url:
        return url
    return f"https://{url}"


def _firm_token(firm_name: str) -> str:
    """Normalize a firm name to an alpha-only token used for title matching."""
    return _NON_ALPHA_RE.sub("", firm_name.lower())[:_FIRM_TOKEN_LEN]


async def _validate(url: str, firm_token: str) -> bool:
    """Run HEAD reachability + blocklist + title-token check on ``url``.

    Returns ``False`` on any network error, non-200/301/302 status,
    blocklisted host, or title that doesn't carry the firm token. A
    page with no ``<title>`` is allowed through as long as HEAD passed
    and the domain is clear — small broker-dealer sites often render an
    empty title from a JS shell and that shouldn't be a hard reject.
    """
    if not url or not firm_token:
        return False

    domain = _hostname(url)
    if not domain or domain in DOMAIN_BLOCKLIST:
        return False

    try:
        async with httpx.AsyncClient(
            timeout=_VALIDATE_TIMEOUT_S,
            follow_redirects=True,
        ) as client:
            head = await client.head(url)
            if head.status_code not in (200, 301, 302):
                return False

            # Re-check the final hostname after redirects so a candidate
            # that redirects to LinkedIn still gets rejected.
            final_host = _hostname(str(head.url))
            if final_host and final_host in DOMAIN_BLOCKLIST:
                return False

            page = await client.get(url, timeout=_VALIDATE_TIMEOUT_S)
            match = _TITLE_RE.search(page.text or "")
            if match is None:
                return True
            title_token = _NON_ALPHA_RE.sub("", match.group(1).lower())
            return firm_token in title_token
    except httpx.HTTPError as exc:
        logger.info("Website validate failed for %s: %s", url, exc)
        return False


def _hostname(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host or None
