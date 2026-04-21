"""
FINRA BrokerCheck acquisition.

Uses the undocumented-but-stable JSON search API that BrokerCheck's own
frontend consumes, then resolves the PDF at the deterministic URL pattern
https://files.brokercheck.finra.org/firm/firm_{CRD}.pdf
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class FinraSearchHit:
    crd: str
    firm_name: str
    score: float


class FinraClient:
    """Async HTTP client for FINRA BrokerCheck."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=settings.per_request_timeout_s,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    async def __aenter__(self) -> "FinraClient":
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ search

    async def search_firm(self, firm_name: str, max_hits: int = 12) -> list[FinraSearchHit]:
        """Return ranked CRD candidates for a firm name."""
        params = {
            "query": firm_name,
            "hl": "true",
            "nrows": str(max_hits),
            "start": "0",
            "r": "25",
            "sort": "score+desc",
            "wt": "json",
        }

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.retries),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.get(settings.finra_search_url, params=params)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    await asyncio.sleep(retry_after)
                    resp.raise_for_status()
                resp.raise_for_status()
                payload = resp.json()

        return self._parse_search_response(payload)

    @staticmethod
    def _parse_search_response(payload: dict) -> list[FinraSearchHit]:
        """FINRA wraps results under hits.hits[*]._source.firm_{source,name}."""
        hits: list[FinraSearchHit] = []
        raw_hits = (payload or {}).get("hits", {}).get("hits", []) or []
        for h in raw_hits:
            src = h.get("_source") or {}
            crd = (
                src.get("firm_source_id")
                or src.get("firm_ia_full_source_id")
                or src.get("content_source_id")
                or ""
            )
            name = src.get("firm_name") or src.get("firm_ia_name") or ""
            if not crd:
                continue
            hits.append(
                FinraSearchHit(
                    crd=str(crd),
                    firm_name=name,
                    score=float(h.get("_score") or 0.0),
                )
            )
        return hits

    @staticmethod
    def _score_match(candidate: str, target: str) -> float:
        """Very cheap normalized-token containment score for disambiguation."""
        def norm(s: str) -> set[str]:
            s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
            return {t for t in s.split() if len(t) > 2 and t not in {"the", "llc", "inc", "co", "and"}}
        a, b = norm(candidate), norm(target)
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    async def resolve_crd(self, firm_name: str) -> Optional[str]:
        """Pick the best CRD for a name using name-token overlap."""
        hits = await self.search_firm(firm_name)
        if not hits:
            return None
        ranked = sorted(
            hits,
            key=lambda h: (self._score_match(h.firm_name, firm_name), h.score),
            reverse=True,
        )
        return ranked[0].crd

    # ------------------------------------------------------------------ pdf

    async def download_pdf(self, crd: str) -> bytes:
        """Download the Detailed Report PDF for a CRD."""
        url = settings.finra_pdf_url_template.format(crd=crd)

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.retries),
            wait=wait_exponential(multiplier=1, min=2, max=60),
            retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.get(url)
                if resp.status_code == 429:
                    await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                    resp.raise_for_status()
                if resp.status_code == 404:
                    raise FileNotFoundError(f"No BrokerCheck PDF for CRD {crd}")
                resp.raise_for_status()
                return resp.content

        raise RuntimeError("unreachable")
