"""Unit tests for ``app.services.web_research.search_web`` — the generic
public-web search behind Doxie's ``research_term`` tool.

respx-mocked so no SerpAPI quota is burned. Validates the SerpAPI parse +
answer-box extraction and the graceful-degrade contract (missing key / error
-> empty payload, never raises).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.web_research import search_web

_SERPAPI_URL = "https://serpapi.com/search.json"


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default SerpAPI configured; individual tests override."""
    monkeypatch.setattr(settings, "serpapi_api_key", "test-serpapi", raising=False)


@respx.mock
async def test_serpapi_hit_returns_shaped_results() -> None:
    respx.get(_SERPAPI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "organic_results": [
                    {
                        "link": "https://example.test/sofr",
                        "title": "SOFR",
                        "snippet": "SOFR is a benchmark rate.",
                    }
                ]
            },
        )
    )

    out = await search_web("SOFR")

    assert out["provider"] == "serpapi"
    assert out["results"][0] == {
        "title": "SOFR",
        "url": "https://example.test/sofr",
        "snippet": "SOFR is a benchmark rate.",
    }


@respx.mock
async def test_serpapi_error_returns_empty() -> None:
    """A provider error degrades to an empty payload (never raises) so the
    caller can fall back to the model's own knowledge."""
    respx.get(_SERPAPI_URL).mock(return_value=httpx.Response(500))

    out = await search_web("Reg BI")

    assert out == {"results": [], "answer": None, "provider": None}


@respx.mock
async def test_serpapi_answer_box_description_becomes_answer() -> None:
    """SerpAPI's answer_box carries the definition text, which must surface
    as ``answer``."""
    respx.get(_SERPAPI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "answer_box": {
                    "title": "T+1",
                    "link": "https://sec.gov/t1",
                    "snippet": "T+1 settles one business day after the trade.",
                },
                "organic_results": [
                    {"link": "https://sec.gov/t1", "title": "T+1", "snippet": "..."}
                ],
            },
        )
    )

    out = await search_web("T+1 settlement")

    assert out["provider"] == "serpapi"
    assert out["answer"] == "T+1 settles one business day after the trade."


async def test_no_key_returns_empty_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "serpapi_api_key", None, raising=False)

    out = await search_web("anything")

    assert out == {"results": [], "answer": None, "provider": None}


async def test_blank_query_returns_empty() -> None:
    assert await search_web("   ") == {
        "results": [],
        "answer": None,
        "provider": None,
    }
