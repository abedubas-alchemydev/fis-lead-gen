from __future__ import annotations

import asyncio
import json

import httpx

import logging

from app.core.config import settings
from app.services.brokercheck_pdf import (
    FinraPdfFetchError,
    FormBdDetail,
    fetch_form_bd_detail,
)
from app.services.normalization import normalize_sec_file_number
from app.services.service_models import FinraBrokerDealerRecord
from app.services.website_resolver import is_blocklisted_host

logger = logging.getLogger(__name__)

# FINRA BrokerCheck detail endpoint base URL. The legacy /firm/{crd}
# path now 403s at Cloudflare even with realistic browser headers; the
# /search/firm/{crd} path returns the same payload shape and still works.
_FINRA_DETAIL_BASE_URL = "https://api.brokercheck.finra.org/search/firm"

# Browser-fingerprint headers required by FINRA's Cloudflare gateway.
# Captured from brokercheck.finra.org DevTools cURL. The full set must be
# sent together — partial fingerprints (e.g. UA only) still return 403.
BROKERCHECK_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    # httpx auto-negotiates Accept-Encoding: gzip, deflate, br, zstd by default.
    # FINRA's Cloudflare gateway responds with malformed compressed bodies that
    # raise "Data-loss while decompressing corrupted data" on every request.
    # Forcing identity bypasses compression entirely (browser cURL captures
    # also omit Accept-Encoding, which is why DevTools traffic worked).
    "Accept-Encoding": "identity",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://brokercheck.finra.org",
    "Priority": "u=1, i",
    "Referer": "https://brokercheck.finra.org/",
    "Sec-Ch-Ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}

# Solr query params required by /search/firm. ``query`` is overridden
# per-request (empty for detail-by-CRD, search term for enumeration).
BROKERCHECK_BASE_PARAMS = {
    "hl": "true",
    "nrows": "12",
    "query": "",
    "start": "0",
    "wt": "json",
}


