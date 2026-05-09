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
from app.services.website_resolver import (
    _firm_acronym_token,
    _firm_tokens,
    _firm_word_tokens,
    resolve_website,
)


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
async def test_resolver_aliases_admits_parent_domain_for_short_acronym_brand() -> None:
    """BOFA SECURITIES, INC. regression: legal name produces only one
    weak token (``"bofasecu"``) because "BOFA" is below the per-word
    minimum and "SECURITIES" / "INC" are stop-words. Apollo returns
    ``bankofamerica.com`` (the parent's primary digital surface, where
    BofA Securities's contact emails actually live) but the legal-name
    token can't anchor that host so the validator rejects it.

    Passing ``resolver_aliases=["Bank of America Securities"]`` adds
    the 8-char prefix token ``"bankofam"``, which anchors cleanly on
    ``bankofamerica.com`` and lets the candidate through. This is the
    exact scenario the LLM enricher (``firm_alias_enricher``) addresses.
    """
    candidate = "https://www.bankofamerica.com"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(
            name="BOFA SECURITIES, INC.",
            website_url=candidate,
            domain="bankofamerica.com",
        ),
    )

    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )

    website, source, reason = await resolve_website(
        "BOFA SECURITIES, INC.",
        "283942",
        apollo,
        resolver_aliases=["Bank of America Securities"],
    )

    assert (website, source, reason) == (candidate, "apollo", None)


@respx.mock
async def test_resolver_aliases_without_alias_still_misses_short_acronym_brand() -> None:
    """Negative control: same BOFA setup as the test above but WITHOUT
    ``resolver_aliases``. The legal-name token ``"bofasecu"`` can't
    anchor ``bankofamerica.com`` and there are no stop-word-free words
    of length >= 5 to feed the title-soft-match fallback, so the
    validator rejects the Apollo candidate. Confirms the alias is the
    decisive factor in the previous test, not some other gate."""
    candidate = "https://www.bankofamerica.com"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(
            name="BOFA SECURITIES, INC.",
            website_url=candidate,
            domain="bankofamerica.com",
        ),
    )

    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )

    website, source, reason = await resolve_website(
        "BOFA SECURITIES, INC.",
        "283942",
        apollo,
        # Crucially: no resolver_aliases -> token pool is just ["bofasecu"]
    )

    assert website is None
    assert source is None
    assert reason == "no_valid_candidate"


