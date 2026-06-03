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
        # Aggregators / blog hosts that rank above small-firm sites and
        # pass the title-token check because the firm's name appears on
        # the profile page. Each frequently shows up as a SerpAPI top
        # result for small/obscure broker-dealers and is never a firm's
        # primary website.
        "builtin.com",          # tech jobs aggregator (firm profiles)
        "wordpress.com",        # subdomain match: ``*.wordpress.com``
        "zoominfo.com",         # B2B contact/company aggregator
        "crunchbase.com",       # company directory
        "pitchbook.com",        # company directory
        "dnb.com",              # Dun & Bradstreet directory
        "sifma.org",            # SIFMA member directory
        "smartadvisormatch.com",  # advisor aggregator
        "glassdoor.com",        # employer/jobs review aggregator
        "indeed.com",           # jobs aggregator
        "yelp.com",             # local-business directory
        "mapquest.com",         # business directory
        "yellowpages.com",      # business directory
        "bbb.org",              # Better Business Bureau directory
        "opencorporates.com",   # corporate-records directory
        "lei-lookup.com",       # LEI registry lookup
        "cbinsights.com",       # market-intel directory
        "buzzfile.com",         # business directory
        "manta.com",            # business directory
        "leadiq.com",           # B2B contact aggregator
        "advisor.investedbetter.com",  # advisor aggregator
        "getprospect.com",      # B2B contact aggregator
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
    # Aggregator / directory URL shapes — these patterns are how
    # third-party sites slot a firm-named profile under a parent
    # domain. The parent domain may not itself be on the blocklist
    # (e.g., a smaller advisor-network site), but a path of this shape
    # makes clear the URL isn't the firm's homepage. Concrete repros:
    # ``getprospect.com/business-directory/<slug>``,
    # ``advisor.investedbetter.com/firm/<slug>-<id>``,
    # ``flaia.org/companies/<slug>``,
    # ``thegalbreathgroup.com/alden`` (not caught here — needs a
    # different signal — but listed for awareness).
    "/firm/",
    "/firms/",
    "/company/",
    "/companies/",
    "/business/",
    "/business-directory/",
    "/listing/",
    "/listings/",
    "/leadership/",
    "/team/",
    "/people/",
    "/directory/",
)


_VALIDATE_TIMEOUT_S: Final = 5.0
_FIRM_TOKEN_LEN: Final = 8
_NON_ALPHA_RE: Final = re.compile(r"[^a-z]")
_WORD_RE: Final = re.compile(r"[A-Za-z]+")

# Words ignored when generating acronym + per-word tokens. Mirrors
# ``normalization._ENTITY_NOISE_TOKENS`` and adds the BD-specific
# corporate-suffix tokens (``group``, ``associates``, ``advisors``, etc.)
# that would otherwise pollute acronyms — e.g., for ``ATLAS STRATEGIC
# ADVISORS, LLC`` we want acronym ``"as"`` rejected (too short / too
# generic) rather than ``"asa"`` which the suffix word would otherwise
# generate. Kept as a local constant to avoid coupling resolver
# heuristics to the entity-normalization module's noise list.
_ENTITY_NOISE: Final = frozenset({
    "inc", "llc", "lp", "lp.", "corp", "corporation", "company", "co",
    "ltd", "limited", "plc", "the", "of", "and", "a", "an",
    "group", "associates", "partners", "holdings",
    "capital", "securities", "advisors", "advisory",
    "services", "management", "investments", "investment",
    "financial", "trading",
})

# Minimum length for an acronym to be eligible as a domain-anchor
# token. Two-letter acronyms (e.g., ``"AC"`` for ``ANTA CAPITAL``) are
# too generic — they trivially match the start of any segment beginning
# with those letters. Three+ chars meaningfully constrain the anchor.
_MIN_ACRONYM_LEN: Final = 3

# Minimum length for a single significant-word prefix to be eligible
# as a domain-anchor token. ``ATLAS`` is 5 chars → eligible; ``IT`` /
# ``MA`` / common 2-3 char fillers are not.
_MIN_WORD_LEN: Final = 5

