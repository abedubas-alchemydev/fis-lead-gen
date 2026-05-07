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


# Apex domains we never accept as a firm's primary website. ``_is_blocked_domain``
# matches each entry as either the exact host or any subdomain, so a single
# ``finra.org`` entry covers ``brokercheck.finra.org`` AND
# ``files.brokercheck.finra.org`` (the BrokerCheck PDF CDN — Google ranks
# the Detailed Report PDF as the top organic result for many small firms
# with no public site, and pre-suffix-match those URLs were stamping
# ``broker_dealer.website`` with the FINRA CDN host).
DOMAIN_BLOCKLIST: Final = frozenset(
    {
        # Regulator portals — covers brokercheck/files.brokercheck/adviserinfo/efts/data
        "finra.org",
        "sec.gov",
        # Social and people-search
        "linkedin.com",
        "facebook.com",
        "twitter.com",
        "x.com",
        "instagram.com",
        "youtube.com",
        # Major news outlets that frequently rank above small-firm sites
        "bloomberg.com",
        "reuters.com",
        "wsj.com",
        "nytimes.com",
    }
)

# URL-path suffixes that signal "this is a file download, not a homepage".
# A firm's primary website is never a PDF / Word doc / Excel sheet, but
# SerpAPI happily returns hosted PDFs as the top organic result for
# obscure firms (BrokerCheck Detailed Report being the canonical offender).
_BLOCKED_PATH_SUFFIXES: Final = (".pdf", ".doc", ".docx", ".xls", ".xlsx")


_VALIDATE_TIMEOUT_S: Final = 5.0
_FIRM_TOKEN_LEN: Final = 8
_TITLE_RE: Final = re.compile(r"<title[^>]*>([^<]*)</title>", re.IGNORECASE)
_NON_ALPHA_RE: Final = re.compile(r"[^a-z]")


def is_blocklisted_host(url: str | None) -> bool:
    """Return True when ``url``'s host (or any parent label-suffix of it)
    is in :data:`DOMAIN_BLOCKLIST`.

    Suffix matching means ``brokercheck.finra.org`` in the blocklist also
    catches ``files.brokercheck.finra.org`` (the PDF host) and any future
    sibling subdomain — an entry asserts "this domain and everything
    under it is administrative/aggregator infrastructure, not a firm
    website." ``sec.gov`` similarly catches ``adviserinfo.sec.gov``,
    ``linkedin.com`` catches ``www.linkedin.com``, and so on.

    Shared with the FINRA enumeration writer so a brokercheck/finra/sec.gov
    self-reference URL never gets persisted onto ``broker_dealer.website``,
    even when it appears in a Form-BD-canonical key. Empty / unparseable
    inputs return ``False`` — callers handle the empty-string case via
    their own truthy guard before calling this.
    """
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    parts = host.split(".")
    for i in range(len(parts)):
        if ".".join(parts[i:]) in DOMAIN_BLOCKLIST:
            return True
    return False


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
    # Walks ALL organic results returned by the client (already capped at
    # 10) so a strong-but-not-first hit can still win once the earlier
    # ones get rejected by blocklist / content-type / title checks. The
    # earlier top-5 cap missed firms whose own homepage ranked 6+ behind
    # LinkedIn, news articles, BrokerCheck PDFs, and aggregator listings
    # — concretely BANKERS LIFE SECURITIES, INC, where bankerslife.com
    # was rank 6 behind five rejected hits. Walking all 10 costs nothing
    # extra (SerpAPI is already paid for and returned), and the validator
    # is the strict gate.
    if serpapi is not None:
        providers_attempted += 1
        try:
            results = await serpapi.search_firm(firm_name)
            for result in results:
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
    """Run path/host blocklist + HEAD reachability + title-or-domain check on ``url``.

    Returns ``False`` on any network error, non-200/301/302 status,
    blocklisted host (apex or subdomain), file-download path, or page
    that fails BOTH the title-match AND the domain-match fallback. A
    page with no ``<title>`` is allowed through as long as HEAD passed
    and the domain + path are clear — small broker-dealer sites often
    render an empty title from a JS shell.

    Domain-match fallback: when the page title is present but doesn't
    carry the firm token, check whether the firm token is a prefix of
    any ``.``- or ``-``-delimited segment of the final hostname
    (post-redirect). Catches firms whose homepage uses a brand or
    marketing tagline as its title (e.g., ``303alternatives.com`` with
    title ``"Welcome"``). The segment-prefix anchor blocks the
    obvious false-positive class — ``securiti`` won't match an unrelated
    ``consecutivesecurities.com``, only domains that *start* with the
    firm token.
    """
    if not url or not firm_token:
        return False

    if is_blocklisted_host(url) or _is_blocked_path(url):
        return False
    domain = _hostname(url)
    if not domain:
        return False

    try:
        async with httpx.AsyncClient(
            timeout=_VALIDATE_TIMEOUT_S,
            follow_redirects=True,
        ) as client:
            head = await client.head(url)
            if head.status_code not in (200, 301, 302):
                return False

            # Re-check the final URL after redirects so a candidate that
            # redirects to a blocked vendor (LinkedIn) or to a file
            # download (a SaaS landing page that 302s to a hosted PDF)
            # still gets rejected.
            final_url = str(head.url)
            if is_blocklisted_host(final_url) or _is_blocked_path(final_url):
                return False
            final_host = _hostname(final_url)

            page = await client.get(url, timeout=_VALIDATE_TIMEOUT_S)
            match = _TITLE_RE.search(page.text or "")
            if match is None:
                # JS-shell or empty-title sites: admit on reachability
                # alone, same as before. The blocklist + redirect check
                # already filtered the obvious garbage.
                return True
            title_token = _NON_ALPHA_RE.sub("", match.group(1).lower())
            if firm_token in title_token:
                return True
            # Title-match failed → domain-match fallback.
            return _domain_segment_starts_with(
                final_host or domain, firm_token
            )
    except httpx.HTTPError as exc:
        logger.info("Website validate failed for %s: %s", url, exc)
        return False


