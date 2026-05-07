"""Resolver-chain tests for ``app.services.website_resolver``.

Locks the chain order (Apollo → serper.dev → SerpAPI), the validation
gates (HEAD reachability, blocklist, title-token), and the provider-
error vs. clean-miss reason strings the endpoint relies on. Apollo /
serper.dev / SerpAPI clients are stubbed with ``AsyncMock``; HEAD/GET
to candidate URLs go through respx so the validator's behavior is also
covered.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services.apollo import ApolloError, ApolloOrganization
from app.services.serpapi import SerpAPIError, SerpResult
from app.services.serper import SerperError
from app.services.website_resolver import resolve_website


_FIRM_NAME = "Acme Securities LLC"
_CANDIDATE_URL = "https://acme-securities.example.test"
_CANDIDATE_DOMAIN = "acme-securities.example.test"
# Domain-anchored SerpAPI URL — the firm token "acmesecu" is a prefix
# of the segment "acmesecurities-online" (forward direction match).
# Required because the post-2026-05-07 validator admits only on a
# domain anchor; the previous "acme-from-serp" stub didn't anchor and
# would now always reject.
_SERPAPI_URL = "https://acmesecurities-online.example.test"
_SERPAPI_BLOCKLISTED_URL = "https://www.linkedin.com/company/acme-securities"


def _apollo_org(
    *,
    website_url: str | None = _CANDIDATE_URL,
    domain: str | None = _CANDIDATE_DOMAIN,
) -> ApolloOrganization:
    return ApolloOrganization(
        name=_FIRM_NAME,
        website_url=website_url,
        domain=domain,
    )


def _ok_html(title: str = "Acme Securities — Home") -> str:
    return f"<html><head><title>{title}</title></head><body>hi</body></html>"


def _mock_validate_pass(url: str, html: str | None = None) -> None:
    """Wire respx so HEAD + GET on ``url`` look like a healthy firm site."""
    text = _ok_html() if html is None else html
    respx.head(url).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", url)),
    )
    respx.get(url).mock(return_value=httpx.Response(200, text=text))


# ─────────────────────────── happy-path order ────────────────────────────


@respx.mock
async def test_apollo_wins_first_search_tiers_not_called() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=_apollo_org())
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(return_value=[])
    _mock_validate_pass(_CANDIDATE_URL)

    website, source, reason = await resolve_website(
        _FIRM_NAME, "1234", apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (_CANDIDATE_URL, "apollo", None)
    apollo.search_organization.assert_awaited_once_with(_FIRM_NAME, "1234")
    serpapi.search_firm.assert_not_awaited()


# ─────────────────────────── miss vs. provider-error ─────────────────────


@respx.mock
async def test_no_valid_candidate_when_chain_returns_none() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(return_value=[])

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert website is None
    assert source is None
    assert reason == "no_valid_candidate"


@respx.mock
async def test_all_providers_errored_when_both_raise() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        side_effect=ApolloError("apollo 503 retries exhausted"),
    )
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        side_effect=SerpAPIError("SerpAPI returned 500"),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert website is None
    assert source is None
    assert reason is not None and reason.startswith("all_providers_errored")
    assert "apollo" in reason and "serpapi" in reason


# ─────────────────────────── validation gates ────────────────────────────


@respx.mock
async def test_head_non_200_rejects_candidate() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=_apollo_org())
    respx.head(_CANDIDATE_URL).mock(
        return_value=httpx.Response(
            404, request=httpx.Request("HEAD", _CANDIDATE_URL)
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo,
    )

    assert website is None
    assert reason == "no_valid_candidate"


@respx.mock
async def test_blocklisted_domain_is_rejected_pre_head() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=_apollo_org(
            website_url="https://www.linkedin.com/company/acme-securities",
            domain="linkedin.com",
        )
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo,
    )

    assert website is None
    assert reason == "no_valid_candidate"


@respx.mock
async def test_title_without_firm_token_rejects_candidate() -> None:
    """Title-mismatch + domain-mismatch → reject. Candidate URL's
    domain (``unrelated-firm.example.test``) does not anchor on the
    firm token (``"acmesecu"``), so neither the title check nor the
    domain-match fallback admits."""
    unrelated_url = "https://unrelated-firm.example.test"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(
            name=_FIRM_NAME,
            website_url=unrelated_url,
            domain="unrelated-firm.example.test",
        ),
    )

    respx.head(unrelated_url).mock(
        return_value=httpx.Response(
            200, request=httpx.Request("HEAD", unrelated_url)
        ),
    )
    respx.get(unrelated_url).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Totally Unrelated Site</title></head></html>",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo,
    )

    assert website is None
    assert reason == "no_valid_candidate"


@respx.mock
async def test_anchored_domain_admits_regardless_of_page_content() -> None:
    """Post-2026-05-07 rule: the page's title / body content is no
    longer a signal — admission is granted on domain anchor alone.
    The candidate's hostname (``acme-securities.example.test``)
    contains the segment ``acme-securities`` which startswith the firm
    token ``"acmesecu"``, so HEAD-200 + clean blocklist + clean path
    is enough."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=_apollo_org())

    respx.head(_CANDIDATE_URL).mock(
        return_value=httpx.Response(
            200, request=httpx.Request("HEAD", _CANDIDATE_URL)
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo,
    )

    assert website == _CANDIDATE_URL
    assert source == "apollo"
    assert reason is None


# ─────────────────────────── domain-match fallback ────────────────────────────


@respx.mock
async def test_domain_match_accepts_when_title_unrelated_but_hostname_starts_with_firm_token() -> None:
    """Hyphenated-domain case: the firm-name tokens span hyphenated
    segments of the hostname. Title is generic ("Welcome"). The
    full-host-prefix check (after stripping non-alpha chars) admits."""
    firm = "Acme Securities LLC"  # firm_token = "acmesecu"
    candidate = "https://acme-securities.brand.example.test"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(name=firm, website_url=candidate, domain="acme-securities.brand.example.test"),
    )
    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )
    respx.get(candidate).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Welcome to our firm</title></head></html>",
        ),
    )

    website, source, reason = await resolve_website(firm, None, apollo)
    assert website == candidate
    assert source == "apollo"
    assert reason is None