# Cap on Apollo organizations/search calls per resolution: the legal name
# plus up to (this − 1) brand aliases. Bounds the extra Apollo traffic the
# alias-query path adds; a firm's combined dba_names + resolver_aliases is
# almost always 1–3 entries, so 4 covers the realistic pool while capping
# pathological alias lists.
_MAX_APOLLO_QUERIES: Final = 4

# Browser User-Agent for the validator's HEAD/GET reachability check.
# Many firm sites sit behind Cloudflare / WAFs that 403 the default
# ``python-httpx/X.X`` UA on the assumption it's a bot. Concrete
# observed: ``austinatlantic.com`` 403'd on python-httpx but 200'd on
# Chrome's UA. Identifying as a real browser is a legitimate
# reachability check — the validator only inspects HTTP status, not
# page content, so there's no "scrape" semantic at play.
_BROWSER_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Maximum body bytes to read when extracting a page <title> for the
# title-soft-match fallback. The tag is essentially always within the
# first few KB; cap to avoid pulling multi-MB pages when validating.
_TITLE_FETCH_BODY_CAP: Final = 65_536

# Compiled extractor for the first ``<title>`` element. Permissive on
# attributes (e.g., ``<title lang="en">``) and case (HTML allows any
# combination). Multiline since real pages wrap the body across lines.
_TITLE_RE: Final = re.compile(
    r"<title[^>]*>([^<]+)</title>", re.IGNORECASE | re.DOTALL
)

# URL paths that count as "homepage-like" for the title-soft-match
# fallback admit gate. Matches ``/``, ``/about``, ``/home``, ``/index``,
# ``/welcome``, etc. — single-segment, alphabetic, ≤16 chars. Anything
# more specific is rejected: a page at ``/firm/123`` or
# ``/news/<slug>`` is by definition not the firm's homepage and so
# title-mention of the firm name doesn't make it the firm's site.
_HOMEPAGE_PATH_RE: Final = re.compile(r"^/?[a-z]{0,16}/?$", re.IGNORECASE)


def _is_homepage_like_path(url: str) -> bool:
    """Return True when ``url``'s path is the homepage or a single
    short alphabetic segment (``/``, ``/about``, ``/home``, etc.).

    Used by the title-soft-match validator path. Critical guard against
    the historical false-positive cohort: news articles, deal-
    announcement pages, LEI registries, and aggregator profile pages
    that mention the firm by name in their ``<title>`` but whose path
    has slugs / ids / multi-segment depth.
    """
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query or parsed.fragment:
        return False
    return bool(_HOMEPAGE_PATH_RE.match(path))


