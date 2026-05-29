"""Unit tests for ``app.services.serper.SerperClient``.

Mirrors the contract tests for ``SerpAPIClient`` since the resolver
chain holds both clients to the same shape (return ``list[SerpResult]``,
raise on non-2xx). Covers:
- happy path → trimmed ``SerpResult`` list
- empty / missing organic block → empty list (clean miss, not error)
- 5xx / 429 → ``SerperError`` so the chain falls through to SerpAPI
  rather than caching a false miss
- POST body shape + ``X-API-KEY`` header (locks the protocol distinct
  from SerpAPI's GET-with-key-in-querystring)
- response trimming → only ``url``/``domain``/``title`` survive

Tests use respx — no real serper.dev quota is burned.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.services.serper import (
    SerperClient,
    SerperError,
)
from app.services.serpapi import SerpResult


_API_KEY = "test-serper-key"
_FIRM = "Pershing LLC"
_SEARCH_URL = "https://google.serper.dev/search"


def _organic(link: str, title: str) -> dict[str, object]:
    return {"link": link, "title": title, "snippet": "ignored", "position": 1}


def _payload(organic: list[dict[str, object]]) -> dict[str, object]:
    return {"organic": organic, "searchParameters": {"q": _FIRM}}


@respx.mock
async def test_happy_path_returns_trimmed_results() -> None:
    organic = [
        _organic("https://www.pershing.com/", "Pershing — Clearing & Custody"),
        _organic("https://www.bny.com/pershing", "BNY Pershing"),
    ]
    respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_payload(organic)),
    )
    client = SerperClient(_API_KEY)

    results = await client.search_firm(_FIRM)

    assert len(results) == 2
    assert results[0] == SerpResult(
        url="https://www.pershing.com/",
        domain="www.pershing.com",
        title="Pershing — Clearing & Custody",
        snippet="ignored",
    )
    assert all(isinstance(r, SerpResult) for r in results)


@respx.mock
async def test_empty_organic_returns_empty_list() -> None:
    respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_payload([])),
    )
    client = SerperClient(_API_KEY)

    assert await client.search_firm(_FIRM) == []


@respx.mock
async def test_missing_organic_key_returns_empty_list() -> None:
    """Defensive — serper.dev occasionally returns 200 with no organic
    block (e.g. when the query produced only an answer-box). Must be a
    clean miss, not a parse crash."""
    respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"searchParameters": {"q": _FIRM}}),
    )
    client = SerperClient(_API_KEY)

    assert await client.search_firm(_FIRM) == []


@respx.mock
async def test_5xx_raises_serper_error() -> None:
    respx.post(_SEARCH_URL).mock(return_value=httpx.Response(500))
    client = SerperClient(_API_KEY)

    with pytest.raises(SerperError) as exc_info:
        await client.search_firm(_FIRM)
    assert "500" in str(exc_info.value)


@respx.mock
async def test_429_raises_serper_error() -> None:
    respx.post(_SEARCH_URL).mock(return_value=httpx.Response(429))
    client = SerperClient(_API_KEY)

    with pytest.raises(SerperError) as exc_info:
        await client.search_firm(_FIRM)
    assert "429" in str(exc_info.value)


@respx.mock
async def test_request_carries_post_body_and_api_key_header() -> None:
    """Locks the protocol shape: POST with ``X-API-KEY`` header and
    ``{"q": <firm>, "num": 10}`` JSON body. Distinct from SerpAPI's
    GET-with-key-in-querystring."""
    route = respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_payload([])),
    )
    client = SerperClient(_API_KEY)

    await client.search_firm(_FIRM)

    assert route.call_count == 1
    request = route.calls[0].request
    assert request.method == "POST"
    assert request.headers.get("x-api-key") == _API_KEY
    body = json.loads(request.content.decode())
    assert body == {"q": _FIRM, "num": 10}


@respx.mock
async def test_blank_link_entries_are_skipped() -> None:
    organic = [
        {"link": "", "title": "Empty link"},
        _organic("https://www.pershing.com/", "Real result"),
        {"title": "No link key at all"},
    ]
    respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_payload(organic)),
    )
    client = SerperClient(_API_KEY)

    results = await client.search_firm(_FIRM)

    assert len(results) == 1
    assert results[0].url == "https://www.pershing.com/"


def test_blank_api_key_rejected() -> None:
    with pytest.raises(ValueError):
        SerperClient("")


async def test_blank_firm_name_returns_empty() -> None:
    client = SerperClient(_API_KEY)
    assert await client.search_firm("   ") == []