@respx.mock
async def test_domain_match_accepts_subdomain_when_segment_starts_with_firm_token() -> None:
    """Subdomain case: firm token aligns with a non-leading segment.
    e.g., ``trade.smithcapital.com`` for ``Smith Capital`` (token
    ``"smithcap"``). Per-segment prefix check admits."""
    firm = "Smith Capital"  # firm_token = "smithcap"
    candidate = "https://trade.smithcapital.example.test"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(name=firm, website_url=candidate, domain="trade.smithcapital.example.test"),
    )
    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )
    respx.get(candidate).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Welcome</title></head></html>",
        ),
    )

    website, source, reason = await resolve_website(firm, None, apollo)
    assert website == candidate
    assert source == "apollo"
    assert reason is None


@respx.mock
async def test_domain_match_rejects_substring_in_middle_of_segment() -> None:
    """Anchor safety: firm_token appearing in the middle of an
    unrelated word must NOT match. Concrete: firm ``ABC Securities``
    (token ``"abcsecur"``) on a Google hit at
    ``blackabcsecurities.example.test`` — neither the full-host prefix
    nor any per-segment prefix anchors at ``"abcsecur"``."""
    firm = "ABC Securities"  # firm_token = "abcsecur"
    candidate = "https://blackabcsecurities.example.test"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(name=firm, website_url=candidate, domain="blackabcsecurities.example.test"),
    )
    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )
    respx.get(candidate).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Some Other Site</title></head></html>",
        ),
    )

    website, source, reason = await resolve_website(firm, None, apollo)
    assert website is None
    assert reason == "no_valid_candidate"


# ─────────────────────────── search-tier optional skip ────────────────────


@respx.mock
async def test_search_tiers_unset_falls_through_to_clean_miss() -> None:
    """Apollo misses and neither serper.dev nor SerpAPI keys are
    configured (clients passed as None / omitted). The chain falls
    through to ``no_valid_candidate`` cleanly."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)

    website, source, reason = await resolve_website(_FIRM_NAME, None, apollo)

    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_apollo_errored_only_provider_returns_provider_error() -> None:
    """Apollo errors and the search tiers are unset → the chain has
    one attempted provider and one error, so it returns
    ``all_providers_errored`` instead of swallowing as a clean miss."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        side_effect=ApolloError("apollo 503"),
    )

    website, source, reason = await resolve_website(_FIRM_NAME, None, apollo)

    assert website is None
    assert source is None
    assert reason is not None and reason.startswith("all_providers_errored")
    assert "apollo" in reason


# ─────────────────────────── serpapi tier 3 ──────────────────────────────


def _serp_results(*urls: str) -> list[SerpResult]:
    return [
        SerpResult(url=u, domain=u.split("/")[2], title="Acme Securities — Home")
        for u in urls
    ]


