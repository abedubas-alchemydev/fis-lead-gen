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
from app.services.serpapi import SerpAPIClient, SerpAPIError
from app.services.serper import SerperClient, SerperError

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

# Path substrings that mark a URL as a content / aggregator / registry
# page — never the firm's homepage. The validator's title-token check
# admits any page whose ``<title>`` contains the firm name as a
# substring, and Google ranks deal-announcement / news / LEI-registry
# pages above the firm's own site for many obscure broker-dealers, so
# without this guard those URLs pass and get stamped on the firm row.
# Concrete pre-fix repros:
#   - 3WIRE ADVISORY LLC → ``hl.com/about-us/transactions/parry-labs-...``
#     (Houlihan Lokey transaction announcement page)
#   - 777 SECURITIES → ``cfo.com/news/leaders-of-miami-investment-firm-...``
#     (CFO.com news article that mentioned the firm by name)
#   - 4170 SECURITIES LLC → ``lei-lookup.com/record/254900AX56TV6OE5G885/``
#     (LEI registry record)
# Substring match against the lower-cased URL path; a slash on each
# side keeps these from matching a homepage segment that happens to
# contain the keyword in another context.
_NON_HOMEPAGE_PATH_KEYWORDS: Final = (
    "/news/",
    "/article/",
    "/articles/",
    "/transactions/",
    "/transaction/",
    "/press-release",
    "/press-releases",
    "/blog/",
    "/blogs/",
    "/events/",
    "/event/",
    "/lookup/",
    "/record/",
    "/records/",
    "/profile/",
)


_VALIDATE_TIMEOUT_S: Final = 5.0
_FIRM_TOKEN_LEN: Final = 8
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
    serpapi: SerpAPIClient | None = None,
    serper: SerperClient | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Run the resolver chain for ``firm_name``.

    Returns
    -------
    (website, source, reason)
      - On success: ``(url, 'apollo'|'serper'|'serpapi', None)``
      - On clean miss (chain ran, no valid candidate): ``(None, None, 'no_valid_candidate')``
      - On total provider failure: ``(None, None, 'all_providers_errored: ...')``

    ``serper`` and ``serpapi`` may be ``None`` when their respective API
    keys aren't configured; the chain skips that tier and falls through
    to a clean miss / partial-error case.

    Tier order
    ----------
    Apollo → serper.dev (optional) → SerpAPI. Hunter's company-find
    endpoint was previously Tier 2 but its ``/v2/companies/find``
    endpoint expects ``domain`` not ``company`` name; every
    name-based call returns 400 and we swallowed it as a clean miss.
    Removing it from the chain saves an HTTP roundtrip per firm without
    losing any signal (the contact-discovery module's separate Hunter
    integration is unaffected — that one uses different endpoints).

    serper.dev runs ahead of SerpAPI when configured because it's ~50×
    cheaper per query; SerpAPI is the canonical fallback (and is the
    primary search tier when serper.dev is unset).
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

    # Tier 2 — serper.dev Google search (optional, ahead of SerpAPI)
    # Walks ALL organic results returned by the client (already capped at
    # 10). serper.dev is structurally identical to SerpAPI for our
    # purposes (same Google organic results, same SerpResult dataclass)
    # but ~50× cheaper, so we hit it first to keep SerpAPI quota for the
    # genuine fallback case. Skipped when ``SERPER_API_KEY`` is unset.
    if serper is not None:
        providers_attempted += 1
        try:
            results = await serper.search_firm(firm_name)
            for result in results:
                if await _validate(result.url, firm_token):
                    return (result.url, "serper", None)
        except SerperError as exc:
            errors.append(f"serper: {exc}")
        except Exception as exc:  # pragma: no cover - belt + braces
            errors.append(f"serper: {exc}")

    # Tier 3 — SerpAPI Google search (canonical fallback)
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
    """Normalize a firm name to an alpha-only token used for the
    domain-anchor check."""
    return _NON_ALPHA_RE.sub("", firm_name.lower())[:_FIRM_TOKEN_LEN]


