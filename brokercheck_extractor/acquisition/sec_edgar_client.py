"""
SEC EDGAR acquisition for Form X-17A-5 (annual audited financial statements).

Strategy:
  1. Full-text search (efts.sec.gov) to resolve the firm to a CIK when we don't have one.
  2. data.sec.gov submissions JSON for the CIK gives the full filing history with
     form types — we filter to X-17A-5 variants and pull the latest two filings.
  3. Download the primary document (often a PDF already; sometimes an index + linked PDF).
"""
from __future__ import annotations

import asyncio
import logging
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

X17_FORM_TYPES = {"X-17A-5", "X-17A-5/A", "X-17A-5 PART II", "X-17A-5 PART III"}


@dataclass
class X17Filing:
    cik: str
    accession_number: str          # formatted, with dashes
    form_type: str
    filing_date: str               # ISO date
    primary_document: str          # filename within the accession folder
    pdf_bytes: Optional[bytes] = None


class SecEdgarClient:
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._owns_client = client is None
        # SEC explicitly requires a descriptive User-Agent or will 403.
        headers = {"User-Agent": settings.user_agent, "Accept": "application/json"}
        self._client = client or httpx.AsyncClient(
            timeout=settings.per_request_timeout_s,
            headers=headers,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "SecEdgarClient":
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---------------------------------------------------------------- resolve

    async def resolve_cik(self, firm_name: str) -> Optional[str]:
        """Use EDGAR full-text search, restricted to X-17A-5, to resolve CIK."""
        params = {"q": f'"{firm_name}"', "forms": "X-17A-5"}
        payload = await self._get_json(settings.sec_edgar_search_url, params=params)

        hits = (payload or {}).get("hits", {}).get("hits", []) or []
        for h in hits:
            src = h.get("_source") or {}
            ciks = src.get("ciks") or []
            if ciks:
                return str(ciks[0]).lstrip("0") or ciks[0]
        return None

    # ------------------------------------------------------------ submissions

    async def list_x17_filings(self, cik: str, limit: int = 2) -> list[X17Filing]:
        """Return the latest N X-17A-5 filings for this CIK, newest first."""
        cik10 = cik.zfill(10)
        url = settings.sec_submissions_url_template.format(cik10=cik10)
        payload = await self._get_json(url)

        recent = (payload or {}).get("filings", {}).get("recent", {}) or {}
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        dates = recent.get("filingDate") or []
        primary_docs = recent.get("primaryDocument") or []

        filings: list[X17Filing] = []
        for form, accession, fdate, pdoc in zip(forms, accessions, dates, primary_docs):
            if form in X17_FORM_TYPES or form.startswith("X-17A-5"):
                filings.append(
                    X17Filing(
                        cik=cik,
                        accession_number=accession,
                        form_type=form,
                        filing_date=fdate,
                        primary_document=pdoc,
                    )
                )
            if len(filings) >= limit:
                break
        return filings

    # ------------------------------------------------------------- download

    async def download_filing(self, filing: X17Filing) -> bytes:
        """Download the primary document for an X-17A-5 filing."""
        accession_nodash = filing.accession_number.replace("-", "")
        url = settings.sec_archive_url_template.format(
            cik_int=int(filing.cik),
            accession_nodash=accession_nodash,
            primary_doc=filing.primary_document,
        )
        return await self._get_bytes(url)

    # ---------------------------------------------------------------- helpers

    async def _get_json(self, url: str, params: Optional[dict] = None) -> dict:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.retries),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.get(url, params=params)
                if resp.status_code == 429:
                    await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                    resp.raise_for_status()
                resp.raise_for_status()
                return resp.json()
        return {}

    async def _get_bytes(self, url: str) -> bytes:
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
                resp.raise_for_status()
                return resp.content
        raise RuntimeError("unreachable")
