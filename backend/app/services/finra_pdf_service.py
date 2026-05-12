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
        # FINRA's Cloudflare gateway responds with malformed compressed bodies
        # that surface as ``pdfminer: Data-loss while decompressing corrupted
        # data`` warnings on every Flate stream inside the PDF — the bytes
        # are silently mangled by httpx's auto-decompressor before pdfminer
        # ever sees them. Forcing identity + reading via ``aiter_raw`` keeps
        # the bytes verbatim. Same root cause + same fix as ``services/edgar.py``
        # and the SEC PDF path in ``services/pdf_downloader.py``.
        "Accept-Encoding": "identity",
    }

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 404:
                    raise FinraPdfNotFound(f"no PDF for CRD {crd}")
                if response.status_code != 200:
                    raise FinraPdfFetchError(f"http {response.status_code}")
                content_type = response.headers.get("content-type", "").lower()
                # aiter_raw, not aiter_bytes — bypass httpx auto-decompression.
                # Cloudflare sometimes sets Content-Encoding: gzip on PDF
                # bodies anyway; aiter_bytes auto-decompresses on that header
                # and corrupts already-application-compressed PDF streams.
                chunks: list[bytes] = []
                async for chunk in response.aiter_raw():
                    if chunk:
                        chunks.append(chunk)
                pdf_bytes = b"".join(chunks)
    except httpx.HTTPError as exc:
        raise FinraPdfFetchError(f"network: {exc.__class__.__name__}: {exc}") from exc

    if "pdf" not in content_type and not pdf_bytes.startswith(b"%PDF"):
        raise FinraPdfFetchError(
            f"unexpected content-type {content_type!r}; not a PDF"
        )

    return pdf_bytes