class FinraService:
    # Alphabetical prefix queries ensure coverage of firms whose names don't
    # contain any of the keyword terms (e.g. "Apex", "Virtu", "Citadel").
    _ALPHA_PREFIXES = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + list("0123456789")

    async def fetch_broker_dealers(self, limit: int | None = None) -> list[FinraBrokerDealerRecord]:
        return await self._fetch_live_broker_dealers(limit=limit)

    async def _fetch_live_broker_dealers(self, limit: int | None = None) -> list[FinraBrokerDealerRecord]:
        records: list[FinraBrokerDealerRecord] = []
        seen_crd_numbers: set[str] = set()

        # Phase 1: keyword queries (broad coverage of common firm name patterns)
        keyword_terms = [query.strip() for query in settings.finra_harvest_queries.split(",") if query.strip()]
        # Phase 2: alphabetical prefix queries (catch firms missed by keywords).
        # Both phases stay as base queries; adaptive refinement (below) only ever
        # ADDS sub-queries, so single-char coverage — the only signal for firms
        # whose sole matching token is one character — is never dropped.
        all_queries = keyword_terms + self._ALPHA_PREFIXES

        delay = max(1.0 / settings.finra_rate_limit_per_second, settings.finra_request_delay_seconds)

        async with httpx.AsyncClient(
            timeout=settings.finra_request_timeout_seconds,
            follow_redirects=True,
            headers=BROKERCHECK_HEADERS,
        ) as client:
            for query_index, query in enumerate(all_queries):
                limit_reached = await self._harvest_query(
                    client,
                    query=query,
                    depth=1,
                    delay=delay,
                    limit=limit,
                    records=records,
                    seen_crd_numbers=seen_crd_numbers,
                )
                if limit_reached:
                    return records

                if query_index < len(all_queries) - 1 and delay > 0:
                    await asyncio.sleep(delay)

        return records

    async def _harvest_query(
        self,
        client: httpx.AsyncClient,
        *,
        query: str,
        depth: int,
        delay: float,
        limit: int | None,
        records: list[FinraBrokerDealerRecord],
        seen_crd_numbers: set[str],
    ) -> bool:
        """Page a single query (bounded by the safe pagination window) and,
        when its reported ``total`` exceeds that window, recurse into refined
        sub-queries to recover the truncated tail.

        BrokerCheck silently caps deep pagination (``start>=9000`` returns
        ``hits: null``), so any query whose total exceeds
        ``finra_pagination_safe_window`` can only surface its first ~8k rows.
        For those we append each ``finra_query_refine_charset`` character to
        the query string ("a" -> "aa".."a9") and recurse, up to
        ``finra_query_refine_max_depth`` levels (single -> double -> triple
        char, then stop). This is strictly additive: the base query is always
        fully paged first, and refined sub-queries only union in extra CRDs —
        the shared ``seen_crd_numbers`` set absorbs the heavy parent/child
        overlap.

        Returns ``True`` when the caller-supplied ``limit`` was reached, which
        unwinds the whole enumeration (preserving the original early-return).
        """
        total, limit_reached = await self._page_query(
            client,
            query=query,
            delay=delay,
            limit=limit,
            records=records,
            seen_crd_numbers=seen_crd_numbers,
        )
        if limit_reached:
            return True

        # Only refine queries that actually overflow the window, and only while
        # we still have recursion budget. Most queries fall well under the cap
        # (e.g. "e" ~= 4,139) and never spawn sub-queries.
        if (
            total is not None
            and total > settings.finra_pagination_safe_window
            and depth < settings.finra_query_refine_max_depth
        ):
            for char in settings.finra_query_refine_charset:
                if delay > 0:
                    await asyncio.sleep(delay)
                limit_reached = await self._harvest_query(
                    client,
                    query=query + char,
                    depth=depth + 1,
                    delay=delay,
                    limit=limit,
                    records=records,
                    seen_crd_numbers=seen_crd_numbers,
                )
                if limit_reached:
                    return True

        return False

    async def _page_query(
        self,
        client: httpx.AsyncClient,
        *,
        query: str,
        delay: float,
        limit: int | None,
        records: list[FinraBrokerDealerRecord],
        seen_crd_numbers: set[str],
    ) -> tuple[int | None, bool]:
        """Page one query from ``start=0`` up to the safe pagination window,
        appending new active broker-dealers (deduped by CRD) into ``records``.

        Returns ``(total, limit_reached)`` where ``total`` is the hit total
        reported on the query's first page (the caller uses it to decide
        whether to refine) and ``limit_reached`` is ``True`` when the
        caller-supplied ``limit`` was hit mid-page.
        """
        start = 0
        page_size = 100
        query_total: int | None = None

        while True:
            hits, total = await self._search(client, query=query, start=start, rows=page_size)
            if query_total is None:
                query_total = total
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
                    return query_total, True

            start += page_size
            if total is None or start >= total:
                break
            # BrokerCheck truncates deep pagination beyond ~8-9k hits; stop at
            # the safe window and let refined sub-queries recover the tail.
            if start > settings.finra_pagination_safe_window:
                break
            if delay > 0:
                await asyncio.sleep(delay)

        return query_total, False

    async def enrich_with_detail(
        self,
        records: list[FinraBrokerDealerRecord],
        *,
        batch_size: int = 50,
    ) -> list[FinraBrokerDealerRecord]:
        """Fetch the Form BD Detailed Report PDF for each record and pull
        ``types_of_business``, ``executive_officers``, and
        ``firm_operations_text`` (the clearing-classifier gate text) onto the
        record. Web Address is plucked opportunistically when the PDF
        carries one — the Form BD PDF rarely does, so the Apollo
        organizations fallback is what populates ``website`` for the bulk
        of firms.

        Why PDF, not JSON. The legacy ``/firm/{crd}`` JSON detail endpoint
        is gone behind Cloudflare, and the substitute (``/search/firm/{crd}``)
        returns a payload that no longer carries the Form BD fields. The
        deterministic Form BD PDF still does. See
        ``services/brokercheck_pdf.py`` for the full rationale + parser
        scope, and ``reports/finra-pdf-migration-blocker-2026-05-01.md``
        for the build-context decision (we inline rather than import the
        sibling ``brokercheck_extractor/`` package, which can't be reached
        from the backend Docker image).

        Per-firm budget: PDF download + 30-page extract + parse averages
        2-3 seconds. ~3500 firms × ~3s = ~3 hours wall clock for a full
        regen. Stays sequential for now (matches today's behavior + keeps
        FINRA happy without a custom rate-limiter); concurrency can be
        revisited if the wall clock starts to bite.
        """
        delay = max(1.0 / settings.finra_rate_limit_per_second, settings.finra_request_delay_seconds)
        total = len(records)

        for index, record in enumerate(records):
            if (index + 1) % 100 == 0 or index == 0:
                logger.info(
                    "FINRA detail enrichment: %d/%d processed.",
                    index + 1, total,
                )
            await self._enrich_record_from_pdf(record)
            if delay > 0 and index < total - 1:
                await asyncio.sleep(delay)

        return records

    async def _enrich_record_from_pdf(self, record: FinraBrokerDealerRecord) -> None:
        """Apply Form BD PDF fields to a single record.

        Failure semantics: a 404 from FINRA (no PDF on file for this CRD)
        leaves the record untouched. Transient upstream errors and parse
        exceptions log a warning and leave the record untouched — the
        existing record from the search-page enumeration stays as our best
        view of the firm, and the Apollo fallback still gets a chance at
        the website. We deliberately do NOT null fields the search-page
        gave us; that would silently throw away real data on a transient
        FINRA outage.
        """
        try:
            detail = await fetch_form_bd_detail(record.crd_number)
        except FinraPdfFetchError as exc:
            logger.warning(
                "FINRA Form BD PDF fetch failed for CRD %s: %s",
                record.crd_number, exc,
            )
            return
        except Exception as exc:  # noqa: BLE001 — parse failures must not abort the loop
            logger.warning(
                "FINRA Form BD PDF parse failed for CRD %s: %s",
                record.crd_number, exc,
            )
            return

        if detail is None:
            return

        self._apply_form_bd_detail(record, detail)

    @staticmethod
    def _apply_form_bd_detail(
        record: FinraBrokerDealerRecord,
        detail: FormBdDetail,
    ) -> None:
        """Stamp Form BD fields onto a record without overwriting real values
        with parser-empties. Each field guards on a truthy value from the
        PDF — the search-page enumeration's data stays in place when the
        PDF parser couldn't recover a section."""
        if detail.types_of_business:
            record.types_of_business = detail.types_of_business
        if detail.executive_officers:
            record.executive_officers = detail.executive_officers
        if detail.firm_operations_text:
            record.firm_operations_text = detail.firm_operations_text
        if (
            detail.web_address
            and not record.website
            and not is_blocklisted_host(detail.web_address)
        ):
            record.website = detail.web_address
            record.website_source = "finra"
        if detail.types_of_business_other:
            record.types_of_business_other = detail.types_of_business_other
        if detail.registration_date:
            record.registration_date = detail.registration_date
        if detail.formation_date:
            record.formation_date = detail.formation_date

    async def fetch_firm_search_metadata(
        self,
        crd_number: str,
    ) -> dict[str, object] | None:
        """Fetch fresh ``branch_count`` + ``business_type`` for a single firm.

        These two fields come off FINRA's BrokerCheck *search* payload —
        not the Form BD PDF that ``enrich_with_detail`` parses, and not
        the substitute ``/search/firm/{crd}`` endpoint
        :meth:`fetch_website_by_crd` uses (Cloudflare-era replacement
        which dropped the Form BD fields). The original keyword/alpha
        search endpoint :meth:`_search` still surfaces ``firm_branches_count``
        and ``firm_type``, so this method runs a CRD-targeted search and
        builds a fresh metadata dict from the matching hit.

        Returns a ``{"branch_count": int|None, "business_type": str|None}``
        dict on success, or ``None`` if FINRA has no active hit for the
        CRD or the call fails after retries. Either entry value can be
        ``None`` when the source field is missing — caller should apply
        only truthy / non-None values to avoid overwriting a present
        BD column with a transient miss.

        Free + cheap (~1 HTTP call, FINRA rate-limited at 2 req/s).
        Opens its own ``httpx.AsyncClient`` so the orchestrator's
        per-firm runner can call it without managing a client.
        """
        async with httpx.AsyncClient(
            timeout=settings.finra_request_timeout_seconds,
            follow_redirects=True,
            headers=BROKERCHECK_HEADERS,
        ) as client:
            try:
                hits, _ = await self._search(
                    client, query=str(crd_number), start=0, rows=20
                )
            except (RuntimeError, httpx.HTTPError):
                return None

            for hit in hits:
                source = hit.get("_source") or hit.get("source")
                if not isinstance(source, dict):
                    continue
                if str(source.get("firm_source_id") or "").strip() != str(crd_number).strip():
                    continue

                branch_count_raw = source.get("firm_branches_count")
                try:
                    branch_count: int | None = (
                        int(branch_count_raw) if branch_count_raw is not None else None
                    )
                except (TypeError, ValueError):
                    branch_count = None

                business_type = self._clean_text(
                    source.get("firm_ia_full_sec_number")
                    or source.get("firm_type")
                )

                return {
                    "branch_count": branch_count,
                    "business_type": business_type,
                }

            return None

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
        website = self._clean_text(
            source.get("firm_ia_main_web_address")
            or source.get("firm_main_web_address")
            or source.get("firm_web_address")
            or source.get("firm_website")
        )
        if website and is_blocklisted_host(website):
            return None
        return website

    async def _fetch_firm_detail(
        self,
        client: httpx.AsyncClient,
        crd_number: str,
    ) -> dict[str, object] | None:
        """Fetch the FINRA BrokerCheck detail page for a single firm."""
        url = f"{_FINRA_DETAIL_BASE_URL}/{crd_number}"
        for attempt in range(1, settings.finra_request_max_retries + 1):
            try:
                response = await client.get(url, params=BROKERCHECK_BASE_PARAMS)
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
        # snake-cased Form BD field; ``firm_main_web_address`` /
        # ``firm_web_address`` / ``firm_website`` show up as legacy variants.
        # ``firm_bc_scope_url`` is intentionally NOT in the chain — that field
        # is FINRA's pointer to the firm's own BrokerCheck profile/PDF (e.g.
        # ``files.brokercheck.finra.org/firm/firm_<CRD>.pdf``), not a website
        # the firm filed. Persisting it short-circuits the on-demand resolver
        # (which only fires when ``website IS NULL``) and renders as a
        # FINRA URL on the firm-detail page.
        website = self._clean_text(
            source.get("firm_ia_main_web_address")
            or source.get("firm_main_web_address")
            or source.get("firm_web_address")
            or source.get("firm_website")
        )
        # Defensive: even the canonical Form BD key occasionally carries a
        # FINRA/SEC self-reference for firms that didn't file a real website.
        # Blocklisted hosts are never persisted; the lazy
        # Apollo→Hunter→SerpAPI resolver runs on first visit instead.
        if website and not is_blocklisted_host(website):
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
            "wt": "json",
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

        # ``firm_other_names`` is the FINRA "doing business as" / alternate
        # trade-name field. It used to be conflated with ``business_type``
        # as a fallback alongside ``firm_ia_full_sec_number`` / ``firm_type``,
        # which discarded DBA data entirely. Parse it on its own so the
        # website resolver can anchor candidate URLs on the trade name when
        # the legal LLC name doesn't match the firm's brand domain
        # (canonical case: ``303 ALTERNATIVES, LLC`` operating at
        # ``303capitalmarkets.com``).
        dba_names = self._parse_dba_names(
            source.get("firm_other_names"), legal_name=name,
        )

        business_type = self._clean_text(
            source.get("firm_ia_full_sec_number")
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
            dba_names=dba_names,
        )

    @staticmethod
    def _parse_dba_names(
        raw: object, *, legal_name: str
    ) -> list[str] | None:
        """Normalize FINRA's "other names" payload into a list of DBAs.

        FINRA exposes the same DBA data under two different shapes
        depending on which endpoint surfaced it:

        - **Search endpoint** (``_search`` → ``_build_record``): a
          string at top-level ``firm_other_names``. Format varies:
          single name, semicolon-delimited, newline-delimited, or
          ``"d/b/a <trade-name>"`` markers. Comma split is avoided
          because legitimate LLC suffixes carry internal commas.
        - **Detail endpoint** (``_fetch_firm_detail`` →
          ``content.basicInformation.otherNames``): already a list of
          strings, one entry per name. The list typically includes the
          firm's own legal name as the first entry.

        Both shapes pass through here. Returns ``None`` when nothing
        usable remains. Drops items that match the firm's legal name
        (case- and whitespace-insensitive) and de-dupes (same
        normalization).
        """
        if raw is None:
            return None

        # Build the raw item list, regardless of input shape.
        items: list[str]
        if isinstance(raw, list):
            items = [str(x).strip() for x in raw if str(x).strip()]
        else:
            text = str(raw).strip()
            if not text:
                return None
            # Split on the unambiguous delimiters (semicolons and
            # newlines). Comma split is intentionally avoided — many
            # legitimate firm names carry an internal ``, LLC`` /
            # ``, INC`` / ``, L.P.`` suffix.
            items = []
            for chunk in text.replace("\r", "\n").split("\n"):
                for sub in chunk.split(";"):
                    cleaned = sub.strip()
                    if cleaned:
                        items.append(cleaned)

        # Per-item: strip a leading ``d/b/a`` / ``DBA`` prefix.
        cleaned_items: list[str] = []
        for item in items:
            lower = item.lower()
            for marker in ("d/b/a ", "dba "):
                if lower.startswith(marker):
                    item = item[len(marker):].strip()
                    break
            if item:
                cleaned_items.append(item)

        # De-dupe (case-insensitive) and drop legal-name matches.
        legal_norm = " ".join(legal_name.lower().split())
        seen: set[str] = set()
        out: list[str] = []
        for p in cleaned_items:
            norm = " ".join(p.lower().split())
            if not norm or norm == legal_norm or norm in seen:
                continue
            seen.add(norm)
            out.append(p)
        return out or None

    @staticmethod
    def extract_dba_names_from_detail(
        detail: dict[str, object] | None, *, legal_name: str
    ) -> list[str] | None:
        """Parse DBA names out of a ``_fetch_firm_detail`` response.

        The detail endpoint nests trade names inside a JSON-encoded
        ``content`` string at ``content.basicInformation.otherNames``.
        Concretely for CRD 166675 (303 ALTERNATIVES, LLC):

            {
              "_source": {
                "content": "{\"basicInformation\": {\"firmName\":
                  \"303 ALTERNATIVES, LLC\", \"otherNames\": [\"303
                  ALTERNATIVES, LLC\", \"303 CAPITAL MARKETS, LLC\"]}}"
              }
            }

        Returns the DBA list (with legal name dropped + dedupe) or
        ``None`` when the path is absent / empty / unparseable.
        """
        if not detail:
            return None
        source = FinraService._extract_detail_source_static(detail)
        if not isinstance(source, dict):
            return None
        content_raw = source.get("content")
        if not isinstance(content_raw, str) or not content_raw.strip():
            return None
        try:
            content = json.loads(content_raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(content, dict):
            return None
        basic_info = content.get("basicInformation")
        if not isinstance(basic_info, dict):
            return None
        other_names = basic_info.get("otherNames")
        return FinraService._parse_dba_names(other_names, legal_name=legal_name)

    @staticmethod
    def _extract_detail_source_static(
        detail: dict[str, object],
    ) -> dict[str, object] | None:
        """Static mirror of ``_extract_detail_source`` so the static
        ``extract_dba_names_from_detail`` helper doesn't need an
        instance to navigate the ``hits.hits[0]._source`` envelope."""
        if "firm_name" in detail or "firm_source_id" in detail or "content" in detail:
            return detail  # type: ignore[return-value]
        hits_container = detail.get("hits")
        if isinstance(hits_container, dict):
            hits = hits_container.get("hits", [])
            if isinstance(hits, list) and hits:
                first = hits[0]
                if isinstance(first, dict):
                    source = first.get("_source") or first.get("source")
                    if isinstance(source, dict):
                        return source
        return None

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
