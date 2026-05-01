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
from app.services.website_resolver import resolve_website


_FIRM_NAME = "Acme Securities LLC"
_CANDIDATE_URL = "https://acme-securities.example.test"
_CANDIDATE_DOMAIN = "acme-securities.example.test"
_HUNTER_DOMAIN = "acme-from-hunter.example.test"


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
