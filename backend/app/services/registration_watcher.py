"""FINRA Form BD newly-registered watcher.

Restores the PRD-spec'd "Registrations" alert category that's been
empty since launch. The original ``daily_filing_monitor`` tried to
detect new BD registrations from SEC EDGAR submissions JSON, but Form
BD is filed via FINRA Web CRD, not EDGAR — so EDGAR feeds never carry
the signal. After 2026-05-04 the filing monitor was retargeted at
X-17A-5 (annual audited financial reports), and Form BD detection
moved here.

How it works:

1. The FINRA detail backfill populates ``broker_dealers.registration_date``
   from each firm's Form BD PDF (see ``services/brokercheck_pdf.py``).
   Most firms in our universe have historical SEC registration dates
   (decades old), but newly-registered firms ingested by future
   ``initial_load`` runs will land with recent dates.
2. This watcher scans ``broker_dealers WHERE registration_date >=
   today - 30 days`` once per cron tick and inserts a ``Form BD`` alert
   for each. The dedupe_key is keyed on ``(bd.id, registration_date)``
   so re-running the watcher is idempotent.
3. The existing ``alerts.py:72`` "registrations" category filters on
   ``form_type == "Form BD"`` so these alerts auto-surface on the
   /alerts "Registrations" tab — no FE change required.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer
from app.models.pipeline_run import PipelineRun
from app.services.alerts import AlertRepository
from app.services.service_models import FilingAlertRecord

logger = logging.getLogger(__name__)


_RECENT_REGISTRATION_WINDOW_DAYS = 30


class RegistrationWatcherService:
    """Cron-driven detector for newly-registered broker-dealers.

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
            pipeline_name="registration_watcher",
            trigger_source=trigger_source,
            status="running",
            total_items=0,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes="Scanning broker_dealers for recent FINRA registrations.",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

        cutoff = date.today() - timedelta(days=_RECENT_REGISTRATION_WINDOW_DAYS)
        recent_query = (
            select(BrokerDealer)
            .where(BrokerDealer.registration_date.is_not(None))
            .where(BrokerDealer.registration_date >= cutoff)
            .order_by(BrokerDealer.registration_date.desc(), BrokerDealer.id.asc())
        )
        recent = (await db.execute(recent_query)).scalars().all()

        records = self._build_alert_records(recent)

        async with SessionLocal() as write_db:
            await self.alert_repository.upsert_many(write_db, records)
            await write_db.commit()

            run = await write_db.get(PipelineRun, run_id)
            if run is None:
                raise RuntimeError(
                    f"Pipeline run {run_id} could not be reloaded for "
                    "registration watcher finalization."
                )

            run.total_items = len(records)
            run.processed_items = len(records)
            run.success_count = len(records)
            run.failure_count = 0
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            run.notes = (
                f"Detected {len(records)} broker-dealer registration(s) "
                f"in the last {_RECENT_REGISTRATION_WINDOW_DAYS} days."
            )
            await write_db.commit()
            await write_db.refresh(run)
            logger.info(
                "Registration watcher run %d: %d new registration alert(s) emitted.",
                run.id,
                len(records),
            )
            return run

    @staticmethod
    def _build_alert_records(
        broker_dealers: list[BrokerDealer],
    ) -> list[FilingAlertRecord]:
        """Map each recently-registered BD to a Form BD alert record.

        ``filed_at`` is anchored at noon UTC on the registration date so
        timezone math is stable across the daily/cron boundary. The
        dedupe_key is ``"Form BD:{bd_id}:{registration_date.isoformat()}"``
        which is also the shape the existing ``daily_filing_monitor``
        used pre-2026-05-04, so any historical Form BD rows still match
        the same key space.
        """
        records: list[FilingAlertRecord] = []
        for bd in broker_dealers:
            if bd.registration_date is None:
                continue
            filed_at = datetime.combine(
                bd.registration_date,
                time(hour=12),
                tzinfo=timezone.utc,
            )
            records.append(
                FilingAlertRecord(
                    bd_id=bd.id,
                    dedupe_key=f"Form BD:{bd.id}:{bd.registration_date.isoformat()}",
                    form_type="Form BD",
                    priority="high",
                    filed_at=filed_at,
                    summary=(
                        "New broker-dealer registration detected via "
                        "FINRA Form BD. Route to the Registrations tab."
                    ),
                    source_filing_url=bd.filings_index_url,
                )
            )
        records.sort(key=lambda item: item.filed_at, reverse=True)
        return records
