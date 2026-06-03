"""Unit tests for ``app.services.web_research.search_web`` — the generic
public-web search behind Doxie's ``research_term`` tool.

respx-mocked so no serper / SerpAPI quota is burned. Mirrors the
provider-fallback contract from ``test_linkedin_search`` (serper first,
SerpAPI on miss/error) and the parse contracts in ``test_serper`` /
``test_serpapi``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.web_research import search_web

_SERPER_URL = "https://google.serper.dev/search"
_SERPAPI_URL = "https://serpapi.com/search.json"


@pytest.fixture(autouse=True)
def _both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default both providers configured; individual tests override."""
    monkeypatch.setattr(settings, "serper_api_key", "test-serper", raising=False)
    monkeypatch.setattr(settings, "serpapi_api_key", "test-serpapi", raising=False)


def _serper_payload(
    items: list[tuple[str, str, str]],
) -> dict[str, object]:
    return {
        "organic": [
            {"link": url, "title": title, "snippet": snippet}
            for (url, title, snippet) in items
        ]
    }


@respx.mock
async def test_serper_hit_returns_shaped_results() -> None:
    respx.post(_SERPER_URL).mock(
        return_value=httpx.Response(
            200,
            json=_serper_payload(
                [("https://example.test/sofr", "SOFR", "SOFR is a benchmark rate.")]
            ),
        )
    )

    out = await search_web("SOFR")

    assert out["provider"] == "serper"
    assert out["results"][0] == {
        "title": "SOFR",
        "url": "https://example.test/sofr",
        "snippet": "SOFR is a benchmark rate.",
    }
    # serper never tags high-confidence → no distinct answer extracted.
    assert out["answer"] is None


@respx.mock
async def test_serper_error_falls_back_to_serpapi() -> None:
    respx.post(_SERPER_URL).mock(return_value=httpx.Response(500))
    serpapi_route = respx.get(_SERPAPI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "organic_results": [
                    {"link": "https://x.test", "title": "X", "snippet": "def"}
                ]
            },
        )
    )

    out = await search_web("Reg BI")

    assert serpapi_route.called
    assert out["provider"] == "serpapi"
    assert out["results"][0]["url"] == "https://x.test"


@respx.mock
async def test_serpapi_answer_box_description_becomes_answer() -> None:
    """serper misses (empty organic); SerpAPI's answer_box carries the
    definition text, which must surface as ``answer``."""
    respx.post(_SERPER_URL).mock(
        return_value=httpx.Response(200, json={"organic": []})
    )
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


async def test_no_keys_returns_empty_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "serper_api_key", None, raising=False)
    monkeypatch.setattr(settings, "serpapi_api_key", None, raising=False)

    out = await search_web("anything")

    assert out == {"results": [], "answer": None, "provider": None}


async def test_blank_query_returns_empty() -> None:
    assert await search_web("   ") == {
        "results": [],
        "answer": None,
        "provider": None,
    }
