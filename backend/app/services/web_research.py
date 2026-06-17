"""Generic public-web search behind Doxie's ``research_term`` tool.

When Doxie hits a term it doesn't know, ``research_term`` researches it on
the public web. This module runs that search, reusing the firm-website
resolver's SerpAPI client (``serpapi.py``). Despite its ``search_firm``
method name, the client issues a plain ``q=`` Google search with no
firm-specific filtering, so it doubles as a general web-search backend
here.

Two deliberate choices:

- **Tight per-client timeout.** A hung search must not eat Doxie's per-tool
  execution budget (``TOOL_EXECUTION_TIMEOUT_S`` in ``chatbot.py``), so we
  pass a short ``timeout_s`` into each client. The ``research_term`` tool
  itself sets a larger ``timeout_s`` to absorb the SerpAPI single-retry.
- **Never raises.** A missing API key or a provider error degrades to an
  empty payload (``provider`` is ``None``) so the caller can fall back to
  the model's own knowledge instead of 500'ing the chat.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from app.core.config import settings
from app.services.serpapi import SerpAPIClient, SerpAPIError, SerpResult

logger = logging.getLogger(__name__)

# Keep the search call well under the chat service's 5s per-tool budget.
# The common SerpAPI path resolves in ~1-2s; bounding it here means a
# slow provider can't silently consume the whole iteration budget.
WEB_RESEARCH_CLIENT_TIMEOUT_S: Final = 6.0
WEB_RESEARCH_DEFAULT_LIMIT: Final = 4


async def search_web(
    query: str, *, limit: int = WEB_RESEARCH_DEFAULT_LIMIT
) -> dict[str, Any]:
    """Run a generic web search via SerpAPI.

    Returns
    ``{"results": [{"title", "url", "snippet"}], "answer": str | None,
    "provider": "serpapi" | None}``. ``answer`` is the best
    high-confidence snippet (Google knowledge-graph / answer-box text) when
    one is present — the cleanest definition source. Never raises: a missing
    key or provider error yields an empty payload with ``provider`` None.
    """
    query = (query or "").strip()
    if not query:
        return {"results": [], "answer": None, "provider": None}

    hits: list[SerpResult] = []
    provider: str | None = None

    serpapi_key = getattr(settings, "serpapi_api_key", None)
    if serpapi_key:
        try:
            # ``search_firm`` is a generic ``q=`` Google search despite the
            # name — there is no firm-specific filtering inside the client.
            hits = await SerpAPIClient(
                serpapi_key, timeout_s=WEB_RESEARCH_CLIENT_TIMEOUT_S
            ).search_firm(query)
            provider = "serpapi" if hits else None
        except SerpAPIError as exc:
            logger.info("web_research serpapi error for %r: %s", query, exc)

    answer = next(
        (h.snippet for h in hits if h.is_high_confidence and h.snippet), None
    )
    results = [
        {"title": h.title, "url": h.url, "snippet": h.snippet}
        for h in hits[: max(1, limit)]
    ]
    return {"results": results, "answer": answer, "provider": provider}