async def _title_matches_firm(
    client: httpx.AsyncClient,
    url: str,
    significant_words: list[str],
) -> bool:
    """GET ``url``, parse the ``<title>`` tag, and return True when it
    contains any significant firm word.

    Body is capped at ``_TITLE_FETCH_BODY_CAP`` bytes — the title tag
    is essentially always within the first few KB and we don't need
    the rest. Uses the same client as the HEAD check so the browser
    UA / redirect / timeout settings are inherited.

    Significant-word match is alpha-stripped + lowercased on both
    sides. Avoids false matches on punctuation but doesn't try fuzzy
    matching — the caller's word list is already filtered to ≥5 chars
    for the same reason (under 5 chars is over-loose).
    """
    if not significant_words:
        return False
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return False
        body = resp.text[:_TITLE_FETCH_BODY_CAP]
    except httpx.HTTPError:
        return False
    match = _TITLE_RE.search(body)
    if not match:
        return False
    title = _NON_ALPHA_RE.sub("", match.group(1).lower())
    if not title:
        return False
    return any(word in title for word in significant_words)


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
    apollo: ApolloClient | None,
    serpapi: SerpAPIClient | None = None,
    serper: SerperClient | None = None,
    dba_names: list[str] | None = None,
    resolver_aliases: list[str] | None = None,
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

    ``dba_names`` is the firm's list of "doing business as" / alternate
    trade names (typically sourced from ``broker_dealers.dba_names``,
    parsed from FINRA's ``firm_other_names``). Each name in the list is
    converted to a firm token; the validator's domain-anchor check
    admits a candidate if **any** of the (legal + DBA) tokens anchor the
    candidate's hostname. Without this, firms registered as
    ``303 ALTERNATIVES, LLC`` but operating at ``303capitalmarkets.com``
    (DBA "303Capital Markets") would always miss because the legal-name
    token doesn't share characters with the brand domain.

    ``resolver_aliases`` is the LLM-generated alias pool sourced from
    ``broker_dealers.resolver_aliases`` (populated by
    ``services/firm_alias_enricher.py``). Treated equivalently to
    ``dba_names`` for token generation -- both feed the same
    ``_firm_tokens`` pool. Kept as a separate parameter so callers can
    distinguish provenance (FINRA-supplied trade names vs. LLM-supplied
    parent/brand expansions) when logging or auditing. Concrete repro
    this admits: ``BOFA SECURITIES, INC.`` operating at
    ``bankofamerica.com`` -- the FINRA dba_names list is empty/sparse,
    but resolver_aliases ``["Bank of America Securities"]`` produces
    token ``"bankofam"`` which anchors the parent-company hostname.

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

    Apollo (Tier 1) is queried with the legal name **and** each alias from
    ``dba_names`` / ``resolver_aliases`` (deduped, capped at
    ``_MAX_APOLLO_QUERIES``). Apollo's structured org→domain index resolves
    parent/brand names a legal entity name doesn't match — e.g. ``AQR
    INVESTMENTS, LLC`` returns zero Apollo orgs, but the alias ``AQR Capital
    Management`` resolves to ``aqr.com``. The search tiers stay on the legal
    name only: Google doesn't surface those parent sites even for the alias
    query, and SerpAPI is metered.
    """
    # Merge FINRA-supplied DBAs and LLM-supplied aliases into the same
    # token pool. Provenance is preserved at the column level on the BD
    # row; the resolver itself treats both as equivalent token sources
    # because the validator's domain-anchor check only cares about the
    # final token set.
    combined_aliases = [*(dba_names or []), *(resolver_aliases or [])]
    firm_tokens = _firm_tokens(firm_name, combined_aliases)
    # Significant-word list used by the validator's title-soft-match
    # fallback path: any word from the legal name (or any DBA / alias)
    # of length ≥ ``_MIN_WORD_LEN``, dropping corporate-suffix stop-words.
    # Empty for very short / suffix-heavy firm names — those firms simply
    # can't get the title-match safety net.
    significant_words: list[str] = []
    for name in (firm_name, *combined_aliases):
        if not name:
            continue
        for word in _significant_words(name):
            if len(word) >= _MIN_WORD_LEN and word not in significant_words:
                significant_words.append(word)
    errors: list[str] = []
    providers_attempted = 0

    # Tier 1 — Apollo organizations/search (skipped when ``apollo`` is None
    # — bulk backfills can opt out via ``--no-apollo`` to run SerpAPI-only).
    #
    # Query the legal name first, then each known alias (FINRA DBA names +
    # LLM brand aliases). Apollo's structured org→domain index resolves the
    # parent/brand names a legal entity name doesn't match at all — e.g. the
    # legal entity "AQR INVESTMENTS, LLC" returns zero Apollo orgs, but its
    # alias "AQR Capital Management" resolves straight to aqr.com. The aliases
    # were already used to *validate* a candidate (firm_tokens); here we also
    # use them to *query*. Restricted to Apollo on purpose: the SerpAPI/serper
    # search tiers stay on the legal name because Google doesn't surface these
    # parent sites even for the alias query (observed for AQR) and SerpAPI is
    # metered — so we don't multiply paid search calls. The validator still
    # gates every candidate, so an alias only admits a domain its own token
    # anchors.
    if apollo is not None:
        providers_attempted += 1
        # Legal name carries the CRD keyword for disambiguation; brand aliases
        # are queried bare — a "CRD <n>" keyword would filter out the parent-
        # brand org, which isn't registered under this firm's CRD. Deduped
        # case-insensitively and capped at _MAX_APOLLO_QUERIES.
        apollo_queries: list[tuple[str, str | None]] = [(firm_name, crd)]
        seen_queries = {firm_name.strip().lower()}
        for alias in combined_aliases:
            alias = (alias or "").strip()
            if not alias or alias.lower() in seen_queries:
                continue
            seen_queries.add(alias.lower())
            apollo_queries.append((alias, None))
            if len(apollo_queries) >= _MAX_APOLLO_QUERIES:
                break
        try:
            for query_name, query_crd in apollo_queries:
                org = await apollo.search_organization(query_name, query_crd)
                if org is None:
                    continue
                candidate = _candidate_from_apollo(org)
                if candidate and await _validate(
                    candidate,
                    firm_tokens,
                    significant_words=significant_words,
                ):
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
                if await _validate(
                    result.url,
                    firm_tokens,
                    high_confidence=getattr(
                        result, "is_high_confidence", False
                    ),
                    significant_words=significant_words,
                ):
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
                if await _validate(
                    result.url,
                    firm_tokens,
                    high_confidence=getattr(
                        result, "is_high_confidence", False
                    ),
                    significant_words=significant_words,
                ):
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
    """Normalize a single firm name to an alpha-only token used for the
    domain-anchor check."""
    return _NON_ALPHA_RE.sub("", firm_name.lower())[:_FIRM_TOKEN_LEN]


def _significant_words(firm_name: str) -> list[str]:
    """Return the alphabetic words in ``firm_name`` minus stop-words /
    corporate suffixes. Used by the acronym + per-word token generators."""
    return [
        w.lower()
        for w in _WORD_RE.findall(firm_name)
        if w.lower() not in _ENTITY_NOISE
    ]


def _firm_acronym_token(firm_name: str) -> str | None:
    """Generate a brand-acronym token from the firm's significant words.

    Catches firms operating at brand-domain abbreviations that don't
    share a prefix with the legal name. Concrete repro:
    ``INTERNATIONAL ASSETS ADVISORY, LLC`` → significant words
    ``[international, assets, advisory]`` → acronym ``"iaa"`` → matches
    domain segment ``"iaac"`` of the firm's actual site ``iaac.com``,
    which the legal-name token ``"internat"`` could never anchor on.

    Requires:
    - 3+ significant words (otherwise the acronym derives from too thin
      a signal — a 2-word firm produces a 2-char acronym).
    - Final acronym length >= ``_MIN_ACRONYM_LEN`` to bar trivially
      short matches.
    """
    significant = _significant_words(firm_name)
    if len(significant) < _MIN_ACRONYM_LEN:
        return None
    acronym = "".join(w[0] for w in significant)
    return acronym if len(acronym) >= _MIN_ACRONYM_LEN else None


def _firm_word_tokens(firm_name: str) -> list[str]:
    """Generate per-significant-word tokens (each truncated to 8 chars).

    Catches firms whose brand domain matches a single word from the
    firm name even when the legal-name 8-char prefix doesn't anchor.
    Concrete repro: ``ATLAS STRATEGIC ADVISORS, LLC`` → words
    ``[atlas, strategic, advisors]`` (advisors filtered out as a
    corporate suffix) → tokens ``["atlas", "strategi"]`` — the
    ``"atlas"`` token anchors against ``atlas.com`` or
    ``atlas-advisors.com``.

    Each word must be ``_MIN_WORD_LEN`` characters or longer to skip
    fillers (``of``, ``and``, ``the``) and 3-4 char strings that would
    over-match.
    """
    return [
        word[:_FIRM_TOKEN_LEN]
        for word in _significant_words(firm_name)
        if len(word) >= _MIN_WORD_LEN
    ]


def _firm_tokens(
    firm_name: str, dba_names: list[str] | None
) -> list[str]:
    """Build the candidate-token set from the firm's legal name and any
    "doing business as" trade names.

    Combines THREE generators per name to widen the domain-anchor net:
    1. The 8-char alpha prefix of the full name (``_firm_token``).
    2. The acronym of the significant words (``_firm_acronym_token``)
       — catches brand-acronym domains like ``iaac.com`` for
       ``INTERNATIONAL ASSETS ADVISORY``.
    3. Per-significant-word prefixes (``_firm_word_tokens``) — catches
       single-word brand domains like ``atlas.com`` for ``ATLAS
       STRATEGIC ADVISORS``.

    Returns a deduplicated list of non-empty tokens. The validator
    admits a candidate if its domain anchors on **any** of these tokens,
    so a firm registered as ``303 ALTERNATIVES, LLC`` (legal-name token
    ``"alternat"``) operating at ``303capitalmarkets.com`` (DBA "303Capital
    Markets" → token ``"capitalm"``) admits via the DBA token even though
    the legal-name token has no overlap with the brand domain.
    """
    candidates = [firm_name, *(dba_names or [])]
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(token: str | None) -> None:
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)

    for name in candidates:
        if not name:
            continue
        _add(_firm_token(name))
        _add(_firm_acronym_token(name))
        for word_token in _firm_word_tokens(name):
            _add(word_token)
    return tokens


async def _validate(
    url: str,
    firm_tokens: list[str],
    *,
    high_confidence: bool = False,
    significant_words: list[str] | None = None,
) -> bool:
    """Reachability + blocklist + (domain-anchor OR title-soft-match) gate.

    Three admit paths, evaluated in order after the pre-domain gates
    (blocklist / path-keyword / HEAD non-2xx) all clear:

    1. **High-confidence bypass** — when ``high_confidence`` is True the
       caller is asserting Google's own entity resolution has identified
       the URL as the firm's site (knowledge-graph or answer-box panel).
       Reachability + blocklist + path-guard still apply, but the
       domain-anchor heuristic is skipped: we trust Google to have done
       the firm-to-site mapping better than our 8-char prefix could.

    2. **Domain anchor** — the regular path. The candidate's final
       (post-redirect) hostname must align to **any** of the firm's
       tokens via ``_domain_segment_anchors_on_firm`` (forward or
       truncated-brand direction). Tokens combine legal name + DBA names
       + acronym + per-significant-word prefixes.

    3. **Title-soft-match fallback** — only runs when (1) is False, (2)
       missed, **and** ``significant_words`` is non-empty. Re-fetches
       the page (GET; HEAD doesn't carry a body), parses the ``<title>``
       tag, and admits if the title contains any significant word ≥5
       chars. The candidate's URL path must also be "homepage-like"
       (root or single short alphabetic segment) — this guards against
       the historical false-positive class where news / transaction /
       LEI-record pages mentioned the firm by name in their title.

    Pre-domain gates that always apply: host blocklist (suffix-match on
    ``DOMAIN_BLOCKLIST``), file-download path (``.pdf``/``.doc``/etc.),
    content-page path (``/news/``, ``/transactions/``, ``/lookup/``,
    ``/firm/``, etc.), HEAD non-2xx (with 403/405 → GET fallback).

    Returns ``False`` on any network error or any gate rejection. Also
    returns ``False`` when neither ``firm_tokens`` nor ``high_confidence``
    nor ``significant_words`` would let the candidate be admitted.
    """
    if not url:
        return False
    # Fast reject before any HTTP I/O when there's no admission path
    # possible: not high-confidence, no domain-anchor tokens, and no
    # significant-word list for the title fallback.
    if not high_confidence and not firm_tokens and not significant_words:
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
            headers={"User-Agent": _BROWSER_USER_AGENT},
        ) as client:
            head = await client.head(url)
            # Some servers reject HEAD (405 Method Not Allowed) or block
            # bot-like HEAD with 403 even when GET goes through. Retry
            # with GET in those cases so we don't reject reachable sites.
            if head.status_code in (403, 405):
                head = await client.get(url)
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
            host_to_check = final_host or domain

            # Path 1: high-confidence (Google entity-resolved). Skip the
            # domain-anchor heuristic — the candidate's source already
            # encoded the firm-to-site mapping.
            if high_confidence:
                return True

            # Path 2: domain anchor on any of the firm tokens.
            if firm_tokens and any(
                _domain_segment_anchors_on_firm(host_to_check, token)
                for token in firm_tokens
            ):
                return True

            # Path 3: title-soft-match. Only when the caller passed a
            # significant-word list AND the URL path is homepage-like.
            # Guards against the news/transactions/LEI false-positive
            # cohort that originally caused title-only admit to be
            # removed. We re-fetch with GET to read the page body —
            # HEAD doesn't carry a title.
            if (
                significant_words
                and _is_homepage_like_path(final_url)
                and await _title_matches_firm(
                    client, final_url, significant_words
                )
            ):
                return True

            return False
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
