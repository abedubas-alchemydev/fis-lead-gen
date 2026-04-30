"""Apollo client tests.

All HTTP via respx — no real Apollo calls. The settings ``apollo_api_key``
is a constructor argument here (not read from settings), so we don't need
to monkey-patch the global config to keep test runs hermetic.

The tests lock the PRD constraint at the parser boundary: the client
returns ``ApolloExecutive`` (first_name + last_name + officer_rank) only,
even when the upstream response includes email/phone/linkedin_url.
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest
import respx

from app.services.apollo import (
    ApolloClient,
    ApolloError,
    ApolloExecutive,
)


_APOLLO_URL = "https://api.apollo.io/api/v1/mixed_people/search"


def _person(
    first: str,
    last: str,
    title: str,
    *,
    email: str | None = "leak@example.com",
    phone: str = "+1-555-0100",
    linkedin: str = "https://www.linkedin.com/in/leak",
) -> dict[str, object]:
    """Synthesize an Apollo person payload that includes the rich PII the
    client must NOT propagate. Used to lock the names-only contract."""
    return {
        "first_name": first,
        "last_name": last,
        "name": f"{first} {last}",
        "title": title,
        "email": email,
        "phone_numbers": [{"sanitized_number": phone}],
        "linkedin_url": linkedin,
    }


@pytest.fixture
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real exponential backoff sleep so retry tests stay fast."""
    async def _no_sleep(_attempt: int) -> None:
        return None

    monkeypatch.setattr(ApolloClient, "_backoff", staticmethod(_no_sleep))


@respx.mock
async def test_happy_path_returns_one_ceo() -> None:
    respx.post(_APOLLO_URL).mock(
        return_value=httpx.Response(
            200,
            json={"people": [_person("Jane", "Roe", "Chief Executive Officer")]},
        )
    )

    client = ApolloClient(api_key="test-key")
    results = await client.search_executives("Acme Securities LLC", "123456")

    assert len(results) == 1
    assert isinstance(results[0], ApolloExecutive)
    assert results[0].first_name == "Jane"
    assert results[0].last_name == "Roe"
    assert results[0].officer_rank == "ceo"


@respx.mock
async def test_response_trimmed_to_name_only(fast_backoff: None) -> None:
    """PRD regression test: even when Apollo ships email/phone/linkedin in
    the response, the client must not surface those channels. ``ApolloExecutive``
    has exactly three fields and no others."""
    respx.post(_APOLLO_URL).mock(
        return_value=httpx.Response(
            200,
            json={"people": [_person("Jane", "Roe", "CEO")]},
        )
    )

    client = ApolloClient(api_key="test-key")
    results = await client.search_executives("Acme")
    assert len(results) == 1

    field_names = {f.name for f in dataclasses.fields(results[0])}
    assert field_names == {"first_name", "last_name", "officer_rank"}, (
        "ApolloExecutive must remain names-only — the CSV-export PRD "
        "constraint forbids email/phone/linkedin on the Apollo path."
    )


@respx.mock
async def test_429_then_success_retries(fast_backoff: None) -> None:
    """A transient 429 is retried up to ``max_attempts`` times; the next
    successful response wins. Without this, a single rate-limit pulse
    burns the firm into the data_not_present bucket for nothing."""
    route = respx.post(_APOLLO_URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(
                200,
                json={"people": [_person("Sam", "Smith", "President")]},
            ),
        ]
    )

    client = ApolloClient(api_key="test-key", max_attempts=3)
    results = await client.search_executives("Acme")

    assert route.call_count == 2
    assert len(results) == 1
    assert results[0].officer_rank == "president"


@respx.mock
async def test_persistent_5xx_raises_apollo_error(fast_backoff: None) -> None:
    """Persistent 500s exhaust the retry budget and raise ``ApolloError``.
    Caller uses this to mark provider_error (and avoid silently empty)."""
    route = respx.post(_APOLLO_URL).mock(return_value=httpx.Response(503))

    client = ApolloClient(api_key="test-key", max_attempts=3)
    with pytest.raises(ApolloError):
        await client.search_executives("Acme")

    assert route.call_count == 3


@respx.mock
async def test_4xx_other_than_429_raises_immediately(fast_backoff: None) -> None:
    """A 401/403 is not a transient failure — surface it immediately as
    ``ApolloError`` so we don't burn the retry budget on auth bugs."""
    route = respx.post(_APOLLO_URL).mock(return_value=httpx.Response(401))

    client = ApolloClient(api_key="test-key", max_attempts=3)
    with pytest.raises(ApolloError):
        await client.search_executives("Acme")

    assert route.call_count == 1


@respx.mock
async def test_network_error_retries(fast_backoff: None) -> None:
    """Network errors (timeout, connection reset) follow the same retry
    chain as transient HTTP failures."""
    route = respx.post(_APOLLO_URL).mock(
        side_effect=[
            httpx.ConnectError("connection refused"),
            httpx.Response(
                200,
                json={"people": [_person("Pat", "Quinn", "CFO")]},
            ),
        ]
    )

    client = ApolloClient(api_key="test-key", max_attempts=3)
    results = await client.search_executives("Acme")
    assert route.call_count == 2
    assert results[0].officer_rank == "cfo"


@respx.mock
async def test_empty_people_returns_empty_list() -> None:
    respx.post(_APOLLO_URL).mock(
        return_value=httpx.Response(200, json={"people": []})
    )

    client = ApolloClient(api_key="test-key")
    results = await client.search_executives("Acme")
    assert results == []


@respx.mock
async def test_dedupes_by_first_last() -> None:
    respx.post(_APOLLO_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "people": [
                    _person("Jane", "Roe", "CEO"),
                    _person("Jane", "Roe", "President"),
                ]
            },
        )
    )

    client = ApolloClient(api_key="test-key")
    results = await client.search_executives("Acme")
    assert len(results) == 1


@respx.mock
async def test_falls_back_to_combined_name_field() -> None:
    """When Apollo omits ``first_name``/``last_name`` and only sends ``name``,
    the parser splits on whitespace."""
    respx.post(_APOLLO_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "people": [
                    {"name": "Casey Stone", "title": "COO"},
                ]
            },
        )
    )

    client = ApolloClient(api_key="test-key")
    results = await client.search_executives("Acme")
    assert len(results) == 1
    assert results[0].first_name == "Casey"
    assert results[0].last_name == "Stone"
    assert results[0].officer_rank == "coo"


@respx.mock
async def test_unknown_title_yields_other_rank() -> None:
    respx.post(_APOLLO_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "people": [_person("Alex", "Doe", "Director of Engineering")]
            },
        )
    )

    client = ApolloClient(api_key="test-key")
    results = await client.search_executives("Acme")
    assert results[0].officer_rank == "other"


def test_empty_api_key_raises() -> None:
    with pytest.raises(ValueError):
        ApolloClient(api_key="")


@respx.mock
async def test_blank_firm_name_short_circuits() -> None:
    route = respx.post(_APOLLO_URL).mock(return_value=httpx.Response(200, json={"people": []}))

    client = ApolloClient(api_key="test-key")
    results = await client.search_executives("   ")
    assert results == []
    assert route.call_count == 0
