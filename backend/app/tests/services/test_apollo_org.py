"""Apollo organizations-search client tests.

All HTTP via respx — no real Apollo calls. The tests lock the field
contract at the parser boundary: ``ApolloOrganization`` exposes
``name`` + ``website_url`` + ``domain`` and nothing else, even when
the upstream response includes phone, address, employee_count,
funding history, technologies, or any other Apollo enrichment.
That trim mirrors the names-only contract enforced for
``search_executives`` and keeps the org-search path free of
PII / commercially-sensitive fields we don't have a use for.
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest
import respx

from app.services.apollo import (
    ApolloClient,
    ApolloError,
    ApolloOrganization,
)


_APOLLO_ORGS_URL = "https://api.apollo.io/api/v1/organizations/search"


def _organization(
    *,
    name: str = "Acme Securities LLC",
    website_url: str | None = "https://acme-securities.example.com",
    primary_domain: str | None = "acme-securities.example.com",
) -> dict[str, object]:
    """Synthesize an Apollo organization payload that includes the rich
    enrichment fields the client must NOT propagate. Used to lock the
    website-only contract."""
    return {
        "name": name,
        "website_url": website_url,
        "primary_domain": primary_domain,
        # Fields below MUST NOT appear on ApolloOrganization.
        "phone": "+1-555-0100",
        "publicly_traded_symbol": None,
        "employee_count": 42,
        "founded_year": 2010,
        "industries": ["financial services"],
        "technologies": ["okta", "salesforce"],
        "primary_phone": {"sanitized_number": "+1-555-0100"},
        "raw_address": "1 Wall Street, New York, NY 10005, USA",
        "linkedin_url": "https://linkedin.com/company/acme-securities",
    }


@pytest.fixture
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real exponential backoff sleep so retry tests stay fast."""

    async def _no_sleep(_attempt: int) -> None:
        return None

    monkeypatch.setattr(ApolloClient, "_backoff", staticmethod(_no_sleep))


@respx.mock
async def test_happy_path_returns_organization() -> None:
    respx.post(_APOLLO_ORGS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"organizations": [_organization()]},
        )
    )

    client = ApolloClient(api_key="test-key")
    org = await client.search_organization("Acme Securities LLC", "123456")

    assert isinstance(org, ApolloOrganization)
    assert org.name == "Acme Securities LLC"
    assert org.website_url == "https://acme-securities.example.com"
    assert org.domain == "acme-securities.example.com"


@respx.mock
async def test_response_trimmed_to_website_only() -> None:
    """Even when Apollo ships phone/address/employee_count/technologies in
    the response, the client must surface only name + website_url + domain.
    ``ApolloOrganization`` has exactly three fields and no others."""
    respx.post(_APOLLO_ORGS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"organizations": [_organization()]},
        )
    )

    client = ApolloClient(api_key="test-key")
    org = await client.search_organization("Acme")
    assert org is not None

    field_names = {f.name for f in dataclasses.fields(org)}
    assert field_names == {"name", "website_url", "domain"}, (
        "ApolloOrganization must stay website-only — phone/address/"
        "employee_count/technologies must not leak through this module."
    )


@respx.mock
async def test_no_match_returns_none() -> None:
    """Empty organizations list is the normal "we don't know this firm"
    path — return None so the caller leaves website NULL."""
    respx.post(_APOLLO_ORGS_URL).mock(
        return_value=httpx.Response(200, json={"organizations": []}),
    )

    client = ApolloClient(api_key="test-key")
    org = await client.search_organization("Unknown Firm LLC")

    assert org is None


@respx.mock
async def test_429_then_success_retries(fast_backoff: None) -> None:
    """A transient 429 is retried up to ``max_attempts`` times; the next
    successful response wins. Without this, a single rate-limit pulse
    leaves the firm's website NULL despite Apollo knowing the answer."""
    route = respx.post(_APOLLO_ORGS_URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"organizations": [_organization()]}),
        ]
    )

    client = ApolloClient(api_key="test-key", max_attempts=3)
    org = await client.search_organization("Acme")

    assert org is not None
    assert org.website_url == "https://acme-securities.example.com"
    assert route.call_count == 2


@respx.mock
async def test_5xx_persistent_raises_apollo_error(fast_backoff: None) -> None:
    """5xx after retries -> ApolloError so the caller can mark the firm
    as a provider-error review item rather than caching the empty result."""
    respx.post(_APOLLO_ORGS_URL).mock(return_value=httpx.Response(503))

    client = ApolloClient(api_key="test-key", max_attempts=2)
    with pytest.raises(ApolloError):
        await client.search_organization("Acme")


@respx.mock
async def test_4xx_non_retryable_raises_immediately(fast_backoff: None) -> None:
    """A 401/403 isn't a transient — fail fast so the caller sees the
    auth/permission problem instead of waiting through the retry budget."""
    route = respx.post(_APOLLO_ORGS_URL).mock(return_value=httpx.Response(401))

    client = ApolloClient(api_key="bad-key", max_attempts=3)
    with pytest.raises(ApolloError):
        await client.search_organization("Acme")
    assert route.call_count == 1
