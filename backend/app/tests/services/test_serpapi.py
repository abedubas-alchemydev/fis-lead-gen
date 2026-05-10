"""Unit tests for ``app.services.serpapi.SerpAPIClient``.

Covers the contract the resolver chain depends on:
- happy path → trimmed ``SerpResult`` list (top organic results only)
- empty ``organic_results`` → empty list (clean miss, not error)
- 5xx / 429 → ``SerpAPIError`` so the chain records provider-error
  semantics rather than caching a false miss
- response trimming → only ``url``/``domain``/``title`` survive; the
  payload's metadata block (which echoes the API key on every request)
  never leaks into ``SerpResult`` or any returned field

Tests use respx — no real SerpAPI quota is burned. The free-tier ceiling
is 100 searches/month and is reserved for production lazy resolution.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.serpapi import (
    SerpAPIClient,
    SerpAPIError,
    SerpResult,
)


_API_KEY = "test-serpapi-key"
_FIRM = "Pershing LLC"
_SEARCH_URL = "https://serpapi.com/search.json"


def _organic(link: str, title: str) -> dict[str, object]:
    return {"link": link, "title": title, "snippet": "ignored"}


def _payload(
    organic: list[dict[str, object]],
    *,
    include_search_metadata: bool = True,
) -> dict[str, object]:
    """SerpAPI-shaped JSON. ``search_metadata`` mirrors the real response
    and is exactly the kind of envelope we don't want bleeding through."""
    body: dict[str, object] = {"organic_results": organic}
    if include_search_metadata:
        body["search_metadata"] = {
            "id": "smt-fake-12345",
            "google_url": (
                f"https://www.google.com/search?q=Pershing+broker-dealer&api_key={_API_KEY}"
            ),
            "raw_html_file": "https://serpapi.com/searches/x/x.html",
        }
        body["search_parameters"] = {
            "engine": "google",
            "q": f"{_FIRM} broker-dealer",
            "api_key": _API_KEY,
        }
    return body


@respx.mock
async def test_happy_path_returns_trimmed_results() -> None:
    organic = [
        _organic("https://www.pershing.com/", "Pershing — Clearing & Custody"),
        _organic("https://www.bny.com/pershing", "BNY Pershing"),
        _organic("https://example.org/article", "About Pershing"),
    ]
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_payload(organic)),
    )
    client = SerpAPIClient(_API_KEY)

    results = await client.search_firm(_FIRM)

    assert len(results) == 3
    assert results[0] == SerpResult(
        url="https://www.pershing.com/",
        domain="www.pershing.com",
        title="Pershing — Clearing & Custody",
    )
    assert all(isinstance(r, SerpResult) for r in results)


@respx.mock
async def test_empty_organic_results_returns_empty_list() -> None:
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_payload([])),
    )
    client = SerpAPIClient(_API_KEY)

    results = await client.search_firm(_FIRM)

    assert results == []


@respx.mock
async def test_5xx_raises_serpapi_error() -> None:
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(500))
    client = SerpAPIClient(_API_KEY)

    with pytest.raises(SerpAPIError) as exc_info:
        await client.search_firm(_FIRM)
    assert "500" in str(exc_info.value)


@respx.mock
async def test_429_raises_serpapi_error() -> None:
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(429))
    client = SerpAPIClient(_API_KEY)

    with pytest.raises(SerpAPIError) as exc_info:
        await client.search_firm(_FIRM)
    assert "429" in str(exc_info.value)


@respx.mock
async def test_response_trimming_no_api_key_leak() -> None:
    """SerpResult must only carry url/domain/title — never the metadata
    block (which embeds the API key in google_url + search_parameters)."""
    organic = [
        _organic("https://www.pershing.com/", "Pershing — Home"),
    ]
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_payload(organic)),
    )
    client = SerpAPIClient(_API_KEY)

    results = await client.search_firm(_FIRM)

    assert len(results) == 1
    result = results[0]
    assert {f for f in result.__dataclass_fields__} == {"url", "domain", "title"}
    for field_value in (result.url, result.domain, result.title):
        assert _API_KEY not in field_value


@respx.mock
async def test_blank_link_entries_are_skipped() -> None:
    """Defensive — SerpAPI occasionally returns hits with no ``link``
    (e.g. featured snippets); they must not produce empty SerpResults."""
    organic = [
        {"link": "", "title": "Empty link"},
        _organic("https://www.pershing.com/", "Real result"),
        {"title": "No link key at all"},
    ]
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_payload(organic)),
    )
    client = SerpAPIClient(_API_KEY)

    results = await client.search_firm(_FIRM)

    assert len(results) == 1
    assert results[0].url == "https://www.pershing.com/"


def test_blank_api_key_rejected() -> None:
    with pytest.raises(ValueError):
        SerpAPIClient("")
