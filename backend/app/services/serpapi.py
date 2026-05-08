"""SerpAPI Google-search client used as the third tier of the
firm-website resolver chain.

Apollo (Tier 1) and Hunter (Tier 2) cover most firms via direct
organization / company-finder lookups. When both miss, SerpAPI runs a
plain Google search for the firm name (no trailing qualifier) and
returns the top organic results. The resolver's existing ``_validate()``
helper (HEAD reachability + domain blocklist + page-title firm-name
match) filters those candidates exactly the same way Apollo + Hunter
results are filtered, so a SerpAPI hit is held to the same bar.

Why no ``" broker-dealer"`` suffix
----------------------------------
The original query was ``"<firm name> broker-dealer"`` to bias toward
regulatory pages. In practice that biased toward FINRA/SEC pages,
news articles, and aggregator listings — which the validator then
rejected via the blocklist + title checks — and demoted the firm's
own homepage past rank 5 (where the resolver stops walking). Concrete
repro: BANKERS LIFE SECURITIES, INC. Plain Google for the firm name
returns ``bankerslife.com`` at rank 1; the ``broker-dealer`` variant
demotes it to rank 6+ and the resolver missed every Bankers-Life-style
firm. Drop the qualifier; the validator already blocks regulator /
social / aggregator hits.

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

import asyncio
import logging
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


_SERPAPI_SEARCH_URL: Final = "https://serpapi.com/search.json"
_DEFAULT_TIMEOUT_S: Final = 10.0
_MAX_RESULTS: Final = 10
# Retry once on transient network errors (timeout, connection drop)
# before raising. Bulk runs we observed had a small but non-zero rate
# of single-firm SerpAPI flakes that succeeded immediately on retry —
# without this, those firms got marked ``all_providers_errored`` and
# stayed NULL until a follow-up backfill.
_RETRY_BACKOFF_S: Final = 2.0


class SerpAPIError(Exception):
    """Raised when SerpAPI returns a non-2xx status the resolver should treat
    as a provider error (so it falls through to the all-providers-errored
    path rather than caching a false miss)."""


@dataclass(slots=True, frozen=True)
class SerpResult:
    """Trimmed view of one SerpAPI search result.

    ``url`` is the candidate site the resolver feeds into ``_validate()``;
    ``domain`` is pre-parsed for cheap blocklist comparison upstream;
    ``title`` is the result title (used for logging context, not gating —
    the resolver re-checks the actual page ``<title>`` itself);
    ``is_high_confidence`` flags entity-resolved results from Google's
    ``knowledge_graph`` or ``answer_box`` panels — for these the resolver
    skips its domain-anchor heuristic since Google has already done the
    "is this site the firm" mapping.
    """

    url: str
    domain: str
    title: str
    is_high_confidence: bool = False


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
        """Run a Google search for the firm name (no qualifier).

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
            "q": firm_name,
            "api_key": self._api_key,
            "num": "10",
        }

        # Single retry with backoff on transient network failures only
        # (timeouts, connection drops). HTTP-status errors (4xx/5xx) are
        # NOT retried — those signal something the resolver should
        # surface immediately.
        response: httpx.Response | None = None
        last_exc: Exception | None = None
        for attempt in range(2):
            if attempt > 0:
                await asyncio.sleep(_RETRY_BACKOFF_S)
            try:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(
                        _SERPAPI_SEARCH_URL, params=params
                    )
                last_exc = None
                break
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                logger.info(
                    "SerpAPI transient error for '%s' (attempt %d/2): %s",
                    firm_name,
                    attempt + 1,
                    exc,
                )
                continue
            except httpx.HTTPError as exc:
                last_exc = exc
                break

        if response is None:
            logger.warning(
                "SerpAPI network error for '%s': %s", firm_name, last_exc
            )
            raise SerpAPIError(
                f"SerpAPI network error: {last_exc}"
            ) from last_exc

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

        results: list[SerpResult] = []

        # High-confidence: Google's entity-resolved knowledge graph card.
        # When present, the ``website`` field is Google's "this is the
        # firm's official site" signal — far more reliable than walking
        # organic results. Place at the FRONT of the list so the resolver
        # tries it first, and tag with ``is_high_confidence`` so the
        # validator can skip its domain-anchor heuristic.
        kg = payload.get("knowledge_graph")
        if isinstance(kg, dict):
            kg_url = kg.get("website")
            if isinstance(kg_url, str) and kg_url:
                domain = (urlparse(kg_url).hostname or "").lower()
                if domain:
                    title = kg.get("title")
                    results.append(
                        SerpResult(
                            url=kg_url,
                            domain=domain,
                            title=str(title) if isinstance(title, str) else "",
                            is_high_confidence=True,
                        )
                    )

        # High-confidence: Google's answer box (direct-answer panel).
        # Less common than knowledge graph; still entity-aware. Some
        # answer boxes carry a ``link`` (the source page); others carry
        # ``displayed_link`` (which is sometimes a domain string only).
        ab = payload.get("answer_box")
        if isinstance(ab, dict):
            ab_url = ab.get("link") or ab.get("displayed_link")
            if isinstance(ab_url, str) and ab_url:
                if "://" not in ab_url:
                    ab_url = f"https://{ab_url}"
                domain = (urlparse(ab_url).hostname or "").lower()
                if domain:
                    title = ab.get("title")
                    results.append(
                        SerpResult(
                            url=ab_url,
                            domain=domain,
                            title=str(title) if isinstance(title, str) else "",
                            is_high_confidence=True,
                        )
                    )

        # Regular (organic) results — capped at _MAX_RESULTS, walked in
        # order. Each must clear the validator's domain-anchor + path /
        # blocklist gates upstream.
        organic = payload.get("organic_results")
        if isinstance(organic, list):
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