@respx.mock
async def test_apollo_none_serpapi_valid_wins() -> None:
    """Apollo produces no candidate; SerpAPI returns one that passes
    ``_validate()`` — chain returns ``('<url>', 'serpapi', None)``."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(_SERPAPI_URL),
    )
    _mock_validate_pass(_SERPAPI_URL)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (_SERPAPI_URL, "serpapi", None)
    serpapi.search_firm.assert_awaited_once_with(_FIRM_NAME)


@respx.mock
async def test_apollo_none_serpapi_all_blocklist_clean_miss() -> None:
    """Every SerpAPI hit is on the domain blocklist — chain falls
    through to ``no_valid_candidate`` (clean miss, NOT provider error)."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            _SERPAPI_BLOCKLISTED_URL,
            "https://www.facebook.com/acme-securities",
            "https://twitter.com/acme",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert website is None
    assert source is None
    assert reason == "no_valid_candidate"


@respx.mock
async def test_apollo_and_serpapi_errored_returns_provider_error() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        side_effect=ApolloError("apollo 503 retries exhausted"),
    )
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        side_effect=SerpAPIError("SerpAPI returned 500"),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert website is None
    assert source is None
    assert reason is not None and reason.startswith("all_providers_errored")
    assert "apollo" in reason
    assert "serpapi" in reason


@respx.mock
async def test_serpapi_walks_past_top_5_when_earlier_results_rejected() -> None:
    """Locks the post-2026-05-07 walk count: the resolver must walk
    every result the SerpAPI client returns, not just top 5. Pre-fix
    the slice ``results[:5]`` missed firms whose own homepage ranked
    6+ (concrete repro: BANKERS LIFE SECURITIES, INC, where
    bankerslife.com was rank 6 behind 5 rejected hits). The first 5
    results in this test are all blocklisted social/regulator hits;
    the 6th is the firm's real site."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=[
            SerpResult(url="https://www.linkedin.com/company/acme-1", domain="www.linkedin.com", title="LinkedIn"),
            SerpResult(url="https://www.facebook.com/acme-2", domain="www.facebook.com", title="Facebook"),
            SerpResult(url="https://files.brokercheck.finra.org/firm/firm_1.pdf", domain="files.brokercheck.finra.org", title="FINRA PDF"),
            SerpResult(url="https://twitter.com/acme-4", domain="twitter.com", title="Twitter"),
            SerpResult(url="https://www.bloomberg.com/profile/company/acme", domain="www.bloomberg.com", title="Bloomberg"),
            SerpResult(url=_SERPAPI_URL, domain="acmesecurities-online.example.test", title="Acme — Home"),
        ],
    )
    _mock_validate_pass(_SERPAPI_URL)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (_SERPAPI_URL, "serpapi", None)


@respx.mock
async def test_apollo_wins_serpapi_not_called() -> None:
    """When Apollo's first candidate validates, the chain must not waste
    SerpAPI quota — search_firm is never awaited."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=_apollo_org())
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(return_value=_serp_results(_SERPAPI_URL))
    _mock_validate_pass(_CANDIDATE_URL)

    website, source, reason = await resolve_website(
        _FIRM_NAME, "1234", apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (_CANDIDATE_URL, "apollo", None)
    serpapi.search_firm.assert_not_awaited()


# ─────────────────────────── serper.dev tier 2 ─────────────────────────────


_SERPER_URL = "https://acmesecurities-from-serper.example.test"


@respx.mock
async def test_serper_runs_before_serpapi_when_apollo_misses() -> None:
    """Tier order is Apollo → serper.dev → SerpAPI. When Apollo misses
    and serper.dev returns a valid candidate, SerpAPI must NOT fire
    (saves the more expensive quota)."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serper = AsyncMock()
    serper.search_firm = AsyncMock(
        return_value=[
            SerpResult(url=_SERPER_URL, domain="acmesecurities-from-serper.example.test", title="Acme Securities — Home"),
        ],
    )
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(return_value=_serp_results(_SERPAPI_URL))
    _mock_validate_pass(_SERPER_URL)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi, serper=serper,
    )

    assert (website, source, reason) == (_SERPER_URL, "serper", None)
    serpapi.search_firm.assert_not_awaited()


@respx.mock
async def test_serper_errors_falls_through_to_serpapi() -> None:
    """When serper.dev errors (e.g., 429 quota burn), the chain must
    fall through to SerpAPI rather than recording all_providers_errored."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serper = AsyncMock()
    serper.search_firm = AsyncMock(side_effect=SerperError("serper.dev returned 429"))
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(return_value=_serp_results(_SERPAPI_URL))
    _mock_validate_pass(_SERPAPI_URL)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi, serper=serper,
    )

    assert (website, source, reason) == (_SERPAPI_URL, "serpapi", None)