def _domain_segment_starts_with(host: str, firm_token: str) -> bool:
    """Return True when ``firm_token`` aligns to a "natural" boundary in
    ``host``. Two checks, OR'd:

    1. **Full-host prefix**: the hostname (after stripping a leading
       ``www.``) with all non-alpha chars removed *starts with* the
       firm token. Catches hyphenated domains where the token spans
       segments — e.g., ``acme-securities.com`` with firm ``Acme
       Securities`` (firm_token ``"acmesecu"``) → strips to
       ``"acmesecuritiescom"`` → matches.
    2. **Per-segment prefix**: any ``.``- or ``-``-delimited segment of
       the host, after per-segment non-alpha strip, starts with the
       firm token. Catches subdomain layouts — e.g.,
       ``trade.smithcapital.com`` with firm ``Smith Capital`` (token
       ``"smithcap"``) → segment ``"smithcapital"`` matches.

    Both checks are *prefix*, never substring — so the bad-class
    false positive (firm token appearing in the middle of an
    unrelated word, e.g., ``"securiti"`` matching
    ``consecutivesecurities.com``) is structurally blocked.
    """
    if not host or not firm_token:
        return False
    host_lower = host.lower()
    if host_lower.startswith("www."):
        host_lower = host_lower[4:]

    full_stripped = _NON_ALPHA_RE.sub("", host_lower)
    if full_stripped.startswith(firm_token):
        return True

    for segment in re.split(r"[.\-]", host_lower):
        normalised = _NON_ALPHA_RE.sub("", segment)
        if normalised.startswith(firm_token):
            return True
    return False


def _is_blocked_path(url: str) -> bool:
    """Reject URLs whose path looks like a file download.

    A firm's primary website is never a PDF / Office doc / Excel sheet.
    SerpAPI sometimes ranks the BrokerCheck Detailed Report PDF above any
    other result for obscure firms with no public site, and we don't want
    that bleeding into ``broker_dealer.website``.
    """
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _BLOCKED_PATH_SUFFIXES)


def _hostname(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host or None