@respx.mock
async def test_resolver_aliases_merges_with_dba_names() -> None:
    """Both alias sources (FINRA-supplied dba_names + LLM-generated
    resolver_aliases) feed the same token pool. A firm whose dba_names
    is empty but whose resolver_aliases anchors a candidate must admit;
    likewise a firm whose dba_names anchors must admit even when the
    resolver_aliases list is empty.

    This test passes both at once and confirms the union covers a host
    that anchors only on the LLM-supplied alias's token."""
    candidate = "https://www.bankofamerica.com"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(
            name="BOFA SECURITIES, INC.",
            website_url=candidate,
            domain="bankofamerica.com",
        ),
    )

    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )

    website, source, reason = await resolve_website(
        "BOFA SECURITIES, INC.",
        "283942",
        apollo,
        # FINRA gave us a useless DBA (basically the legal name again).
        dba_names=["BofA Securities"],
        # LLM gave us the parent-brand expansion that actually anchors.
        resolver_aliases=["Bank of America Securities"],
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


# ──────────────────── new token generators (B1, B2) ────────────────────────


def test_acronym_token_iaa_for_international_assets_advisory() -> None:
    """``INTERNATIONAL ASSETS ADVISORY, LLC`` → significant words
    ``[international, assets, advisory]`` after dropping ``LLC``. The
    final word ``advisory`` is also dropped as a corporate-suffix
    stop-word, but min-3-significant-word + min-3-acronym-len gates
    are evaluated BEFORE the suffix-word filter on words... actually
    the filter drops it, leaving ``[international, assets]`` — that's
    only 2 significant words. Verify: callers can trust the public
    contract that 2-significant-word firms produce no acronym (avoids
    spurious 2-char tokens)."""
    # Drop ``advisory`` as a stop-word → only 2 significant words →
    # below minimum, returns None. The IAAC case is rescued by word-
    # prefix tokens (``international``, ``advisor``) instead.
    assert _firm_acronym_token("INTERNATIONAL ASSETS ADVISORY, LLC") is None


def test_acronym_token_handles_three_unfiltered_words() -> None:
    """Firms with three+ significant words that aren't corporate-suffix
    stop-words generate an acronym. ``ATLAS STRATEGIC MILITARY`` →
    ``"asm"`` (3 chars, eligible)."""
    assert _firm_acronym_token("ATLAS STRATEGIC MILITARY") == "asm"


def test_acronym_token_none_for_two_significant_words() -> None:
    """``MOSAIC ATS, LLC`` has only 2 significant words after dropping
    ``LLC`` (``mosaic``, ``ats``). Min 3 significant words required
    → returns None."""
    assert _firm_acronym_token("MOSAIC ATS, LLC") is None


def test_acronym_token_none_when_only_stop_words() -> None:
    """Firms whose significant words are entirely corporate suffixes
    produce no acronym."""
    assert _firm_acronym_token("CAPITAL GROUP, LLC") is None


def test_word_tokens_atlas_strategic_advisors() -> None:
    """Per-significant-word tokens drop stop-words (LLC, ADVISORS) and
    short fillers, keeping each remaining word's 8-char prefix."""
    tokens = _firm_word_tokens("ATLAS STRATEGIC ADVISORS, LLC")
    assert "atlas" in tokens
    assert "strategi" in tokens
    # ``advisors`` filtered as a corporate suffix.
    assert "advisors" not in tokens
    # ``LLC`` filtered as a stop-word.
    assert "llc" not in tokens


def test_word_tokens_skips_words_below_min_length() -> None:
    """Words shorter than _MIN_WORD_LEN (5 chars) are skipped to avoid
    over-matching on common 3-4 char fragments. ``ABG SECURITIES`` →
    only ``securities`` is filtered (corporate suffix); ``ABG`` is
    too short to be a token."""
    tokens = _firm_word_tokens("ABG SECURITIES, LLC")
    # ``abg`` (3 chars) is below 5-char minimum → not in tokens.
    assert "abg" not in tokens
    # ``securities`` is dropped as a corporate suffix → not in tokens.
    assert "securiti" not in tokens
    assert tokens == []


def test_firm_tokens_combines_all_three_generators() -> None:
    """``_firm_tokens`` combines (1) full-name 8-char prefix,
    (2) acronym, (3) per-word prefixes — all three for the legal
    name and for each DBA — deduplicated."""
    tokens = _firm_tokens("ATLAS STRATEGIC MILITARY", dba_names=None)
    # Full-name 8-char prefix.
    assert "atlasstr" in tokens
    # Acronym.
    assert "asm" in tokens
    # Per-word tokens (each ≥5 chars after stop-word filter).
    assert "atlas" in tokens
    assert "strategi" in tokens
    assert "military" in tokens


def test_firm_tokens_includes_dba_acronym_and_words() -> None:
    """DBAs go through all three generators too, so a firm with a
    multi-word DBA contributes its acronym + word tokens to the
    anchor-candidate pool."""
    tokens = _firm_tokens(
        "303 ALTERNATIVES, LLC",
        dba_names=["303Capital Markets, LLC"],
    )
    # Legal-name full prefix.
    assert "alternat" in tokens
    # DBA full prefix (alpha-stripped: ``capitalmarketsllc``[:8]).
    assert "capitalm" in tokens


# ──────────────────── high-confidence (knowledge_graph) bypass ─────────────


@respx.mock
async def test_knowledge_graph_high_confidence_bypasses_domain_anchor() -> None:
    """A SerpResult with ``is_high_confidence=True`` (sourced from
    Google's knowledge_graph) admits even when the candidate URL's
    domain doesn't anchor on any firm token. Concrete repro:
    ``INTERNATIONAL ASSETS ADVISORY, LLC`` legal-name token is
    ``"internat"``; their actual brand domain ``iaac.com`` shares
    zero overlap. Pre-fix, this firm fell through to no_valid_candidate
    even though Google's knowledge graph correctly identified the site."""
    firm = "INTERNATIONAL ASSETS ADVISORY, LLC"
    candidate = "https://iaac.com/"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=[
            SerpResult(
                url=candidate,
                domain="iaac.com",
                title="International Assets Advisory, LLC",
                is_high_confidence=True,
            ),
        ],
    )
    respx.head(candidate).mock(
        return_value=httpx.Response(
            200, request=httpx.Request("HEAD", candidate),
        ),
    )

    website, source, reason = await resolve_website(
        firm, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (candidate, "serpapi", None)


@respx.mock
async def test_high_confidence_still_rejected_by_blocklist() -> None:
    """High-confidence candidates still go through pre-domain gates —
    a blocklisted host returns False even when the candidate is flagged
    as Google-entity-resolved. Defense in depth: if Google ever returns
    a knowledge graph for an aggregator (unlikely but possible), the
    blocklist still wins."""
    firm = "FAKE FIRM"
    candidate = "https://www.linkedin.com/company/fake-firm"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=[
            SerpResult(
                url=candidate,
                domain="www.linkedin.com",
                title="Fake Firm",
                is_high_confidence=True,
            ),
        ],
    )

    website, source, reason = await resolve_website(
        firm, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")


# ──────────────────── title-soft-match secondary admit (C1) ────────────────


@respx.mock
async def test_title_soft_match_admits_homepage_with_matching_title() -> None:
    """When domain-anchor misses, a homepage-like URL whose ``<title>``
    contains a significant firm word admits via the title-soft-match
    fallback. Concrete: firm ``CONTOSO BROKERAGE`` (token ``"contosob"``)
    on domain ``contoso.example.test`` — segment ``contoso`` doesn't
    forward-anchor (length 7 < token 8) and 8-char token reverse-anchor
    requires the segment to be ≥5 chars (it is, but the prefix check
    fails since ``"contosob"`` doesn't start with ``"contoso"``... it
    does! So this would domain-anchor.) Use a clearly non-anchoring
    domain instead."""
    firm = "CONTOSO BROKERAGE LLC"
    # Domain that genuinely doesn't share any prefix with the firm
    # tokens — title-soft-match is the only admit path.
    candidate = "https://example.test/"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(candidate),
    )
    respx.head(candidate).mock(
        return_value=httpx.Response(
            200, request=httpx.Request("HEAD", candidate),
        ),
    )
    respx.get(candidate).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Contoso Brokerage — Home</title></head></html>",
        ),
    )

    website, source, reason = await resolve_website(
        firm, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (candidate, "serpapi", None)


@respx.mock
async def test_title_soft_match_rejected_when_path_not_homepage() -> None:
    """A page whose title matches the firm name but whose URL path
    isn't homepage-like (``/firm/...``, ``/news/...``, etc.) does NOT
    admit via title-soft-match. Guards the historical false-positive
    cohort that originally caused title-only admit to be removed."""
    firm = "CONTOSO BROKERAGE LLC"
    # /firm/ path is on the blocklist of non-homepage paths (D2).
    candidate = "https://example.test/firm/contoso-brokerage-12345"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(candidate),
    )

    website, source, reason = await resolve_website(
        firm, None, apollo, serpapi=serpapi,
    )

    # /firm/ path-keyword guard rejects pre-HEAD; never even reaches
    # the title check.
    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_title_soft_match_rejected_when_title_doesnt_match() -> None:
    """Homepage path + title that does NOT contain a significant firm
    word → reject. The title check is the load-bearing guard against
    admitting random homepage URLs that happen to be unrelated firms."""
    firm = "CONTOSO BROKERAGE LLC"
    candidate = "https://example.test/"
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(candidate),
    )
    respx.head(candidate).mock(
        return_value=httpx.Response(
            200, request=httpx.Request("HEAD", candidate),
        ),
    )
    respx.get(candidate).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Some Other Random Site</title></head></html>",
        ),
    )

    website, source, reason = await resolve_website(
        firm, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")


# ──────────────────── extended blocklist (D1) ──────────────────────────────


@respx.mock
async def test_zoominfo_in_blocklist() -> None:
    """ZoomInfo profile pages frequently rank high for small broker-
    dealers and pass title-token checks. Blocklist suffix-match
    rejects every ``zoominfo.com`` subdomain pre-HEAD."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            "https://www.zoominfo.com/c/acme-securities/12345",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_crunchbase_in_blocklist() -> None:
    """Same shape as ZoomInfo: company directory rejected by suffix-match."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            "https://www.crunchbase.com/organization/acme-securities",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, serpapi=serpapi,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")


# ──────────────────── HEAD 403/405 → GET fallback (E2) ─────────────────────


@respx.mock
async def test_head_403_falls_back_to_get() -> None:
    """Some Cloudflare-protected sites 403 HEAD even with the browser
    UA but allow GET. The validator retries with GET; if GET returns
    200 and the domain anchors, the candidate admits."""
    apollo = AsyncMock()
    candidate = "https://acme-securities.example.test/"
    apollo.search_organization = AsyncMock(
        return_value=ApolloOrganization(
            name=_FIRM_NAME,
            website_url=candidate,
            domain="acme-securities.example.test",
        ),
    )
    respx.head(candidate).mock(
        return_value=httpx.Response(
            403, request=httpx.Request("HEAD", candidate),
        ),
    )
    respx.get(candidate).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Acme</title></head></html>",
            request=httpx.Request("GET", candidate),
        ),
    )

    website, source, reason = await resolve_website(_FIRM_NAME, None, apollo)

    assert (website, source, reason) == (candidate, "apollo", None)
