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
import re
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.pdf_downloader import resolve_filing_pdf_url

logger = logging.getLogger(__name__)


# Matches an EDGAR Archives path: ``/Archives/edgar/data/{cik}/{accession_no_dashes}/...``
# Captures cik (variable-length int) and accession_no_dashes (typically 18
# digits). The DB-stored URLs all share this prefix; anything that doesn't
# match isn't an EDGAR filing-package URL so the chat tool should bail.
_EDGAR_FILING_PATH_RE = re.compile(
    r"^/Archives/edgar/data/(?P<cik>\d+)/(?P<accession>\d+)(?:/|$)"
)


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


def _parse_edgar_filing_path(url: str) -> tuple[str, str] | None:
    """Return ``(cik, accession_no_dashes)`` for an EDGAR filing URL.

    Returns ``None`` if the URL doesn't look like an
    ``/Archives/edgar/data/{cik}/{accession}/...`` path. Callers handle
    that as "this URL isn't an EDGAR filing — can't resolve via
    index.json."
    """
    parsed = urlparse(url)
    if not parsed.path:
        return None
    match = _EDGAR_FILING_PATH_RE.match(parsed.path)
    if match is None:
        return None
    return match.group("cik"), match.group("accession")


async def fetch_filing_pdf_bytes(
    source_filing_url: str, *, form_type: str | None = None
) -> bytes:
    """Resolve an EDGAR filing URL to a PDF document, then download it.

    Doxie's IA / II tools store ``source_filing_url`` values that often
    point at the EDGAR HTML filing index (the ``/.../{accession}-index.htm``
    file) rather than a direct PDF. This helper:

    1. If the URL itself ends in ``.pdf``, downloads it directly via
       :func:`fetch_sec_pdf_bytes`.
    2. Otherwise parses ``cik`` + ``accession`` out of the URL path and
       calls :func:`app.services.pdf_downloader.resolve_filing_pdf_url`
       to walk the filing's ``index.json`` and pick the most useful
       PDF inside the package — biased by ``form_type`` so ADV filings
       prefer the Part 2A brochure, X-17A-5 prefers the Statement of
       Financial Condition, etc.
    3. Downloads the resolved PDF URL with the same streaming + SSRF
       checks as :func:`fetch_sec_pdf_bytes`.

    Raises :class:`SecPdfFetchError` when:
    - the input URL isn't an EDGAR filing path (malformed DB row),
    - the filing package contains no PDFs (Form 4 / 13F-HR / Schedule
      13D-G are typically XML-only — the chat tool should fall back
      to the source URL rather than trying to summarise text-only
      content),
    - the resolved PDF download fails for any reason.
    """
    parsed = urlparse(source_filing_url)
    if parsed.path.lower().endswith(".pdf"):
        # Caller already has a direct PDF link — skip the resolver and
        # download in one round-trip. (Some AdvisorFiling rows do
        # store a direct ``.../{cik}/{accession}/primary_document.pdf``
        # URL via ``edgar.build_edgar_filing_url`` when the watcher
        # knew the primary doc.)
        return await fetch_sec_pdf_bytes(source_filing_url)

    parsed_ids = _parse_edgar_filing_path(source_filing_url)
    if parsed_ids is None:
        raise SecPdfFetchError(
            f"URL {source_filing_url!r} isn't an EDGAR filing path — "
            f"can't resolve to a PDF document."
        )
    cik, accession = parsed_ids

    pdf_url = await resolve_filing_pdf_url(
        cik=cik,
        accession_number=accession,
        primary_document=None,
        form_type=form_type,
    )
    if pdf_url is None:
        raise SecPdfFetchError(
            f"no PDF in filing package for accession {accession!r}; the "
            f"form may be XML-only (Form 4, 13F-HR, 13D/G). Offer the "
            f"source URL to the user instead of summarising."
        )

    return await fetch_sec_pdf_bytes(pdf_url)
