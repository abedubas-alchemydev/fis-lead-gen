"""Resolver-chain tests for ``app.services.website_resolver``.

Locks the chain order (Apollo first, Hunter second), the validation
gates (HEAD reachability, blocklist, title-token), and the provider-
error vs. clean-miss reason strings the endpoint relies on. Apollo +
Hunter clients are stubbed with ``AsyncMock``; HEAD/GET to candidate
URLs go through respx so the validator's behavior is also covered.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services.apollo import ApolloError, ApolloOrganization
from app.services.hunter import HunterCompany, HunterError
from app.services.serpapi import SerpAPIError, SerpResult
from app.services.website_resolver import resolve_website


_FIRM_NAME = "Acme Securities LLC"
_CANDIDATE_URL = "https://acme-securities.example.test"
_CANDIDATE_DOMAIN = "acme-securities.example.test"
_HUNTER_DOMAIN = "acme-from-hunter.example.test"
_SERPAPI_URL = "https://acme-from-serp.example.test"
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


def _hunter_company(
    *,
    domain: str = _HUNTER_DOMAIN,
) -> HunterCompany:
    return HunterCompany(domain=domain, name=_FIRM_NAME)


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
async def test_apollo_wins_first_hunter_not_called() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=_apollo_org())
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=_hunter_company())
    _mock_validate_pass(_CANDIDATE_URL)

    website, source, reason = await resolve_website(
        _FIRM_NAME, "1234", apollo, hunter,
    )

    assert (website, source, reason) == (_CANDIDATE_URL, "apollo", None)
    apollo.search_organization.assert_awaited_once_with(_FIRM_NAME, "1234")
    hunter.find_company.assert_not_awaited()


@respx.mock
async def test_apollo_errors_hunter_wins() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        side_effect=ApolloError("apollo dead"),
    )
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=_hunter_company())

    hunter_url = f"https://{_HUNTER_DOMAIN}"
    _mock_validate_pass(hunter_url)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter,
    )

    assert source == "hunter"
    assert website == hunter_url
    assert reason is None


# ─────────────────────────── miss vs. provider-error ─────────────────────


@respx.mock
async def test_no_valid_candidate_when_chain_returns_none() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter,
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
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(
        side_effect=HunterError("hunter 500 retries exhausted"),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter,
    )

    assert website is None
    assert source is None
    assert reason is not None and reason.startswith("all_providers_errored")
    assert "apollo" in reason and "hunter" in reason


# ─────────────────────────── validation gates ────────────────────────────


@respx.mock
async def test_head_non_200_rejects_candidate() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=_apollo_org())
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)

    respx.head(_CANDIDATE_URL).mock(
        return_value=httpx.Response(
            404, request=httpx.Request("HEAD", _CANDIDATE_URL)
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter,
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
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter,
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
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)

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
        _FIRM_NAME, None, apollo, hunter,
    )

    assert website is None
    assert reason == "no_valid_candidate"


@respx.mock
async def test_no_title_passes_when_head_and_blocklist_clear() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=_apollo_org())
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)

    respx.head(_CANDIDATE_URL).mock(
        return_value=httpx.Response(
            200, request=httpx.Request("HEAD", _CANDIDATE_URL)
        ),
    )
    respx.get(_CANDIDATE_URL).mock(
        return_value=httpx.Response(200, text="<html><body>no title here</body></html>"),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter,
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
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)

    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )
    respx.get(candidate).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Welcome to our firm</title></head></html>",
        ),
    )

    website, source, reason = await resolve_website(firm, None, apollo, hunter)
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
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)

    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )
    respx.get(candidate).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Welcome</title></head></html>",
        ),
    )

    website, source, reason = await resolve_website(firm, None, apollo, hunter)
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
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)

    respx.head(candidate).mock(
        return_value=httpx.Response(200, request=httpx.Request("HEAD", candidate)),
    )
    respx.get(candidate).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Some Other Site</title></head></html>",
        ),
    )

    website, source, reason = await resolve_website(firm, None, apollo, hunter)
    assert website is None
    assert reason == "no_valid_candidate"


# ─────────────────────────── hunter is None ────────────────────────────


@respx.mock
async def test_hunter_none_falls_through_to_clean_miss() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, None,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")


@respx.mock
async def test_hunter_none_apollo_errored_returns_provider_error() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        side_effect=ApolloError("apollo 503"),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, None,
    )

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
async def test_apollo_none_hunter_none_serpapi_valid_wins() -> None:
    """Apollo + Hunter both produce no candidate; SerpAPI returns one
    that passes _validate() — chain returns ('<url>', 'serpapi', None)."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(_SERPAPI_URL),
    )
    _mock_validate_pass(_SERPAPI_URL)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter, serpapi,
    )

    assert (website, source, reason) == (_SERPAPI_URL, "serpapi", None)
    serpapi.search_firm.assert_awaited_once_with(_FIRM_NAME)


@respx.mock
async def test_apollo_none_hunter_none_serpapi_all_blocklist_clean_miss() -> None:
    """Every SerpAPI hit is on the domain blocklist — chain falls
    through to ``no_valid_candidate`` (clean miss, NOT provider error)."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=None)
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            _SERPAPI_BLOCKLISTED_URL,
            "https://www.facebook.com/acme-securities",
            "https://twitter.com/acme",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter, serpapi,
    )

    assert website is None
    assert source is None
    assert reason == "no_valid_candidate"


@respx.mock
async def test_all_three_providers_errored_returns_provider_error() -> None:
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(
        side_effect=ApolloError("apollo 503 retries exhausted"),
    )
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(
        side_effect=HunterError("hunter 500 retries exhausted"),
    )
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        side_effect=SerpAPIError("SerpAPI returned 500"),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter, serpapi,
    )

    assert website is None
    assert source is None
    assert reason is not None and reason.startswith("all_providers_errored")
    assert "apollo" in reason
    assert "hunter" in reason
    assert "serpapi" in reason


@respx.mock
async def test_apollo_wins_serpapi_not_called() -> None:
    """When Apollo's first candidate validates, the chain must not waste
    SerpAPI quota — search_firm is never awaited."""
    apollo = AsyncMock()
    apollo.search_organization = AsyncMock(return_value=_apollo_org())
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=_hunter_company())
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(return_value=_serp_results(_SERPAPI_URL))
    _mock_validate_pass(_CANDIDATE_URL)

    website, source, reason = await resolve_website(
        _FIRM_NAME, "1234", apollo, hunter, serpapi,
    )

    assert (website, source, reason) == (_CANDIDATE_URL, "apollo", None)
    hunter.find_company.assert_not_awaited()
    serpapi.search_firm.assert_not_awaited()


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
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)
    serpapi = AsyncMock()
    serpapi.search_firm = AsyncMock(
        return_value=_serp_results(
            "https://files.brokercheck.finra.org/firm/firm_32119.pdf",
        ),
    )

    website, source, reason = await resolve_website(
        _FIRM_NAME, "32119", apollo, hunter, serpapi,
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
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter,
    )

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
    hunter = AsyncMock()
    hunter.find_company = AsyncMock(return_value=None)

    website, source, reason = await resolve_website(
        _FIRM_NAME, None, apollo, hunter,
    )

    assert (website, source, reason) == (None, None, "no_valid_candidate")
