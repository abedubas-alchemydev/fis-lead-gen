from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.services.service_models import DownloadedPdfRecord

logger = logging.getLogger(__name__)

# Allowlist of hosts this service may fetch from. All SEC-owned. Extend only
# after security review — DB-sourced URLs (broker_dealer.filings_index_url)
# flow through this validator, so a wider allowlist directly widens the SSRF
# attack surface. See .claude/focus-fix/diagnosis.md §9 ticket S-1.
_SEC_ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov", "efts.sec.gov"})


def _validate_sec_url(url: str) -> None:
    """Reject non-SEC, non-HTTPS, or private-IP targets before any network call.

    Raises ValueError with a non-sensitive message. The hostname and scheme are
    safe to log because they originate from the DB (broker_dealer.filings_index_url)
    or from settings, not from end-user input.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only HTTPS is allowed; got scheme={parsed.scheme!r}.")
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL has no hostname: {url!r}.")
    if host not in _SEC_ALLOWED_HOSTS:
        raise ValueError(f"Host {host!r} is not in the SEC allowlist.")
    # Defense-in-depth: if the host is ever an IP literal (via misconfigured
    # allowlist or DNS rebind), reject private / loopback / link-local /
    # reserved / multicast ranges. GCP metadata (169.254.169.254) is caught
    # by is_link_local below.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Hostname is not an IP literal — already passed the allowlist, OK.
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError(f"IP literal {host!r} targets a private or reserved range.")

class PdfDownloaderService:
    def __init__(self) -> None:
        self.cache_dir = Path(settings.pdf_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def download_latest_x17a5_pdf(self, broker_dealer: BrokerDealer) -> DownloadedPdfRecord | None:
        return await self._download_live_pdf(broker_dealer)

    async def download_recent_x17a5_pdfs(self, broker_dealer: BrokerDealer, count: int = 2) -> list[DownloadedPdfRecord]:
        """Download the N most recent X-17A-5 PDFs for multi-year financial data."""
        if not broker_dealer.filings_index_url:
            return []

        submissions_payload = await self._get_json_with_retries(broker_dealer.filings_index_url)
        filings = list(self._iter_submission_filings(submissions_payload, broker_dealer.filings_index_url or ""))
        filings_section = submissions_payload.get("filings")
        additional_files = filings_section.get("files", []) if isinstance(filings_section, dict) else []

        for item in additional_files:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.endswith(".json"):
                continue
            history_url = urljoin(broker_dealer.filings_index_url or "", name)
            try:
                history_payload = await self._get_json_with_retries(history_url)
                filings.extend(self._iter_submission_filings(history_payload, history_url))
            except Exception:
                continue

        filings.sort(key=lambda item: str(item["filing_date"]), reverse=True)
        top_filings = filings[:count]

        results: list[DownloadedPdfRecord] = []
        for filing in top_filings:
            try:
                record = await self._download_filing_pdf(broker_dealer, filing)
                if record:
                    results.append(record)
            except Exception:
                continue
        return results

    async def _download_filing_pdf(self, broker_dealer: BrokerDealer, filing: dict[str, object]) -> DownloadedPdfRecord | None:
        """Download a single filing's PDF (with caching)."""
        filing_date = date.fromisoformat(str(filing["filing_date"]))
        accession_slug = str(filing["accession_number"]).replace("-", "")
        pdf_path = self.cache_dir / f"{broker_dealer.cik}-{accession_slug}.pdf"

        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            pdf_bytes = pdf_path.read_bytes()
            pdf_url = await self._resolve_pdf_url(
                cik=broker_dealer.cik,
                accession_number=str(filing["accession_number"]),
                primary_document=str(filing["primary_document"]),
            )
            return DownloadedPdfRecord(
                bd_id=broker_dealer.id, filing_year=filing_date.year,
                report_date=filing_date, source_filing_url=str(filing["filing_index_url"]),
                source_pdf_url=pdf_url, local_document_path=str(pdf_path),
                bytes_base64=base64.b64encode(pdf_bytes).decode("utf-8"),
            )

        pdf_url = await self._resolve_pdf_url(
            cik=broker_dealer.cik,
            accession_number=str(filing["accession_number"]),
            primary_document=str(filing["primary_document"]),
        )
        if pdf_url is None:
            return None

        pdf_bytes = await self._download_bytes_with_retries(pdf_url)
        max_size_mb = settings.gemini_inline_pdf_max_size_mb if settings.llm_provider == "gemini" else settings.openai_max_pdf_size_mb
        if len(pdf_bytes) > max_size_mb * 1024 * 1024:
            return None

        pdf_path.write_bytes(pdf_bytes)
        return DownloadedPdfRecord(
            bd_id=broker_dealer.id, filing_year=filing_date.year,
            report_date=filing_date, source_filing_url=str(filing["filing_index_url"]),
            source_pdf_url=pdf_url, local_document_path=str(pdf_path),
            bytes_base64=base64.b64encode(pdf_bytes).decode("utf-8"),
        )

    async def _download_live_pdf(self, broker_dealer: BrokerDealer) -> DownloadedPdfRecord | None:
        if not broker_dealer.filings_index_url:
            return None

        submissions_payload = await self._get_json_with_retries(broker_dealer.filings_index_url)
        filing = await self._find_latest_x17a5_filing(broker_dealer, submissions_payload)
        if filing is None:
            return None

        filing_date = date.fromisoformat(str(filing["filing_date"]))
        accession_slug = str(filing["accession_number"]).replace("-", "")
        pdf_path = self.cache_dir / f"{broker_dealer.cik}-{accession_slug}.pdf"

        # Cache hit: reuse the already-downloaded PDF to avoid redundant SEC requests.
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            logger.debug("PDF cache hit for BD %d: %s", broker_dealer.id, pdf_path.name)
            pdf_bytes = pdf_path.read_bytes()
            pdf_url = await self._resolve_pdf_url(
                cik=broker_dealer.cik,
                accession_number=str(filing["accession_number"]),
                primary_document=str(filing["primary_document"]),
            )
            return DownloadedPdfRecord(
                bd_id=broker_dealer.id,
                filing_year=filing_date.year,
                report_date=filing_date,
                source_filing_url=str(filing["filing_index_url"]),
                source_pdf_url=pdf_url,
                local_document_path=str(pdf_path),
                bytes_base64=base64.b64encode(pdf_bytes).decode("utf-8"),
            )

        pdf_url = await self._resolve_pdf_url(
            cik=broker_dealer.cik,
            accession_number=str(filing["accession_number"]),
            primary_document=str(filing["primary_document"]),
        )
        if pdf_url is None:
            return None

        pdf_bytes = await self._download_bytes_with_retries(pdf_url)
        max_size_mb = settings.gemini_inline_pdf_max_size_mb if settings.llm_provider == "gemini" else settings.openai_max_pdf_size_mb
        max_pdf_size_bytes = max_size_mb * 1024 * 1024
        if len(pdf_bytes) > max_pdf_size_bytes:
            raise RuntimeError(
                f"Downloaded PDF exceeds the configured {max_size_mb}MB inline ingestion limit for the selected provider."
            )

        pdf_path.write_bytes(pdf_bytes)
        logger.debug("PDF cached for BD %d: %s (%dKB)", broker_dealer.id, pdf_path.name, len(pdf_bytes) // 1024)

        return DownloadedPdfRecord(
            bd_id=broker_dealer.id,
            filing_year=filing_date.year,
            report_date=filing_date,
            source_filing_url=str(filing["filing_index_url"]),
            source_pdf_url=pdf_url,
            local_document_path=str(pdf_path),
            bytes_base64=base64.b64encode(pdf_bytes).decode("utf-8"),
        )

    async def _find_latest_x17a5_filing(
        self,
        broker_dealer: BrokerDealer,
        submissions_payload: dict[str, object],
    ) -> dict[str, object] | None:
        filings = list(self._iter_submission_filings(submissions_payload, broker_dealer.filings_index_url or ""))
        filings_section = submissions_payload.get("filings")
        additional_files = filings_section.get("files", []) if isinstance(filings_section, dict) else []

        for item in additional_files:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.endswith(".json"):
                continue
            history_url = urljoin(broker_dealer.filings_index_url or "", name)
            history_payload = await self._get_json_with_retries(history_url)
            filings.extend(self._iter_submission_filings(history_payload, history_url))

        filings.sort(key=lambda item: str(item["filing_date"]), reverse=True)
        return filings[0] if filings else None

    def _iter_submission_filings(self, payload: dict[str, object], submission_url: str) -> list[dict[str, object]]:
        filings_section = payload.get("filings")
        if not isinstance(filings_section, dict):
            return []

        recent = filings_section.get("recent")
        if not isinstance(recent, dict):
            return []

        forms = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])
        filing_dates = recent.get("filingDate", [])

        results: list[dict[str, object]] = []
        for form, accession_number, primary_document, filing_date in zip(
            forms, accession_numbers, primary_documents, filing_dates, strict=False
        ):
            if not isinstance(form, str) or "X-17A-5" not in form.upper():
                continue
            if not isinstance(accession_number, str) or not isinstance(primary_document, str) or not isinstance(filing_date, str):
                continue
            results.append(
                {
                    "form": form,
                    "accession_number": accession_number,
                    "primary_document": primary_document,
                    "filing_date": filing_date,
                    "filing_index_url": submission_url,
                }
            )

        return results

    async def _resolve_pdf_url(self, *, cik: str, accession_number: str, primary_document: str) -> str | None:
        accession_slug = accession_number.replace("-", "")
        cik_slug = str(int(cik))
        filing_directory_url = f"{settings.sec_archives_base_url}/{cik_slug}/{accession_slug}/"
        index_url = f"{filing_directory_url}index.json"

        try:
            index_payload = await self._get_json_with_retries(index_url)
        except Exception:
            if primary_document.lower().endswith(".pdf"):
                return f"{filing_directory_url}{primary_document}"
            return None

        directory = index_payload.get("directory", {}) if isinstance(index_payload, dict) else {}
        items = directory.get("item", []) if isinstance(directory, dict) else []
        pdf_candidates: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.lower().endswith(".pdf"):
                pdf_candidates.append(name)

        if not pdf_candidates and primary_document.lower().endswith(".pdf"):
            pdf_candidates.append(primary_document)
        if not pdf_candidates:
            return None

        selected_name = sorted(
            pdf_candidates,
            key=lambda name: (
                0 if name == primary_document else 1,
                0 if "x-17" in name.lower() or "x17" in name.lower() else 1,
                0 if "audit" in name.lower() or "report" in name.lower() else 1,
                len(name),
            ),
        )[0]
        return f"{filing_directory_url}{selected_name}"

    async def _get_json_with_retries(self, url: str) -> dict[str, object]:
        # URL validation runs BEFORE the retry loop, so a rejected URL does not
        # consume a retry slot — it raises ValueError directly to the caller.
        # The retry loop's ValueError handler below exists solely for
        # response.json() decode failures on otherwise-successful HTTP fetches.
        _validate_sec_url(url)
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "application/json",
        }
        last_error: Exception | None = None

        for attempt in range(1, settings.sec_request_max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=settings.sec_request_timeout_seconds,
                    headers=headers,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("SEC endpoint returned an unexpected JSON payload.")
                return payload
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt == settings.sec_request_max_retries:
                    raise
                await asyncio.sleep(min(2**attempt, 8))

        raise RuntimeError("Unable to retrieve SEC JSON payload.") from last_error

    async def _download_bytes_with_retries(self, url: str) -> bytes:
        _validate_sec_url(url)
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        }
        last_error: Exception | None = None

        for attempt in range(1, settings.sec_request_max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=settings.sec_request_timeout_seconds,
                    headers=headers,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "pdf" not in content_type and not url.lower().endswith(".pdf"):
                    raise RuntimeError("SEC filing did not resolve to a PDF document.")
                return response.content
            except (httpx.HTTPError, RuntimeError) as exc:
                last_error = exc
                if attempt == settings.sec_request_max_retries:
                    raise
                await asyncio.sleep(min(2**attempt, 8))

        raise RuntimeError("Unable to download SEC PDF after retries.") from last_error