@respx.mock
async def test_serper_none_falls_through_to_serpapi() -> None:
    """When serper.dev key is unset (client passed as None), the chain
    skips that tier silently and uses SerpAPI as before. This is the
    no-config-change path so existing deployments without
    SERPER_API_KEY keep working."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(return_value=_serp_results(_SERPAPI_URL))
    _mock_validate_pass(_SERPAPI_URL)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi, serper=None,
    )

    assert (website, source, reason) == (_SERPAPI_URL, "serpapi", None)


# ─────────────────────── subdomain + file-download rejects ──────────────


@respx.mock
async def test_serpapi_brokercheck_pdf_subdomain_rejected() -> None:
    """Real-world Abacus regression: SerpAPI ranks the firm's BrokerCheck
    Detailed Report PDF (``files.brokercheck.finra.org/firm/firm_<crd>.pdf``)
    above every other result for obscure firms. Both the subdomain
    (``finra.org`` suffix) and the ``.pdf`` path should reject it; the
    chain falls through to a clean miss instead of stamping the FINRA
    CDN as the firm's website. Pre-fix, the exact-match blocklist let
    this through and the FE rendered ``files.brokercheck.finra.org`` as
    the firm's domain."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            "https://files.brokercheck.finra.org/firm/firm_32119.pdf",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, "32119", apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_finra_subdomain_rejected_via_suffix_match() -> None:
    """Any subdomain of ``finra.org`` is rejected because the blocklist
    suffix-matches. Pre-fix only the exact entries on the list were
    rejected, which let any new BrokerCheck/FINRA subdomain in."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=_apollo_org(
            website_url="https://reports.brokercheck.finra.org/firm/12345",
            domain="reports.brokercheck.finra.org",
        ),
    )
    website, source, reason = await resolve_website(_FIRM_NAME, None, apollo)

    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_pdf_path_rejected_on_otherwise_clean_domain() -> None:
    """A candidate hosted on a non-blocked domain is still rejected when
    the path looks like a file download. Stops the chain from stamping a
    hosted-PDF/whitepaper URL as the firm's homepage."""
    apollo = AsyncMock()
    pdf_url = "https://example-cdn.test/files/whitepaper.pdf"
    apollo.search_organization = AsyncMock(
        return_value=_apollo_org(website_url=pdf_url, domain="example-cdn.test"),
    )
    website, source, reason = await resolve_website(_FIRM_NAME, None, apollo)

    assert (website, source, reason) == (None, None, "no_valid_candidate")


# ─────────────────────── content-page path rejects ──────────────────────


