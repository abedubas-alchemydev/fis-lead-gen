from __future__ import annotations

import asyncio
import json

import httpx

import logging

from app.core.config import settings
from app.services.normalization import normalize_sec_file_number
from app.services.service_models import FinraBrokerDealerRecord

logger = logging.getLogger(__name__)

# FINRA BrokerCheck detail endpoint base URL.
_FINRA_DETAIL_BASE_URL = "https://api.brokercheck.finra.org/firm"


class FinraService:
    # Alphabetical prefix queries ensure coverage of firms whose names don't
    # contain any of the keyword terms (e.g. "Apex", "Virtu", "Citadel").
    _ALPHA_PREFIXES = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + list("0123456789")

    async def fetch_broker_dealers(self, limit: int | None = None) -> list[FinraBrokerDealerRecord]:
        return await self._fetch_live_broker_dealers(limit=limit)

    async def _fetch_live_broker_dealers(self, limit: int | None = None) -> list[FinraBrokerDealerRecord]:
        records: list[FinraBrokerDealerRecord] = []
        seen_crd_numbers: set[str] = set()
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; AlchemyDev/1.0; compliance@alchemy.dev)",
        }

        # Phase 1: keyword queries (broad coverage of common firm name patterns)
        keyword_terms = [query.strip() for query in settings.finra_harvest_queries.split(",") if query.strip()]
        # Phase 2: alphabetical prefix queries (catch firms missed by keywords)
        all_queries = keyword_terms + self._ALPHA_PREFIXES

        delay = max(1.0 / settings.finra_rate_limit_per_second, settings.finra_request_delay_seconds)

        async with httpx.AsyncClient(
            timeout=settings.finra_request_timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for query_index, query in enumerate(all_queries):
                start = 0
                page_size = 100

                while True:
                    hits, total = await self._search(client, query=query, start=start, rows=page_size)
                    if not hits:
                        break

                    for hit in hits:
                        source = hit.get("_source") or hit.get("source")
                        if not isinstance(source, dict):
                            continue

                        record = self._build_record(source)
                        if record is None:
                            continue
                        if record.registration_status.lower() != "active":
                            continue
                        if record.crd_number in seen_crd_numbers:
                            continue

                        records.append(record)
                        seen_crd_numbers.add(record.crd_number)

                        if limit is not None and len(records) >= limit:
                            return records

                    start += page_size
                    if total is None or start >= total:
                        break
                    if delay > 0:
                        await asyncio.sleep(delay)

                if query_index < len(all_queries) - 1 and delay > 0:
                    await asyncio.sleep(delay)

        return records

    async def enrich_with_detail(
        self,
        records: list[FinraBrokerDealerRecord],
        *,
        batch_size: int = 50,
    ) -> list[FinraBrokerDealerRecord]:
        """Fetch detailed FINRA reports for each record to populate Stream A fields.

        Hits ``/firm/{crd_number}`` for each record and extracts:
        - Types of Business
        - Direct Owners & Executive Officers
        - Firm Operations text (for clearing classification logic gates)
        - Website URL
        """
        delay = max(1.0 / settings.finra_rate_limit_per_second, settings.finra_request_delay_seconds)
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; AlchemyDev/1.0; compliance@alchemy.dev)",
        }
        total = len(records)

        async with httpx.AsyncClient(
            timeout=settings.finra_request_timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for index, record in enumerate(records):
                if (index + 1) % 100 == 0 or index == 0:
                    logger.info(
                        "FINRA detail enrichment: %d/%d processed.",
                        index + 1, total,
                    )
                detail = await self._fetch_firm_detail(client, record.crd_number)
                if detail is not None:
                    self._apply_detail_to_record(record, detail)
                if delay > 0 and index < total - 1:
                    await asyncio.sleep(delay)

        return records

    async def fetch_website_by_crd(
        self,
        client: httpx.AsyncClient,
        crd_number: str,
    ) -> str | None:
        """Fetch the Form BD "Web Address" for a single CRD.

        Thin public wrapper around :meth:`_fetch_firm_detail` plus the
        same Form-BD-canonical key list that ``_apply_detail_to_record``
        uses. Built for the website backfill (see
        ``scripts/backfill_firm_websites.py``) so the one-shot script
        reuses the live field-name list instead of duplicating it.
        Returns None when the firm has no Web Address on file or the
        detail fetch failed (network / 5xx after retries).
        """
        detail = await self._fetch_firm_detail(client, crd_number)
        if detail is None:
            return None
        source = self._extract_detail_source(detail)
        if source is None:
            return None
        return self._clean_text(
            source.get("firm_ia_main_web_address")
            or source.get("firm_main_web_address")
            or source.get("firm_web_address")
            or source.get("firm_website")
            or source.get("firm_bc_scope_url")
        )

    async def _fetch_firm_detail(
        self,
        client: httpx.AsyncClient,
        crd_number: str,
    ) -> dict[str, object] | None:
        """Fetch the FINRA BrokerCheck detail page for a single firm."""
        url = f"{_FINRA_DETAIL_BASE_URL}/{crd_number}"
        for attempt in range(1, settings.finra_request_max_retries + 1):
            try:
                response = await client.get(url)
                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after")
                    wait = float(retry_after) if retry_after else min(2**attempt, 30)
                    await asyncio.sleep(wait)
                    continue
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else None
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == settings.finra_request_max_retries:
                    logger.warning("FINRA detail fetch failed for CRD %s: %s", crd_number, exc)
                    return None
                await asyncio.sleep(min(2**attempt, 8))
        return None

    def _apply_detail_to_record(
        self,
        record: FinraBrokerDealerRecord,
        detail: dict[str, object],
    ) -> None:
        """Extract Stream A fields from the FINRA detail JSON and apply to the record."""
        # Navigate the FINRA response structure.
        # The response is typically: { "hits": { "hits": [ { "_source": { ... } } ] } }
        source = self._extract_detail_source(detail)
        if source is None:
            return

        # Website. The BrokerCheck Form BD "Web Address" field surfaces under
        # several keys depending on the search vs. detail endpoint and how the
        # firm filed Form BD: ``firm_ia_main_web_address`` is the canonical
        # snake-cased Form BD field, ``firm_main_web_address`` /
        # ``firm_web_address`` show up on some firms, and ``firm_website`` /
        # ``firm_bc_scope_url`` were the original keys we plucked. Try the
        # Form-BD-canonical ones first so production rows are mostly populated
        # straight from FINRA without needing the Apollo fallback.
        website = self._clean_text(
            source.get("firm_ia_main_web_address")
            or source.get("firm_main_web_address")
            or source.get("firm_web_address")
            or source.get("firm_website")
            or source.get("firm_bc_scope_url")
        )
        if website:
            record.website = website
            record.website_source = "finra"

        # Types of Business - stored as JSON string in the detail payload
        business_types = self._parse_business_types(source)
        if business_types:
            record.types_of_business = business_types

        # Direct Owners
        owners = self._parse_owners(source, key="firm_direct_owners")
        if owners:
            record.direct_owners = owners

        # Executive Officers
        officers = self._parse_owners(source, key="firm_executive_officers")
        if not officers:
            officers = self._parse_owners(source, key="firm_control_persons")
        if officers:
            record.executive_officers = officers

        # Firm Operations text (for clearing classification gates)
        operations_text = self._parse_firm_operations(source)
        if operations_text:
            record.firm_operations_text = operations_text

    def _extract_detail_source(self, detail: dict[str, object]) -> dict[str, object] | None:
        """Unwrap the FINRA detail response to get the firm source dict."""
        # Direct source fields at root level
        if "firm_name" in detail or "firm_source_id" in detail:
            return detail
        # Nested hits structure
        hits_container = detail.get("hits")
        if isinstance(hits_container, dict):
            hits = hits_container.get("hits", [])
            if isinstance(hits, list) and hits:
                first = hits[0]
                source = first.get("_source") or first.get("source")
                if isinstance(source, dict):
                    return source
        return None

    def _parse_business_types(self, source: dict[str, object]) -> list[str] | None:
        """Extract the types_of_business list from FINRA detail data."""
        # The detail payload may include this as a JSON-encoded string or a list.
        raw = source.get("firm_bd_types_of_business") or source.get("firm_types_of_business")
        if raw is None:
            return None
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str):
            raw = raw.strip()
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
            # Fallback: split on common delimiters
            items = [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]
            return items if items else None
        return None

    def _parse_owners(
        self,
        source: dict[str, object],
        *,
        key: str,
    ) -> list[dict[str, str]] | None:
        """Parse direct owners or executive officers from FINRA detail data."""
        raw = source.get(key)
        if raw is None:
            return None
        entries: list[dict[str, object]] = []
        if isinstance(raw, list):
            entries = raw
        elif isinstance(raw, str):
            raw = raw.strip()
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        entries = parsed
                except json.JSONDecodeError:
                    return None
            else:
                return None
        else:
            return None

        results: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = self._clean_text(
                entry.get("name") or entry.get("full_name") or entry.get("firstName", "")
            )
            if not name:
                # Try combining first/last name fields
                first = str(entry.get("firstName") or entry.get("first_name") or "").strip()
                last = str(entry.get("lastName") or entry.get("last_name") or "").strip()
                name = f"{first} {last}".strip() or None
            if not name:
                continue
            title = self._clean_text(
                entry.get("title") or entry.get("position") or entry.get("officerTitle") or ""
            )
            result: dict[str, str] = {"name": name}
            if title:
                result["title"] = title
            ownership_pct = self._clean_text(entry.get("ownershipPercentage") or entry.get("ownership_pct"))
            if ownership_pct:
                result["ownership_pct"] = ownership_pct
            results.append(result)
        return results if results else None

    def _parse_firm_operations(self, source: dict[str, object]) -> str | None:
        """Extract the firm operations / clearing arrangement text from FINRA detail.

        This text is used by the Self-Clearing and Introducing logic gates.
        It typically contains phrases like:
        - "This firm does not hold or maintain funds or securities..."
        - "This firm does refer or introduce customers..."
        """
        # Try known field names for the operations section
        for field_name in (
            "firm_bd_firm_operations",
            "firm_operations",
            "firm_clearing_arrangements",
            "firm_bd_clearing",
            "firm_scope_details",
        ):
            raw = source.get(field_name)
            if raw and isinstance(raw, str) and len(raw.strip()) > 20:
                return raw.strip()
            if isinstance(raw, dict):
                # Sometimes operations is a nested object
                text_parts = []
                for value in raw.values():
                    if isinstance(value, str) and value.strip():
                        text_parts.append(value.strip())
                combined = " ".join(text_parts)
                if len(combined) > 20:
                    return combined
        return None

    async def _search(
        self,
        client: httpx.AsyncClient,
        *,
        query: str,
        start: int,
        rows: int,
    ) -> tuple[list[dict[str, object]], int | None]:
        if not query.strip():
            return [], None

        params = {
            "query": query,
            "filter": "active=true",
            "nrows": rows,
            "start": start,
            "hl": "true",
        }
        max_attempts = settings.finra_request_max_retries
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.get(settings.finra_search_base_url, params=params)

                # Handle rate limiting with Retry-After support
                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after")
                    try:
                        wait_seconds = float(retry_after) if retry_after else min(2**attempt, 30)
                    except ValueError:
                        wait_seconds = min(2**attempt, 30)
                    last_error = httpx.HTTPStatusError(
                        "FINRA BrokerCheck rate limited the request.",
                        request=response.request,
                        response=response,
                    )
                    if attempt < max_attempts:
                        await asyncio.sleep(wait_seconds)
                        continue
                    raise RuntimeError(f"FINRA BrokerCheck rate limited after {max_attempts} retries for query '{query}'.") from last_error

                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    return [], None
                hits_container = payload.get("hits", {})
                if not isinstance(hits_container, dict):
                    return [], None
                hits = hits_container.get("hits", [])
                total = hits_container.get("total")
                return (hits if isinstance(hits, list) else []), int(total) if isinstance(total, int) else None
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code in {500, 502, 503, 504} and attempt < max_attempts:
                    await asyncio.sleep(min(2**attempt, 8))
                    continue
                raise RuntimeError(f"FINRA BrokerCheck lookup failed for query '{query}'.") from exc
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt == max_attempts:
                    raise RuntimeError(f"FINRA BrokerCheck lookup failed for query '{query}'.") from exc
                await asyncio.sleep(min(2**attempt, 4))

        raise RuntimeError(f"FINRA BrokerCheck lookup failed for query '{query}'.") from last_error

    def _build_record(self, source: dict[str, object]) -> FinraBrokerDealerRecord | None:
        crd_number = str(source.get("firm_source_id") or "").strip()
        name = str(source.get("firm_name") or "").strip()
        if not crd_number or not name:
            return None

        address_details_raw = source.get("firm_address_details")
        address_details = self._parse_address_details(address_details_raw)
        office_address = address_details.get("officeAddress", {}) if isinstance(address_details, dict) else {}
        mailing_address = address_details.get("mailingAddress", {}) if isinstance(address_details, dict) else {}
        address_source = office_address if office_address else mailing_address if mailing_address else {}

        branch_count_raw = source.get("firm_branches_count")
        try:
            branch_count = int(branch_count_raw) if branch_count_raw is not None else None
        except (TypeError, ValueError):
            branch_count = None

        sec_file_number = normalize_sec_file_number(
            str(source.get("firm_bd_full_sec_number") or source.get("firm_bd_sec_number") or "").strip()
        )
        if sec_file_number is None:
            return None

        business_type = self._clean_text(
            source.get("firm_ia_full_sec_number")
            or source.get("firm_other_names")
            or source.get("firm_type")
        )

        return FinraBrokerDealerRecord(
            crd_number=crd_number,
            name=name,
            sec_file_number=sec_file_number,
            registration_status=str(source.get("firm_scope") or "UNKNOWN").strip().title(),
            branch_count=branch_count,
            address_city=self._clean_text(address_source.get("city")),
            address_state=self._clean_text(address_source.get("state")),
            business_type=business_type,
        )

    def _parse_address_details(self, value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            payload = json.loads(value)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _clean_text(self, value: object) -> str | None:
        cleaned = str(value or "").strip()
        return cleaned or None
