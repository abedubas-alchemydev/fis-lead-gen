"""SerpAPI Google-search client used as the third tier of the
firm-website resolver chain.

Apollo (Tier 1) and Hunter (Tier 2) cover most firms via direct
organization / company-finder lookups. When both miss, SerpAPI runs a
plain Google search for ``"<firm name> broker-dealer"`` and returns the
top organic results. The resolver's existing ``_validate()`` helper
(HEAD reachability + domain blocklist + page-title firm-name match)
filters those candidates exactly the same way Apollo + Hunter results
are filtered, so a SerpAPI hit is held to the same bar.

Quota
-----
Free tier is 100 searches/month. The resolver fires lazily on a firm-
detail page visit only when ``bd.website`` is NULL and Apollo + Hunter
both produced no candidate, so 100/month is enough for testing-volume
traffic. Tests mock SerpAPI calls (respx) to avoid burning quota.

Response trimming
-----------------
SerpAPI's response carries the API key on every request URL plus a
large ``search_metadata`` block with billing identifiers. The client
extracts only ``link`` / ``title`` from each ``organic_results`` entry
and packs them into a frozen ``SerpResult`` so nothing else can leak
out to the resolver or downstream callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


_SERPAPI_SEARCH_URL: Final = "https://serpapi.com/search.json"
_DEFAULT_TIMEOUT_S: Final = 10.0
_MAX_RESULTS: Final = 10


class SerpAPIError(Exception):
    """Raised when SerpAPI returns a non-2xx status the resolver should treat
    as a provider error (so it falls through to the all-providers-errored
    path rather than caching a false miss)."""


@dataclass(slots=True, frozen=True)
class SerpResult:
    """Trimmed view of one SerpAPI organic result.

    ``url`` is the candidate site the resolver feeds into ``_validate()``;
    ``domain`` is pre-parsed for cheap blocklist comparison upstream;
    ``title`` is the result title (used for logging context, not gating —
    the resolver re-checks the actual page ``<title>`` itself).
    """

    url: str
    domain: str
    title: str


class SerpAPIClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not api_key:
            raise ValueError("SerpAPI key is required")
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def search_firm(self, firm_name: str) -> list[SerpResult]:
        """Run a Google search for ``"<firm_name> broker-dealer"``.

        Returns the top-10 organic results trimmed to ``SerpResult``.
        Raises ``SerpAPIError`` on a non-2xx response so the resolver
        chain can record provider-error semantics. Returns an empty list
        on a clean miss (200 with no organic_results).
        """
        firm_name = firm_name.strip()
        if not firm_name:
            return []

        params = {
            "engine": "google",
            "q": f"{firm_name} broker-dealer",
            "api_key": self._api_key,
            "num": "10",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(_SERPAPI_SEARCH_URL, params=params)
        except httpx.HTTPError as exc:
            logger.warning(
                "SerpAPI network error for '%s': %s", firm_name, exc
            )
            raise SerpAPIError(f"SerpAPI network error: {exc}") from exc

        if response.status_code != 200:
            logger.warning(
                "SerpAPI returned %d for '%s'",
                response.status_code,
                firm_name,
            )
            raise SerpAPIError(
                f"SerpAPI returned {response.status_code}"
            )

        return self._parse(response.json())

    @staticmethod
    def _parse(payload: object) -> list[SerpResult]:
        if not isinstance(payload, dict):
            return []
        organic = payload.get("organic_results")
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
