"""serper.dev Google-search client used as a Tier 3 search provider in
the firm-website resolver chain, ahead of SerpAPI.

Why two search providers
------------------------
SerpAPI's Free plan caps at 250 searches/month and the parallel
website-backfill / orchestrator runs blew through that in a single
session. serper.dev exposes the same Google organic results at roughly
50× lower per-query cost ($0.30 / 1k vs SerpAPI's $0.015 / query on the
$75 Developer plan) and ships a one-time 2,500-query free credit on
signup. Slotting it ahead of SerpAPI in the chain means SerpAPI only
fires when serper itself misses or errors — a structural reduction in
SerpAPI quota burn that survives the demo and beyond.

Both clients return the same ``SerpResult`` dataclass (defined in
``services.serpapi``) so the resolver's ``_validate()`` gate doesn't
care which provider sourced the candidate.

Auth + protocol notes
---------------------
- POST request, JSON body (``{"q": "<query>", "num": 10}``). SerpAPI's
  GET-with-key-in-querystring is intentionally NOT mirrored — serper's
  documented surface is POST, and that keeps the API key out of every
  request URL (server logs, intermediaries, browser dev tools).
- API key carried in ``X-API-KEY`` header.
- Organic results live under the ``organic`` key (NOT
  ``organic_results`` like SerpAPI). Each hit has ``link``, ``title``,
  ``snippet``, ``position`` — we only carry forward ``link`` / ``title``
  through ``SerpResult`` (the resolver re-checks page title itself).
"""

from __future__ import annotations

import logging
from typing import Final
from urllib.parse import urlparse

import httpx

from app.services.serpapi import SerpResult

logger = logging.getLogger(__name__)


_SERPER_SEARCH_URL: Final = "https://google.serper.dev/search"
_DEFAULT_TIMEOUT_S: Final = 10.0
_MAX_RESULTS: Final = 10


class SerperError(Exception):
    """Raised when serper.dev returns a non-2xx status the resolver
    should treat as a provider error (so the chain can fall through to
    SerpAPI rather than caching a false miss)."""


class SerperClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not api_key:
            raise ValueError("serper.dev API key is required")
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def search_firm(self, firm_name: str) -> list[SerpResult]:
        """Run a Google search for the firm name (no qualifier).

        Returns the top-10 organic results trimmed to ``SerpResult``.
        Raises ``SerperError`` on a non-2xx response so the resolver
        chain can fall through to SerpAPI cleanly. Returns an empty list
        on a clean miss (200 with no organic block).
        """
        firm_name = firm_name.strip()
        if not firm_name:
            return []

        body = {"q": firm_name, "num": _MAX_RESULTS}
        headers = {
            "X-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    _SERPER_SEARCH_URL, json=body, headers=headers,
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "serper.dev network error for '%s': %s", firm_name, exc
            )
            raise SerperError(f"serper.dev network error: {exc}") from exc

        if response.status_code != 200:
            logger.warning(
                "serper.dev returned %d for '%s'",
                response.status_code,
                firm_name,
            )
            raise SerperError(
                f"serper.dev returned {response.status_code}"
            )

        return self._parse(response.json())

    @staticmethod
    def _parse(payload: object) -> list[SerpResult]:
        if not isinstance(payload, dict):
            return []
        organic = payload.get("organic")
        if not isinstance(organic, list):
            return []

        results: list[SerpResult] = []
        for hit in organic[:_MAX_RESULTS]:
            if not isinstance(hit, dict):
                continue
            url_raw = hit.get("link")
            if not isinstance(url_raw, str) or not url_raw:
                continue
            domain = (urlparse(url_raw).hostname or "").lower()
            if not domain:
                continue
            title = hit.get("title")
            results.append(
                SerpResult(
                    url=url_raw,
                    domain=domain,
                    title=str(title) if isinstance(title, str) else "",
                )
            )
        return results