async def _validate(url: str, firm_token: str) -> bool:
    """Domain-anchor + reachability + content-type validation.

    The single admit gate is the **domain anchor**: at least one
    ``.``- / ``-``-delimited segment of the candidate's final
    (post-redirect) hostname must align to the firm token via
    ``_domain_segment_anchors_on_firm`` (forward or truncated-brand
    direction). Title-match-only used to be an admit path on its own
    but it admitted news articles, deal-announcement pages, LEI
    registries, and third-party advisor directories that happened to
    mention the firm by name — concretely it stamped
    ``cfo.com/news/...`` on 777 SECURITIES, ``hl.com/about-us/
    transactions/...`` on 3WIRE ADVISORY, ``lei-lookup.com/record/...``
    on 4170 SECURITIES, and ``retirementplanning.net/.../<slug>/<id>``
    on ACA/PRUDENT INVESTORS. With the title-only admit gone, the
    page's content / title is informational only — it can't grant
    admission without a matching domain.

    Pre-domain gates that still apply (reject before the domain
    check ever runs): host blocklist (suffix-match on
    ``DOMAIN_BLOCKLIST``), file-download path
    (``.pdf``/``.doc``/etc.), content-page path (``/news/``,
    ``/transactions/``, ``/lookup/``, etc.), HEAD non-2xx, and
    non-HTML ``Content-Type`` on either HEAD or GET.

    Returns ``False`` on any network error or any of the above gate
    rejections.
    """
    if not url or not firm_token:
        return False

    if (
        is_blocklisted_host(url)
        or _is_blocked_path(url)
        or _is_content_page_path(url)
    ):
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
            if (
                is_blocklisted_host(final_url)
                or _is_blocked_path(final_url)
                or _is_content_page_path(final_url)
            ):
                return False
            final_host = _hostname(final_url)

            # The single admit gate: the candidate's final
            # (post-redirect) hostname must anchor on the firm name.
            # The earlier title-only admit path was the loose one —
            # any page whose ``<title>`` mentioned the firm by name
            # passed it, including news articles (``cfo.com/news/...``),
            # deal-announcement pages (``hl.com/about-us/transactions/
            # ...``), LEI-registry records (``lei-lookup.com/record/
            # ...``), and third-party advisor directories
            # (``retirementplanning.net/.../firm-slug/<id>``). With
            # title parsing dropped, the GET request is unnecessary —
            # HEAD + blocklist + path-keyword + domain-anchor is the
            # full chain.
            return _domain_segment_anchors_on_firm(
                final_host or domain, firm_token
            )
    except httpx.HTTPError as exc:
        logger.info("Website validate failed for %s: %s", url, exc)
        return False


_MIN_TRUNCATED_DOMAIN_ANCHOR_CHARS: Final = 5


def _domain_segment_anchors_on_firm(host: str, firm_token: str) -> bool:
    """Return True when ``firm_token`` aligns to a "natural" boundary in
    ``host``. Walks each segment in two directions:

    1. **Forward (segment startswith firm_token)**: catches the common
       case where the firm token is the prefix of a domain segment —
       ``acme-securities.com`` for firm ``Acme Securities`` (token
       ``"acmesecu"``) → segment ``"acmesecurities"`` startswith the
       token. Also runs on the full host (after ``www.`` strip + non-
       alpha strip) to catch hyphenated domains where the token spans
       segments.

    2. **Reverse (firm_token startswith segment)**: catches the
       truncated-brand-domain case where the firm has a longer formal
       name than its domain — ``acmecap.com`` for firm
       ``Acme Capital`` (token ``"acmecapi"``, segment ``"acmecap"``,
       length 7 < token length 8 so the forward check misses but the
       segment is genuinely the firm's brand). Requires the segment to
       be at least ``_MIN_TRUNCATED_DOMAIN_ANCHOR_CHARS`` characters so
       a 2-3 char segment (``ms.com``, ``llc.io``) doesn't gain
       admission via this path.

    Both directions are *prefix* checks, never substring — so the bad-
    class false positive (firm token appearing in the middle of an
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
        if not normalised:
            continue
        if normalised.startswith(firm_token):
            return True
        # Truncated-brand mitigation: firm_token is longer than the
        # segment but the segment is the segment's full prefix of the
        # token. Only counts when the segment is long enough to
        # plausibly be a brand on its own (>= _MIN_TRUNCATED_DOMAIN_ANCHOR_CHARS).
        if (
            len(normalised) >= _MIN_TRUNCATED_DOMAIN_ANCHOR_CHARS
            and firm_token.startswith(normalised)
        ):
            return True
    return False


def _is_content_page_path(url: str) -> bool:
    """Reject URLs whose path marks the page as content, not a homepage.

    Catches news articles / deal-announcement pages / LEI-registry
    records / press releases / blog posts that pass the title-token
    check (because the page mentions the firm by name) but live at a
    URL whose path makes clear they aren't the firm's homepage.
    """
    path = urlparse(url).path.lower()
    return any(kw in path for kw in _NON_HOMEPAGE_PATH_KEYWORDS)


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
