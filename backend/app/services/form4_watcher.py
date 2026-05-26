"""SEC Form 4 (insider transactions) watcher.

Powers the Investors tab. Daily cron query against SEC EDGAR's
full-text search (EFTS) endpoint for Form 4 filings in the past
``settings.form4_lookback_days`` days. Each hit's primary XML is
streamed, parsed, and emitted as one ``Form4TransactionRecord`` per
(reportingOwner × transaction) pair. Rows with a computed
``transaction_value`` below ``settings.form4_min_transaction_value``
are dropped — this is what naturally filters out the noise from
grants, gifts, RSU vests, and other zero-price events.

Why EFTS rather than per-issuer ``submissions.json`` enumeration:

The submissions JSON path only works if we already know which CIKs
to watch. The Investors tab is scoped to *all* EDGAR public
companies (no BD pre-filter — see plan doc + meeting transcript),
which is thousands of issuers. A single EFTS query against
``forms=4`` returns every new Form 4 across the whole universe in
one paged sweep, which is cheap (under 60 seconds end-to-end on a
typical day at the 10 req/sec ceiling).

Idempotency: the ``form4_transactions.dedupe_key`` UNIQUE index plus
``ON CONFLICT DO NOTHING`` in the repository let us re-run the
watcher freely. The 7-day EFTS lookback window deliberately overlaps
prior runs so late-filed Form 4s (which the SEC commonly ingests
1-2 business days after the transaction date) are picked up without
a separate backfill pass.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.pipeline_run import PipelineRun
from app.services.form4_transactions import Form4TransactionRepository
from app.services.form4_xml_parser import (
    ParsedForm4Filing,
    ParsedReportingOwner,
    ParsedTransaction,
    parse_form4_xml,
)
from app.services.service_models import Form4TransactionRecord

logger = logging.getLogger(__name__)


_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
_PAGE_SIZE = 100
_HTTP_DELAY_SECONDS = 0.12  # ≈8 req/sec, comfortably under SEC's 10/sec ceiling


class Form4WatcherService:
    """Tier 2 cron watcher. Designed for daily invocation."""

    def __init__(self) -> None:
        self.repository = Form4TransactionRepository()

    async def run(
        self,
        db: AsyncSession,
        *,
        trigger_source: str = "manual",
    ) -> PipelineRun:
        run = PipelineRun(
            pipeline_name="form4_watcher",
            trigger_source=trigger_source,
            status="running",
            total_items=0,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes="Streaming SEC EDGAR Form 4 filings into form4_transactions.",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

        min_value = Decimal(str(settings.form4_min_transaction_value))
        lookback = max(1, settings.form4_lookback_days)

        hits: list[dict[str, Any]] = []
        records: list[Form4TransactionRecord] = []
        failures = 0

        try:
            async with httpx.AsyncClient(
                timeout=settings.sec_request_timeout_seconds,
                headers={
                    "User-Agent": settings.sec_user_agent,
                    "Accept": "application/json",
                },
                follow_redirects=True,
            ) as client:
                hits = await self._fetch_efts_hits(client, lookback_days=lookback)

                for hit in hits:
                    try:
                        per_hit_records = await self._process_hit(
                            client, hit, min_value=min_value
                        )
                    except Exception as exc:  # noqa: BLE001
                        failures += 1
                        logger.warning(
                            "Form 4 hit failed (adsh=%s): %s",
                            (hit.get("_source") or {}).get("adsh"),
                            exc,
                        )
                        continue
                    records.extend(per_hit_records)
                    await asyncio.sleep(_HTTP_DELAY_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Form 4 watcher run %d failed: %s", run_id, exc)
            async with SessionLocal() as fail_db:
                fail_run = await fail_db.get(PipelineRun, run_id)
                if fail_run is not None:
                    fail_run.status = "failed"
                    fail_run.completed_at = datetime.now(timezone.utc)
                    fail_run.notes = f"Watcher aborted: {exc}"
                    await fail_db.commit()
                    await fail_db.refresh(fail_run)
                    return fail_run
            raise

        # Upsert + finalize gets its own try/except: a chunked upsert that
        # bombs mid-flight (Postgres param ceiling, transient Neon error)
        # should mark the PipelineRun as failed/partial, not propagate a
        # 500 to the Cloud Scheduler caller which would burn retries on a
        # poison batch.
        try:
            async with SessionLocal() as write_db:
                inserted = await self.repository.upsert_many(write_db, records)
                await write_db.commit()

                run = await write_db.get(PipelineRun, run_id)
                if run is None:
                    raise RuntimeError(
                        f"Pipeline run {run_id} could not be reloaded for "
                        "form4 watcher finalization."
                    )
                run.total_items = len(hits)
                run.processed_items = len(records)
                run.success_count = inserted
                run.failure_count = failures
                run.status = "completed" if failures == 0 else "completed_with_errors"
                run.completed_at = datetime.now(timezone.utc)
                run.notes = (
                    f"Scanned {len(hits)} Form 4 filing(s) over the last "
                    f"{lookback} day(s); built {len(records)} record(s); "
                    f"inserted {inserted} new row(s); {failures} hit(s) failed."
                )
                await write_db.commit()
                await write_db.refresh(run)
                logger.info(
                    "Form 4 watcher run %d: %d hits, %d records, %d inserted, %d failures.",
                    run.id,
                    len(hits),
                    len(records),
                    inserted,
                    failures,
                )
                return run
        except Exception as exc:  # noqa: BLE001
            logger.exception("Form 4 watcher upsert failed for run %d: %s", run_id, exc)
            async with SessionLocal() as fail_db:
                fail_run = await fail_db.get(PipelineRun, run_id)
                if fail_run is not None:
                    fail_run.status = "failed"
                    fail_run.completed_at = datetime.now(timezone.utc)
                    fail_run.total_items = len(hits)
                    fail_run.processed_items = len(records)
                    fail_run.failure_count = failures + 1
                    fail_run.notes = (
                        f"Upsert failed after building {len(records)} record(s) "
                        f"from {len(hits)} hit(s): {exc}"
                    )
                    await fail_db.commit()
                    await fail_db.refresh(fail_run)
                    return fail_run
            raise

    async def _fetch_efts_hits(
        self,
        client: httpx.AsyncClient,
        *,
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        """Fetch Form 4 EFTS hits across the lookback window.

        EFTS pagination is backed by Elasticsearch with a per-query
        ``from + size <= 10000`` ceiling, and in practice SEC starts
        500'ing well before that on a busy form code: a 7-day Form 4
        query returns ~2500-3500 hits and consistently 500s around
        ``from=2600``. To stay well clear of both ceilings we partition
        the query by **single day** — each day produces ~300-500 Form 4s
        which pages cleanly. The partition also lets a single bad day
        fail without aborting the whole window.
        """
        end = date.today()
        all_hits: list[dict[str, Any]] = []
        for offset in range(lookback_days):
            day = end - timedelta(days=offset)
            try:
                day_hits = await self._fetch_efts_hits_for_day(client, day)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Form 4 EFTS fetch failed for %s: %s. Continuing with remaining days.",
                    day.isoformat(),
                    exc,
                )
                continue
            all_hits.extend(day_hits)
            await asyncio.sleep(_HTTP_DELAY_SECONDS)
        return all_hits

    async def _fetch_efts_hits_for_day(
        self,
        client: httpx.AsyncClient,
        day: date,
    ) -> list[dict[str, Any]]:
        """Page through EFTS for ``forms=4`` filings on a single date.

        Per-page errors (transient SEC 5xx, rate-limiting) abort the
        current day but bubble up — the day-level loop catches them.
        """
        params: dict[str, Any] = {
            "forms": "4",
            "dateRange": "custom",
            "startdt": day.isoformat(),
            "enddt": day.isoformat(),
            "from": 0,
            "size": _PAGE_SIZE,
        }
        day_hits: list[dict[str, Any]] = []
        cursor = 0
        while True:
            params["from"] = cursor
            response = await client.get(_EFTS_URL, params=params)
            response.raise_for_status()
            payload = response.json()
            hits_block = payload.get("hits", {})
            page_hits = hits_block.get("hits", []) if isinstance(hits_block, dict) else []
            if not page_hits:
                break
            day_hits.extend(page_hits)
            cursor += len(page_hits)
            total = hits_block.get("total", {})
            total_value = (
                total.get("value")
                if isinstance(total, dict)
                else (total if isinstance(total, int) else 0)
            ) or 0
            if cursor >= total_value or len(page_hits) < _PAGE_SIZE:
                break
            await asyncio.sleep(_HTTP_DELAY_SECONDS)
        return day_hits

    async def _process_hit(
        self,
        client: httpx.AsyncClient,
        hit: dict[str, Any],
        *,
        min_value: Decimal,
    ) -> list[Form4TransactionRecord]:
        source = hit.get("_source") or {}
        accession = source.get("adsh")
        if not isinstance(accession, str) or not accession:
            return []
        ciks = source.get("ciks") or []
        if not ciks:
            return []

        filing_cik = str(ciks[0]).lstrip("0") or "0"
        file_date_raw = source.get("file_date") or source.get("filed_at")
        try:
            file_date = date.fromisoformat(file_date_raw) if isinstance(file_date_raw, str) else date.today()
        except ValueError:
            file_date = date.today()
        filed_at = datetime.combine(file_date, time(hour=12), tzinfo=timezone.utc)

        accession_no_dashes = accession.replace("-", "")
        primary_doc_url = _primary_doc_url_from_hit_id(
            hit_id=hit.get("_id"),
            filing_cik=filing_cik,
            accession_no_dashes=accession_no_dashes,
        )
        if primary_doc_url is None:
            primary_doc_url = await self._resolve_primary_xml_url(
                client, filing_cik=filing_cik, accession_no_dashes=accession_no_dashes
            )
        if primary_doc_url is None:
            return []

        xml_bytes = await self._fetch_bytes(client, primary_doc_url)
        if xml_bytes is None:
            return []
        filing = parse_form4_xml(xml_bytes)
        if filing is None:
            return []

        index_url = (
            f"{_ARCHIVES_BASE}/{filing_cik}/{accession_no_dashes}/{accession}-index.htm"
        )

        return _build_transaction_records(
            filing,
            accession_number=accession,
            filed_at=filed_at,
            source_filing_url=index_url,
            min_value=min_value,
        )

    async def _resolve_primary_xml_url(
        self,
        client: httpx.AsyncClient,
        *,
        filing_cik: str,
        accession_no_dashes: str,
    ) -> str | None:
        """Look up the primary XML file by scanning the filing's index.json."""
        index_url = (
            f"{_ARCHIVES_BASE}/{filing_cik}/{accession_no_dashes}/index.json"
        )
        try:
            response = await client.get(index_url)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        directory = payload.get("directory") or {}
        items = directory.get("item") or []
        chosen: str | None = None
        for item in items:
            name = item.get("name") or ""
            if not name.lower().endswith(".xml"):
                continue
            # Skip XSL-rendered variants; we want the raw Form 4 XML.
            if name.lower().startswith("xsl"):
                continue
            chosen = name
            break
        if chosen is None:
            return None
        return f"{_ARCHIVES_BASE}/{filing_cik}/{accession_no_dashes}/{chosen}"

    async def _fetch_bytes(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> bytes | None:
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError:
            return None


def _primary_doc_url_from_hit_id(
    *,
    hit_id: Any,
    filing_cik: str,
    accession_no_dashes: str,
) -> str | None:
    """Fast-path: EFTS ``_id`` is shaped ``"<accession>:<primary-doc>"``.

    Only returns a URL when the primary doc is an ``.xml`` we can parse;
    otherwise the caller falls back to ``index.json`` resolution.
    """
    if not isinstance(hit_id, str) or ":" not in hit_id:
        return None
    _, _, primary_doc = hit_id.partition(":")
    primary_doc = primary_doc.strip()
    if not primary_doc.lower().endswith(".xml"):
        return None
    if primary_doc.lower().startswith("xsl"):
        return None
    return f"{_ARCHIVES_BASE}/{filing_cik}/{accession_no_dashes}/{primary_doc}"


def _build_transaction_records(
    filing: ParsedForm4Filing,
    *,
    accession_number: str,
    filed_at: datetime,
    source_filing_url: str | None,
    min_value: Decimal,
) -> list[Form4TransactionRecord]:
    """Cartesian-product reportingOwners × transactions, then value-filter.

    Pure function. Unit-testable by passing synthetic ``ParsedForm4Filing``
    fixtures — mirrors the ``_build_alert_records`` pattern used by the
    existing watchers.
    """
    records: list[Form4TransactionRecord] = []
    for owner in filing.reporting_owners:
        for idx, txn in enumerate(filing.transactions):
            value = _compute_value(txn)
            if value is None or value < min_value:
                continue
            dedupe_key = _dedupe_key(
                accession=accession_number,
                is_derivative=txn.is_derivative,
                owner_cik=owner.cik,
                index=idx,
            )
            records.append(
                _record_from_pair(
                    accession_number=accession_number,
                    transaction_index=idx,
                    dedupe_key=dedupe_key,
                    issuer=filing,
                    owner=owner,
                    txn=txn,
                    value=value,
                    source_filing_url=source_filing_url,
                    filed_at=filed_at,
                )
            )
    return records


def _compute_value(txn: ParsedTransaction) -> Decimal | None:
    if txn.shares is None or txn.price_per_share is None:
        return None
    try:
        return Decimal(txn.shares) * Decimal(txn.price_per_share)
    except (TypeError, ValueError):
        return None


def _dedupe_key(
    *,
    accession: str,
    is_derivative: bool,
    owner_cik: str,
    index: int,
) -> str:
    table_marker = "d" if is_derivative else "nd"
    return f"Form 4:{accession}:{table_marker}:{owner_cik}:{index}"


def _record_from_pair(
    *,
    accession_number: str,
    transaction_index: int,
    dedupe_key: str,
    issuer: ParsedForm4Filing,
    owner: ParsedReportingOwner,
    txn: ParsedTransaction,
    value: Decimal,
    source_filing_url: str | None,
    filed_at: datetime,
) -> Form4TransactionRecord:
    return Form4TransactionRecord(
        accession_number=accession_number,
        transaction_index=transaction_index,
        is_derivative=txn.is_derivative,
        dedupe_key=dedupe_key,
        issuer_cik=issuer.issuer.cik,
        issuer_name=issuer.issuer.name,
        issuer_ticker=issuer.issuer.trading_symbol,
        reporting_owner_cik=owner.cik,
        reporting_owner_name=owner.name,
        reporting_owner_is_director=owner.relationship.is_director,
        reporting_owner_is_officer=owner.relationship.is_officer,
        reporting_owner_is_ten_pct=owner.relationship.is_ten_pct,
        reporting_owner_title=owner.relationship.officer_title,
        reporting_owner_street1=owner.address.street1,
        reporting_owner_street2=owner.address.street2,
        reporting_owner_city=owner.address.city,
        reporting_owner_state=owner.address.state,
        reporting_owner_zip=owner.address.zip_code,
        security_title=txn.security_title,
        transaction_date=txn.transaction_date,
        transaction_code=txn.transaction_code,
        ad_code=txn.ad_code,
        shares=float(txn.shares) if txn.shares is not None else None,
        price_per_share=float(txn.price_per_share) if txn.price_per_share is not None else None,
        transaction_value=float(value),
        source_filing_url=source_filing_url,
        filed_at=filed_at,
    )
