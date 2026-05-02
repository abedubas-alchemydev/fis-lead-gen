from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import httpx

from app.core.config import settings
from app.services.normalization import normalize_sec_file_number
from app.services.service_models import EdgarBrokerDealerRecord


logger = logging.getLogger(__name__)

# Cached bulk ZIP is considered stale after this many seconds (7 days).
_BULK_ZIP_TTL_SECONDS = 7 * 24 * 60 * 60


class EdgarService:
    PROJECT_ROOT = Path(__file__).resolve().parents[3]
    BROWSE_HEADER_PATTERN = re.compile(
        r'companyName">(?P<name>.*?)<acronym title="Central Index Key">CIK</acronym>#: '
        r'<a href="/cgi-bin/browse-edgar\?action=getcompany&amp;CIK=(?P<cik>\d+)',
        re.IGNORECASE | re.DOTALL,
    )
    STATE_LOCATION_PATTERN = re.compile(
        r'State location:\s*<a [^>]*>(?P<state>[A-Z]{2})</a>',
        re.IGNORECASE,
    )
    STATE_OF_INC_PATTERN = re.compile(
        r'State of Inc\.:\s*<strong>(?P<state>[A-Z]{2})</strong>',
        re.IGNORECASE,
    )
    FILING_DATE_PATTERN = re.compile(r"<td>(?P<date>\d{4}-\d{2}-\d{2})</td>")

    async def fetch_all_broker_dealers(
        self,
        limit: int | None = None,
        *,
        force_refresh: bool = False,
    ) -> list[EdgarBrokerDealerRecord]:
        # Try the lightweight EDGAR company-search endpoint first.  It lists
        # all filers matching a SIC code via a small, paginated Atom feed
        # (~50 pages of 100 results) instead of the multi-GB bulk ZIP.
        try:
            fast_records = await self._fetch_via_company_search(limit=limit)
            if fast_records:
                logger.info("EDGAR company-search returned %d broker-dealer records.", len(fast_records))
                return fast_records
        except Exception as exc:
            logger.warning("EDGAR company-search fast-path failed (%s); falling back to bulk ZIP.", exc)

        zip_path = await self._ensure_bulk_submissions_zip(force_refresh=force_refresh)
        return await asyncio.to_thread(self._parse_bulk_submissions_zip, zip_path, limit)

    async def _fetch_via_company_search(self, *, limit: int | None = None) -> list[EdgarBrokerDealerRecord]:
        """Use the SEC EDGAR company search (browse-edgar with SIC filter) to
        enumerate all broker-dealer filers.  This returns structured data for
        all SIC 6211 entities (~5,000 filers) via a paginated HTML response,
        avoiding the need to download the multi-GB bulk submissions ZIP.

        This is the official, structured company-search endpoint — not the
        full-text search (EFTS) which searches filing *content*.
        """
        target_sic_codes = [code.strip() for code in settings.edgar_target_sic_codes.split(",") if code.strip()]
        if not target_sic_codes:
            return []

        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "text/html,application/xhtml+xml",
            # httpx auto-negotiates Accept-Encoding: gzip, deflate, br, zstd by default.
            # SEC EDGAR's Cloudflare gateway responds with malformed compressed bodies
            # that raise "Data-loss while decompressing corrupted data" on every request.
            # Forcing identity bypasses compression entirely (same fix as services/finra.py).
            "Accept-Encoding": "identity",
        }
        all_records: list[EdgarBrokerDealerRecord] = []
        seen_ciks: set[str] = set()

        async with httpx.AsyncClient(
            timeout=settings.sec_request_timeout_seconds,
            headers=headers,
            follow_redirects=True,
        ) as client:
            for sic_code in target_sic_codes:
                start = 0
                page_size = 100

                while True:
                    if limit is not None and len(all_records) >= limit:
                        return all_records

                    params = {
                        "action": "getcompany",
                        "SIC": sic_code,
                        "dateb": "",
                        "owner": "include",
                        "count": page_size,
                        "search_text": "",
                        "start": start,
                    }

                    for attempt in range(1, settings.sec_request_max_retries + 1):
                        try:
                            response = await client.get(
                                "https://www.sec.gov/cgi-bin/browse-edgar",
                                params=params,
                            )
                            if response.status_code == 429:
                                retry_after = response.headers.get("retry-after")
                                wait = float(retry_after) if retry_after else min(2**attempt, 30)
                                await asyncio.sleep(wait)
                                continue
                            response.raise_for_status()
                            break
                        except httpx.HTTPError:
                            if attempt == settings.sec_request_max_retries:
                                return []  # Fall back to bulk ZIP
                            await asyncio.sleep(min(2**attempt, 8))
                    else:
                        return []

                    page_html = response.text

                    # Parse CIK + company name pairs from the HTML table.
                    page_records = self._parse_company_search_page(page_html, sic_code)
                    if not page_records:
                        break

                    new_count = 0
                    for record in page_records:
                        if record.cik not in seen_ciks:
                            seen_ciks.add(record.cik)
                            all_records.append(record)
                            new_count += 1

                    # If we got fewer results than the page size, we've reached the end.
                    if len(page_records) < page_size:
                        break

                    start += page_size
                    if settings.edgar_rate_limit_per_second > 0:
                        await asyncio.sleep(1 / settings.edgar_rate_limit_per_second)

        if len(all_records) < 100:
            logger.info(
                "EDGAR company-search returned only %d records — too few, falling back to bulk ZIP.",
                len(all_records),
            )
            return []

        return all_records

    # Regex patterns for parsing the browse-edgar company search results page.
    _COMPANY_ROW_PATTERN = re.compile(
        r'CIK=(?P<cik>\d+)[^>]*>(?P<cik_display>[^<]+)</a>\s*'
        r'<td[^>]*><a[^>]*>(?P<name>[^<]+)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    _COMPANY_TABLE_ROW_PATTERN = re.compile(
        r'<tr[^>]*>\s*<td[^>]*>\s*<a[^>]*CIK=(?P<cik>\d+)[^>]*>[^<]*</a>\s*</td>'
        r'\s*<td[^>]*>\s*<a[^>]*>(?P<name>[^<]+)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    _STATE_IN_ROW_PATTERN = re.compile(
        r'<td[^>]*>\s*(?P<state>[A-Z]{2})\s*</td>',
        re.IGNORECASE,
    )

    def _parse_company_search_page(
        self, page_html: str, sic_code: str,
    ) -> list[EdgarBrokerDealerRecord]:
        """Extract CIK + name pairs from the browse-edgar company-search HTML."""
        records: list[EdgarBrokerDealerRecord] = []

        # The browse-edgar page renders companies in an HTML table.
        # Try the more specific table-row pattern first.
        matches = list(self._COMPANY_TABLE_ROW_PATTERN.finditer(page_html))
        if not matches:
            matches = list(self._COMPANY_ROW_PATTERN.finditer(page_html))

        for match in matches:
            cik = match.group("cik").strip().zfill(10)
            name = html.unescape(match.group("name")).strip()
            if not cik or not name:
                continue

            # Try to extract state from the same row region.
            row_start = match.start()
            row_end = min(match.end() + 300, len(page_html))
            row_region = page_html[row_start:row_end]
            state_match = self._STATE_IN_ROW_PATTERN.search(row_region[match.end() - row_start:])
            state = state_match.group("state").upper() if state_match else None

            records.append(EdgarBrokerDealerRecord(
                cik=cik,
                name=name,
                sic=sic_code,
                state=state,
                city=None,
                sec_file_number=None,
                registration_date=None,
                last_filing_date=None,
                filings_index_url=f"{settings.sec_submissions_base_url}/CIK{cik}.json",
                sic_description="Security Brokers, Dealers & Flotation Companies",
            ))

        return records

    async def fetch_records_for_sec_numbers(self, sec_file_numbers: list[str]) -> list[EdgarBrokerDealerRecord]:
        bulk_records = await self.fetch_all_broker_dealers()
        bulk_by_sec = {
            normalized_sec: record
            for record in bulk_records
            if (normalized_sec := normalize_sec_file_number(record.sec_file_number)) is not None
        }

        requested_sec_numbers: list[str] = []
        seen_sec_numbers: set[str] = set()
        for raw_sec_number in sec_file_numbers:
            normalized = normalize_sec_file_number(raw_sec_number)
            if normalized and normalized not in seen_sec_numbers:
                requested_sec_numbers.append(normalized)
                seen_sec_numbers.add(normalized)

        resolved_records: list[EdgarBrokerDealerRecord] = []
        missing_sec_numbers: list[str] = []
        for sec_file_number in requested_sec_numbers:
            bulk_record = bulk_by_sec.get(sec_file_number)
            if bulk_record is not None:
                resolved_records.append(bulk_record)
            else:
                missing_sec_numbers.append(sec_file_number)

        if not missing_sec_numbers:
            return resolved_records

        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "text/html,application/xhtml+xml",
            # httpx auto-negotiates Accept-Encoding: gzip, deflate, br, zstd by default.
            # SEC EDGAR's Cloudflare gateway responds with malformed compressed bodies
            # that raise "Data-loss while decompressing corrupted data" on every request.
            # Forcing identity bypasses compression entirely (same fix as services/finra.py).
            "Accept-Encoding": "identity",
        }
        async with httpx.AsyncClient(
            timeout=settings.sec_request_timeout_seconds,
            headers=headers,
            follow_redirects=True,
        ) as client:
            for index, sec_file_number in enumerate(missing_sec_numbers):
                browse_record = await self._fetch_browse_record(client, sec_file_number)
                if browse_record is not None:
                    resolved_records.append(browse_record)

                if index < len(missing_sec_numbers) - 1 and settings.edgar_rate_limit_per_second > 0:
                    await asyncio.sleep(1 / settings.edgar_rate_limit_per_second)

        return resolved_records

    async def _ensure_bulk_submissions_zip(self, *, force_refresh: bool) -> Path:
        zip_path = self._resolve_project_path(settings.sec_bulk_submissions_zip_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        if not force_refresh and zip_path.exists() and zip_path.stat().st_size > 0:
            age_seconds = time.time() - zip_path.stat().st_mtime
            if age_seconds < _BULK_ZIP_TTL_SECONDS:
                return zip_path
            logger.info("Bulk submissions ZIP is %.1f days old — re-downloading.", age_seconds / 86400)

        temp_path = zip_path.with_suffix(f"{zip_path.suffix}.tmp")
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "application/zip,application/octet-stream;q=0.9,*/*;q=0.8",
            # Identity is the right hint to send, but SEC EDGAR's Akamai POPs that
            # serve GCP egress IPs ignore it and reply with Content-Encoding: gzip
            # anyway (consumer-ISP IPs see no Content-Encoding — verified via curl).
            # Keep the header for cases where Akamai does respect it.
            "Accept-Encoding": "identity",
        }

        async with httpx.AsyncClient(timeout=None, headers=headers, follow_redirects=True) as client:
            async with client.stream("GET", settings.sec_bulk_submissions_url) as response:
                response.raise_for_status()
                # aiter_raw, NOT aiter_bytes. The body is a .zip file (already
                # application-layer compressed); whatever Content-Encoding header
                # Akamai sets, we write the bytes verbatim and zipfile parses them
                # correctly. aiter_bytes auto-decompresses based on Content-Encoding
                # and surfaces ~1500 "Data-loss while decompressing corrupted data"
                # errors per 1.5 GB download from GCP egress IPs (one per chunk).
                with temp_path.open("wb") as handle:
                    async for chunk in response.aiter_raw(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)

        temp_path.replace(zip_path)
        return zip_path

    def _parse_bulk_submissions_zip(self, zip_path: Path, limit: int | None) -> list[EdgarBrokerDealerRecord]:
        target_sic_codes = {
            code.strip()
            for code in settings.edgar_target_sic_codes.split(",")
            if code.strip()
        }
        records: list[EdgarBrokerDealerRecord] = []

        with ZipFile(zip_path) as archive:
            members = sorted(
                member
                for member in archive.namelist()
                if member.lower().endswith(".json") and not member.endswith("/")
            )

            for member_name in members:
                if limit is not None and len(records) >= limit:
                    break

                with archive.open(member_name) as handle:
                    try:
                        payload = json.load(handle)
                    except json.JSONDecodeError:
                        continue

                record = self._build_record_from_submission(payload, target_sic_codes)
                if record is not None:
                    records.append(record)

        return records

    async def _fetch_browse_record(
        self,
        client: httpx.AsyncClient,
        sec_file_number: str,
    ) -> EdgarBrokerDealerRecord | None:
        params = {
            "action": "getcompany",
            "filenum": sec_file_number,
            "owner": "exclude",
            "count": 40,
        }
        last_error: Exception | None = None
        page = ""
        for attempt in range(1, settings.sec_request_max_retries + 1):
            try:
                response = await client.get("https://www.sec.gov/cgi-bin/browse-edgar", params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == settings.sec_request_max_retries:
                    break
                await asyncio.sleep(min(2**attempt, 30))
                continue

            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                try:
                    wait_seconds = float(retry_after) if retry_after else min(2**attempt, 60)
                except ValueError:
                    wait_seconds = min(2**attempt, 60)
                await asyncio.sleep(wait_seconds)
                last_error = httpx.HTTPStatusError(
                    "SEC browse-edgar rate limited the request.",
                    request=response.request,
                    response=response,
                )
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt == settings.sec_request_max_retries:
                    raise
                await asyncio.sleep(min(2**attempt, 8))
                continue

            page = response.text
            break

        if not page:
            if last_error is not None:
                logger.warning("SEC browse-edgar lookup failed for %s: %s", sec_file_number, last_error)
            return None

        if "No matching" in page:
            return None

        header_match = self.BROWSE_HEADER_PATTERN.search(page)
        if header_match is None:
            return None

        cik = header_match.group("cik").zfill(10)
        name = html.unescape(header_match.group("name")).strip()
        state_match = self.STATE_LOCATION_PATTERN.search(page) or self.STATE_OF_INC_PATTERN.search(page)
        state = state_match.group("state").strip().upper() if state_match else None

        filing_dates = [
            parsed
            for raw in self.FILING_DATE_PATTERN.findall(page)
            if (parsed := self._parse_date(raw)) is not None
        ]
        last_filing_date = max(filing_dates) if filing_dates else None

        return EdgarBrokerDealerRecord(
            cik=cik,
            name=name,
            sic="6211",
            state=state,
            city=None,
            sec_file_number=sec_file_number,
            registration_date=None,
            last_filing_date=last_filing_date,
            filings_index_url=f"{settings.sec_submissions_base_url}/CIK{cik}.json",
            sic_description="Security Brokers, Dealers & Flotation Companies",
        )

    def _build_record_from_submission(
        self,
        payload: dict[str, object],
        target_sic_codes: set[str],
    ) -> EdgarBrokerDealerRecord | None:
        cik = str(payload.get("cik") or "").strip().zfill(10)
        name = str(payload.get("name") or "").strip()
        sic = str(payload.get("sic") or "").strip()
        sic_description = str(payload.get("sicDescription") or "").strip() or None

        if not cik or not name or not sic or sic not in target_sic_codes:
            return None

        addresses = payload.get("addresses")
        business_address = addresses.get("business", {}) if isinstance(addresses, dict) else {}
        mailing_address = addresses.get("mailing", {}) if isinstance(addresses, dict) else {}
        address_source = business_address if business_address else mailing_address if mailing_address else {}

        state = self._clean_text(address_source.get("stateOrCountry") or payload.get("stateOfIncorporation"))
        city = self._clean_text(address_source.get("city"))

        filings_index_url = f"{settings.sec_submissions_base_url}/CIK{cik}.json"
        recent = self._get_recent_filings(payload)
        sec_file_number = self._extract_sec_file_number(recent)
        if sec_file_number is None:
            return None
        registration_date = self._extract_registration_date(recent)
        last_filing_date = self._extract_last_filing_date(recent)

        return EdgarBrokerDealerRecord(
            cik=cik,
            name=name,
            sic=sic,
            state=state,
            city=city,
            sec_file_number=sec_file_number,
            registration_date=registration_date,
            last_filing_date=last_filing_date,
            filings_index_url=filings_index_url,
            sic_description=sic_description,
        )

    def _get_recent_filings(self, payload: dict[str, object]) -> dict[str, list[object]]:
        filings = payload.get("filings")
        if not isinstance(filings, dict):
            return {}
        recent = filings.get("recent")
        if not isinstance(recent, dict):
            return {}
        return recent

    def _extract_sec_file_number(self, recent: dict[str, list[object]]) -> str | None:
        forms = recent.get("form", [])
        file_numbers = recent.get("fileNumber", [])

        for form, file_number in zip(forms, file_numbers, strict=False):
            normalized = normalize_sec_file_number(str(file_number) if file_number else None)
            if normalized and self._is_broker_dealer_form(str(form) if form else None):
                return normalized

        for file_number in file_numbers:
            normalized = normalize_sec_file_number(str(file_number) if file_number else None)
            if normalized:
                return normalized

        return None

    def _extract_registration_date(self, recent: dict[str, list[object]]) -> date | None:
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        candidate_dates: list[date] = []

        for form, filing_date in zip(forms, filing_dates, strict=False):
            if not self._is_registration_form(str(form) if form else None):
                continue
            parsed = self._parse_date(str(filing_date) if filing_date else None)
            if parsed is not None:
                candidate_dates.append(parsed)

        return min(candidate_dates) if candidate_dates else None

    def _extract_last_filing_date(self, recent: dict[str, list[object]]) -> date | None:
        filing_dates = recent.get("filingDate", [])
        parsed_dates = [parsed for raw in filing_dates if (parsed := self._parse_date(str(raw) if raw else None)) is not None]
        return max(parsed_dates) if parsed_dates else None

    def _is_broker_dealer_form(self, form: str | None) -> bool:
        if not form:
            return False
        normalized = form.strip().upper().replace("FORM ", "")
        return normalized.startswith("BD") or "X-17A-5" in normalized or "17A-11" in normalized or normalized == "17-A"

    def _is_registration_form(self, form: str | None) -> bool:
        if not form:
            return False
        normalized = form.strip().upper().replace("FORM ", "")
        return normalized == "BD" or normalized.startswith("BD/")

    def _parse_date(self, value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def _clean_text(self, value: object) -> str | None:
        cleaned = str(value or "").strip()
        return cleaned or None

    def _resolve_project_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.PROJECT_ROOT / path
