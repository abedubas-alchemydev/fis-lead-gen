"""Generic SEC PDF fetcher used by Doxie's filing-summarize tools.

Why this exists separately from ``pdf_downloader.py``:

``pdf_downloader.PdfDownloaderService`` is tightly bound to the
broker-dealer X-17A-5 workflow — it consumes a ``BrokerDealer`` ORM
object, walks ``filings_index_url`` to find the latest accession, and
applies multi-document filename scoring to pick the right PDF inside a
package. None of that fits the Doxie use case for IA / II / Form 4
filings: those tables carry a flat ``source_filing_url`` that points
directly at the SEC EDGAR HTML index for one accession, and the model
just wants the bytes of "the PDF for this filing."

So we expose a small helper that:
- Validates the URL against the same SEC SSRF allowlist as
  ``pdf_downloader._validate_sec_url`` (so widening the attack surface
  requires changing that module too).
- Downloads the document at the URL with the project's SEC User-Agent.
- Streams to memory with a hard size ceiling matching
  ``settings.gemini_inline_pdf_max_size_mb`` so an inflated body can't
  starve the chat worker.

Returns raw bytes; the caller base64-encodes for the Gemini payload.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


# Source-of-truth for the allowed SEC hosts lives in
# ``app.services.pdf_downloader._SEC_ALLOWED_HOSTS``; we re-declare here
# (instead of importing the private name) so a future widening of either
# allowlist is a deliberate two-file change. Keep them in sync via grep.
_SEC_ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov", "efts.sec.gov"})

_REQUEST_TIMEOUT_SECONDS = 30.0


class SecPdfFetchError(Exception):
    """A SEC PDF fetch failed (validation, 4xx/5xx, network, or oversize)."""


def _validate_sec_url(url: str) -> None:
    """Mirror of ``pdf_downloader._validate_sec_url`` — duplicated to keep
    the allowlist surface explicit in this module too.

    Raises ``ValueError`` with a non-sensitive message. The hostname and
    scheme are safe to log because they originate from the DB
    (``AdvisorFiling.source_filing_url`` etc.) or from settings, not from
    end-user chat input.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only HTTPS is allowed; got scheme={parsed.scheme!r}.")
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL has no hostname: {url!r}.")
    if host not in _SEC_ALLOWED_HOSTS:
        raise ValueError(f"Host {host!r} is not in the SEC allowlist.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Hostname isn't an IP literal — allowlist already approved it.
        return
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    ):
        raise ValueError(f"IP literal {host!r} targets a private or reserved range.")


async def fetch_sec_pdf_bytes(url: str) -> bytes:
    """Download ``url`` from SEC and return the raw PDF bytes.

    Validates the URL against the SEC allowlist first, then streams the
    response into memory with a size ceiling. Raises ``SecPdfFetchError``
    on any failure path: bad URL, oversize body, non-PDF response,
    network error, or non-2xx status.

    NOTE: This downloads exactly what's at the URL — if the URL points
    at the EDGAR HTML index for a filing (the common shape for
    ``*_filing.source_filing_url``), the bytes returned are HTML, not
    PDF. Callers may need to resolve the actual PDF document URL first;
    for now Doxie surfaces an explicit error so the model can apologise
    and point the user at the source URL instead of generating a wrong
    summary off the HTML index.
    """
    try:
        _validate_sec_url(url)
    except ValueError as exc:
        raise SecPdfFetchError(f"URL validation failed: {exc}") from exc

    max_bytes = settings.gemini_inline_pdf_max_size_mb * 1024 * 1024
    headers = {
        "User-Agent": settings.sec_user_agent,
        "Accept": "application/pdf",
        # See finra_pdf_service comment — identity transfer keeps the
        # raw PDF bytes verbatim through Cloudflare-style gateways.
        "Accept-Encoding": "identity",
    }

    try:
        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 404:
                    raise SecPdfFetchError(f"SEC returned 404 for {url!r}")
                if response.status_code != 200:
                    raise SecPdfFetchError(f"SEC http {response.status_code}")

                content_type = response.headers.get("content-type", "").lower()

                chunks: list[bytes] = []
                running_size = 0
                async for chunk in response.aiter_raw():
                    if not chunk:
                        continue
                    running_size += len(chunk)
                    if running_size > max_bytes:
                        raise SecPdfFetchError(
                            f"document at {url!r} exceeded size ceiling "
                            f"{settings.gemini_inline_pdf_max_size_mb} MB"
                        )
                    chunks.append(chunk)
                body = b"".join(chunks)
    except httpx.HTTPError as exc:
        raise SecPdfFetchError(
            f"network: {exc.__class__.__name__}: {exc}"
        ) from exc

    # Sanity-check the body shape. Many ``source_filing_url`` values
    # point at the EDGAR HTML index (``.../index.json`` / ``.htm``) for
    # the filing rather than a raw PDF — surface that as a clean error
    # so the chat tool can fall back to "here's the source link" rather
    # than feeding HTML to Gemini and getting a confused summary.
    if "pdf" not in content_type and not body.startswith(b"%PDF"):
        raise SecPdfFetchError(
            f"response from {url!r} is not a PDF "
            f"(content_type={content_type!r}); the URL may point at the "
            f"EDGAR HTML filing index rather than the document itself."
        )

    return body
