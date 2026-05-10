"""FINRA BrokerCheck PDF fetch.

The Detailed Report PDF lives at a deterministic URL under
files.brokercheck.finra.org. Previous implementation imported
FinraClient from the sibling `brokercheck_extractor/` package, but that
directory is not copied into the backend Docker image (build context is
./backend/), so the import raised at runtime and surfaced as a broken
link on prod. This module inlines the minimal fetch so the endpoint is
self-contained within backend/.

Per Sprint 2 task #20 (2026-04-27 client meeting), the persistent disk
cache that previously sat at ``settings.pdf_cache_dir`` has been removed.
``fetch_brokercheck_pdf`` returns the PDF bytes; the caller (the
``/brokercheck.pdf`` endpoint) hands them straight back to the browser via
``Response(content=…)`` without ever touching disk.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

FINRA_PDF_URL_TEMPLATE = "https://files.brokercheck.finra.org/firm/firm_{crd}.pdf"
REQUEST_TIMEOUT_SECONDS = 20.0


class FinraPdfNotFound(Exception):
    """FINRA returned 404 for this CRD — no Detailed Report PDF exists."""


class FinraPdfFetchError(Exception):
    """Transient upstream failure from FINRA (network / 5xx / non-PDF body)."""


async def fetch_brokercheck_pdf(crd: str | int) -> bytes:
    """Download the FINRA BrokerCheck Detailed Report PDF for a CRD.

    Raises FinraPdfNotFound on 404, FinraPdfFetchError on any other failure.
    Callers wrap these into appropriate HTTP responses.
    """
    url = FINRA_PDF_URL_TEMPLATE.format(crd=crd)
    headers = {
        "User-Agent": settings.sec_user_agent,
        "Accept": "application/pdf",
    }

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise FinraPdfFetchError(f"network: {exc.__class__.__name__}: {exc}") from exc

    if response.status_code == 404:
        raise FinraPdfNotFound(f"no PDF for CRD {crd}")
    if response.status_code != 200:
        snippet = response.text[:200] if response.text else "(empty body)"
        raise FinraPdfFetchError(f"http {response.status_code}: {snippet}")

    content_type = response.headers.get("content-type", "").lower()
    if "pdf" not in content_type and not response.content.startswith(b"%PDF"):
        raise FinraPdfFetchError(
            f"unexpected content-type {content_type!r}; not a PDF"
        )

    return response.content