@respx.mock
async def test_transactions_announcement_path_rejected() -> None:
    """Real-world 3WIRE ADVISORY regression: SerpAPI ranked the
    Houlihan Lokey transaction-announcement page above the firm's own
    site. The page's ``<title>`` mentioned "3Wire Advisory", so the
    title-token check admitted it and the wrong URL got stamped on the
    BD row. Path-keyword guard rejects ``/transactions/`` and friends
    pre-HEAD."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            "https://hl.com/about-us/transactions/parry-labs-capitol-meridian-partners/",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_news_article_path_rejected() -> None:
    """Real-world 777 SECURITIES regression: SerpAPI ranked a CFO.com
    news article (``/news/leaders-of-miami-investment-firm-...``) above
    the firm's own site. The article mentioned the firm by name, so the
    title-token check admitted it. Path-keyword guard rejects
    ``/news/`` pre-HEAD."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            "https://www.cfo.com/news/leaders-of-miami-investment-firm-face-securities-charges/803863/",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_lei_lookup_record_path_rejected() -> None:
    """Real-world 4170 SECURITIES regression: SerpAPI ranked the firm's
    LEI registry record (``lei-lookup.com/record/...``) as the top
    organic hit for an obscure broker-dealer. Path-keyword guard
    rejects ``/record/`` pre-HEAD so the LEI lookup page can't be
    mistaken for the firm's homepage."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            "https://www.lei-lookup.com/record/254900AX56TV6OE5G885/",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_directory_listing_with_firm_in_title_rejected() -> None:
    """Real-world ACA/PRUDENT INVESTORS regression: SerpAPI ranked a
    third-party advisor directory listing
    (``retirementplanning.net/.../firm-slug/<id>``) above the firm's
    own site. The page's title contained the firm name verbatim, so
    the pre-2026-05-07 title-token check would have admitted, but the
    domain ``retirementplanning.net`` doesn't anchor on the firm token
    (``"acaprude"`` for ACA/PRUDENT). With the title-only admit gone,
    the directory listing is structurally rejected — the page can
    mention the firm by name all it wants, the domain has to anchor.
    Path-keyword guard doesn't help here because ``/retirement-
    planners/`` is a generic directory category, not a content-page
    marker we can blocklist without whack-a-mole."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            "https://www.retirementplanning.net/retirement-planners/new-jersey/green-brook/acaprudent-investors/2001301",
        ),
    )

    website, source, reason = await resolve_website(
        "ACA/Prudent Investors Planning Corporation",
        None,
        apollo,
        serpapi=serpapi,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_truncated_brand_domain_admits() -> None:
    """Truncated-brand mitigation: firm ``Acme Capital`` (token
    ``"acmecapi"`` — 8 chars) has its homepage at
    ``acmecap.example.test`` (segment ``"acmecap"`` — 7 chars). The
    forward direction (segment startswith firm_token) misses because
    ``"acmecap"`` is shorter than the token, but the reverse direction
    (firm_token startswith segment) admits since ``"acmecap"`` is
    >= 5 chars and a prefix of the token. Without this mitigation,
    every firm whose domain abbreviates the formal name would miss."""
    firm = "Acme Capital"  # token = "acmecapi"
    candidate = "https://acmecap.example.test"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(
            name=firm, website_url=candidate, domain="acmecap.example.test",
        ),
    )

    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )

    website, source, reason = await resolve_website(firm, None, apollo)
    assert website == candidate
    assert source == "apollo"
    assert reason is None


@respx.mock
async def test_dba_anchor_admits_when_legal_name_misses() -> None:
    """Real-world 303 ALTERNATIVES, LLC regression: firm registered as
    ``303 Alternatives, LLC`` (legal-name token ``"alternat"``) operates
    as ``303Capital Markets`` and the brand domain is
    ``303capitalmarkets.com``. The legal-name token shares zero overlap
    with the brand domain — without DBA awareness, the firm misses
    every time. Passing ``dba_names=["303Capital Markets, LLC"]`` adds
    the DBA token to the anchor candidate set; the segment
    ``303capitalmarkets`` startswith the DBA token ``"capitalm"`` and
    admits."""
    candidate = "https://www.303capitalmarkets.com"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(
            name="303 ALTERNATIVES, LLC",
            website_url=candidate,
            domain="303capitalmarkets.com",
        ),
    )

    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )

    website, source, reason = await resolve_website(
        "303 ALTERNATIVES, LLC",
        None,
        apollo,
        dba_names=["303Capital Markets, LLC"],
    )

    assert (website, source, reason) == (candidate, "apollo", None)


@respx.mock
async def test_dba_names_none_preserves_legacy_legal_name_path() -> None:
    """Sanity: callers that don't yet pass ``dba_names`` (or pass None)
    still get the legal-name-only behavior. No regression on firms whose
    domain anchors on the legal name."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=_apollo_org())

    respx.head(_CANDIDATE_URL).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", _CANDIDATE_URL)),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, dba_names=None,
    )

    assert (website, source, reason) == (_CANDIDATE_URL, "apollo", None)


@respx.mock
async def test_short_segment_does_not_admit_via_truncated_anchor() -> None:
    """Truncated-brand mitigation is gated on a minimum segment length
    so a 2-3 char segment doesn't gain admission. Firm
    ``Big Investment Group`` (token ``"biginves"``) on ``big.example.test``:
    segment ``"big"`` is 3 chars, below the 5-char threshold, so the
    reverse direction must NOT admit even though ``"biginves"``
    startswith ``"big"``. (Forward direction also doesn't admit since
    ``"big"`` is shorter than the token.)"""
    firm = "Big Investment Group"  # token = "biginves"
    candidate = "https://big.example.test"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(
            name=firm, website_url=candidate, domain="big.example.test",
        ),
    )

    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )

    website, source, reason = await resolve_website(firm, None, apollo)
    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_press_release_path_rejected() -> None:
    """Press-release pages mention the firm by name, so they pass the
    title-token check, but they aren't the firm's homepage. The path-
    keyword guard rejects ``/press-release`` (and ``/press-releases``)
    pre-HEAD."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            "https://example.test/press-release/acme-securities-launches-new-product/",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")
