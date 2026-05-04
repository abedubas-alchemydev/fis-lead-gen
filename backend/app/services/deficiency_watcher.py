"""SEC Rule 17a-11 deficiency notice watcher.

Restores the PRD-spec'd "Critical: Deficiencies" alert category. SEC
Rule 17a-11 requires a broker-dealer to notify the SEC within 24 hours
when its net capital falls below required minimums. The notification
isn't filed under a distinct top-level form code; instead it's filed
as an ``X-17A-5`` (or ``X-17A-5/A`` amendment) whose body explicitly
references Rule 17a-11.

This rules out simple form-code matching — we'd either match every
X-17A-5 (95% false-positive rate; routine annual audits) or only
amendments (5% recall; most deficiency notices are filed as regular
X-17A-5). Instead we use SEC EDGAR's full-text search (EFTS) endpoint
to find filings whose body contains the literal "17a-11" string,
then join the resulting CIKs against our ``broker_dealers`` table.

EFTS is rate-limited to ~10 req/sec but a single daily query covers
all 60-ish 17a-11 mentions per year across all US broker-dealers. No
per-firm fan-out required.

How it works:

1. Query EFTS: ``q="17a-11"&dateRange=custom&startdt=<today-30d>``.
   The literal-quoted query forces exact-string match; unquoted would
   tokenize on the dash.
2. For each hit, look up the CIK in ``broker_dealers``. CIKs from EFTS
   are zero-padded (e.g. ``0001542278``); our DB stores them without
   leading zeros, so strip before the join.
3. Insert a ``Form 17a-11`` alert per matched firm with
   ``priority="critical"``. The existing ``alerts.py:74`` filter
   ``form_type == "Form 17a-11"`` auto-surfaces them on the
   /alerts Deficiencies tab.
4. Dedupe by ``(bd_id, accession_number)`` so re-running the watcher
   is idempotent — the EFTS hit for a given filing always returns the
   same accession.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer
from app.models.pipeline_run import PipelineRun
from app.services.alerts import AlertRepository
from app.services.service_models import FilingAlertRecord

logger = logging.getLogger(__name__)


_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
_DEFICIENCY_QUERY = '"17a-11"'
_LOOKBACK_DAYS = 30
_PAGE_SIZE = 100


class DeficiencyWatcherService:
    """Cron-driven detector for SEC Rule 17a-11 deficiency notices.

    Tier 2 cron job. Designed to run daily; idempotent across runs via
    the alerts table's ``dedupe_key`` unique constraint.
    """

    def __init__(self) -> None:
        self.alert_repository = AlertRepository()

    async def run(
        self,
        db: AsyncSession,
        *,
        trigger_source: str = "manual",
    ) -> PipelineRun:
        run = PipelineRun(
            pipeline_name="deficiency_watcher",
            trigger_source=trigger_source,
            status="running",
            total_items=0,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes="Polling SEC EDGAR full-text search for 17a-11 mentions.",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

        # CIK -> broker_dealer.id index for O(1) lookup against EFTS hits.
        bds = (await db.execute(select(BrokerDealer.id, BrokerDealer.cik))).all()
        cik_to_bd_id: dict[str, int] = {}
        for bd_id, cik in bds:
            if cik is None:
                continue
            normalized = str(cik).lstrip("0") or "0"
            cik_to_bd_id[normalized] = bd_id

        hits = await self._fetch_recent_efts_hits()
        records = self._build_alert_records(hits, cik_to_bd_id)

        async with SessionLocal() as write_db:
            await self.alert_repository.upsert_many(write_db, records)
            await write_db.commit()

            run = await write_db.get(PipelineRun, run_id)
            if run is None:
                raise RuntimeError(
                    f"Pipeline run {run_id} could not be reloaded for "
                    "deficiency watcher finalization."
                )

            run.total_items = len(hits)
            run.processed_items = len(records)
            run.success_count = len(records)
            run.failure_count = 0
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            run.notes = (
                f"Scanned {len(hits)} EFTS hit(s) for '17a-11' in the last "
                f"{_LOOKBACK_DAYS} days; matched {len(records)} to our "
                "broker_dealers."
            )
            await write_db.commit()
            await write_db.refresh(run)
            logger.info(
                "Deficiency watcher run %d: %d EFTS hit(s), %d matched alerts.",
                run.id,
                len(hits),
                len(records),
            )
            return run

    async def _fetch_recent_efts_hits(self) -> list[dict]:
        """Query SEC EFTS for "17a-11" mentions in the last 30 days."""
        end = date.today()
        start = end - timedelta(days=_LOOKBACK_DAYS)
        params = {
            "q": _DEFICIENCY_QUERY,
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
            "from": 0,
            "size": _PAGE_SIZE,
        }
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "application/json",
        }

        all_hits: list[dict] = []
        async with httpx.AsyncClient(
            timeout=settings.sec_request_timeout_seconds,
            headers=headers,
            follow_redirects=True,
        ) as client:
            cursor = 0
            while True:
                params["from"] = cursor
                response = await client.get(_EFTS_URL, params=params)
                response.raise_for_status()
                payload = response.json()
                hits_block = payload.get("hits", {})
                hits = hits_block.get("hits", []) if isinstance(hits_block, dict) else []
                if not hits:
                    break
                all_hits.extend(hits)
                cursor += len(hits)
                total = hits_block.get("total", {})
                total_value = (
                    total.get("value")
                    if isinstance(total, dict)
                    else (total if isinstance(total, int) else 0)
                ) or 0
                if cursor >= total_value or len(hits) < _PAGE_SIZE:
                    break
        return all_hits

    @staticmethod
    def _build_alert_records(
        hits: list[dict],
        cik_to_bd_id: dict[str, int],
    ) -> list[FilingAlertRecord]:
        """Map matching EFTS hits to ``Form 17a-11`` alert records.

        Skips hits whose CIK isn't in our broker-dealer universe.
        Skips hits with no usable accession number (rare; defensive).
        """
        records: list[FilingAlertRecord] = []
        seen: set[str] = set()
        for hit in hits:
            source = hit.get("_source") or {}
            ciks = source.get("ciks") or []
            if not isinstance(ciks, list) or not ciks:
                continue

            accession = source.get("adsh")
            if not isinstance(accession, str) or not accession:
                continue
            filed_raw = source.get("file_date") or source.get("filed_at")
            try:
                filed_date = (
                    date.fromisoformat(filed_raw)
                    if isinstance(filed_raw, str)
                    else None
                )
            except ValueError:
                filed_date = None
            if filed_date is None:
                filed_date = date.today()

            form_type_raw = str(source.get("form", "")).strip().upper()
            for cik_padded in ciks:
                cik_norm = str(cik_padded).lstrip("0") or "0"
                bd_id = cik_to_bd_id.get(cik_norm)
                if bd_id is None:
                    continue
                dedupe_key = f"Form 17a-11:{bd_id}:{accession}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                source_url = (
                    f"https://www.sec.gov/cgi-bin/browse-edgar"
                    f"?action=getcompany&CIK={cik_padded}&type={form_type_raw}"
                    f"&dateb=&owner=include&count=40"
                )
                filed_at = datetime.combine(
                    filed_date, time(hour=12), tzinfo=timezone.utc
                )
                records.append(
                    FilingAlertRecord(
                        bd_id=bd_id,
                        dedupe_key=dedupe_key,
                        form_type="Form 17a-11",
                        priority="critical",
                        filed_at=filed_at,
                        summary=(
                            "Capital deficiency notice detected (Rule 17a-11). "
                            "Route firm to the Alternative List for review."
                        ),
                        source_filing_url=source_url,
                    )
                )
        records.sort(key=lambda item: item.filed_at, reverse=True)
        return records
