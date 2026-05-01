"""Hunter.io company-find client tests.

All HTTP via respx — no real Hunter calls. Locks the contract that
``HunterCompany`` carries only ``domain`` + ``name`` even when the
upstream payload includes industry, employee bands, social handles, and
the rest of Hunter's enrichment fields. Also exercises the retry +
provider-error semantics so the resolver chain can rely on the right
exception class on retries-exhausted vs. clean-miss vs. unknown-firm.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.hunter import HunterClient, HunterCompany, HunterError


_HUNTER_URL = "https://api.hunter.io/v2/companies/find"


def _company_payload(
    *,
    domain: str = "acme-securities.example.com",
    name: str = "Acme Securities LLC",
) -> dict[str, object]:
    """Synthesize a Hunter response that includes enrichment fields the
    client must NOT propagate. Used to lock the trim at the parser
    boundary."""
    return {
        "data": {
            "name": name,
            "domain": domain,
            # Fields below MUST NOT appear on HunterCompany.
            "industry": "Financial Services",
            "employees_count": 42,
            "headcount": "11-50",
            "linkedin": "https://linkedin.com/company/acme-securities",
            "twitter": "@acme",
            "phone": "+1-555-0100",
            "founded_year": 2010,
            "country": "US",
        },
    }


@pytest.fixture
def hunter() -> HunterClient:
    return HunterClient("test-key", max_attempts=3, timeout_s=1.0)


def test_hunter_client_requires_api_key() -> None:
    with pytest.raises(ValueError):
        HunterClient("")


@respx.mock
async def test_find_company_happy_path_trims_to_domain_and_name(
    hunter: HunterClient,
) -> None:
    respx.get(_HUNTER_URL).mock(
        return_value=httpx.Response(200, json=_company_payload()),
    )

    result = await hunter.find_company("Acme Securities LLC")

    assert isinstance(result, HunterCompany)
    assert result.domain == "acme-securities.example.com"
    assert result.name == "Acme Securities LLC"
    # Nothing beyond domain + name on the dataclass.
    assert {f for f in result.__dataclass_fields__} == {"domain", "name"}


@respx.mock
async def test_find_company_404_is_clean_miss(hunter: HunterClient) -> None:
    respx.get(_HUNTER_URL).mock(return_value=httpx.Response(404, json={}))

    result = await hunter.find_company("Nonexistent Firm LLC")

    assert result is None


@respx.mock
async def test_find_company_empty_payload_returns_none(
    hunter: HunterClient,
) -> None:
    respx.get(_HUNTER_URL).mock(
        return_value=httpx.Response(200, json={"data": {}}),
    )

    assert await hunter.find_company("Acme") is None


@respx.mock
async def test_find_company_5xx_retries_then_errors(
    hunter: HunterClient,
) -> None:
    route = respx.get(_HUNTER_URL).mock(
        return_value=httpx.Response(500, text="boom"),
    )

    with pytest.raises(HunterError):
        await hunter.find_company("Acme Securities LLC")

    # Three attempts hit the endpoint.
    assert route.call_count == 3


@respx.mock
async def test_find_company_429_retries_then_succeeds(
    hunter: HunterClient,
) -> None:
    route = respx.get(_HUNTER_URL).mock(
        side_effect=[
            httpx.Response(429, text="rate limited"),
            httpx.Response(200, json=_company_payload()),
        ],
    )

    result = await hunter.find_company("Acme Securities LLC")

    assert isinstance(result, HunterCompany)
    assert route.call_count == 2


@respx.mock
async def test_find_company_non_retryable_4xx_raises_immediately(
    hunter: HunterClient,
) -> None:
    route = respx.get(_HUNTER_URL).mock(
        return_value=httpx.Response(401, json={"error": "auth"}),
    )

    with pytest.raises(HunterError):
        await hunter.find_company("Acme Securities LLC")

    # 401 is not retried.
    assert route.call_count == 1


async def test_find_company_blank_name_returns_none(hunter: HunterClient) -> None:
    assert await hunter.find_company("   ") is None
