from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer
from app.models.filing_alert import FilingAlert

logger = logging.getLogger(__name__)
from app.models.pipeline_run import PipelineRun
from app.services.alerts import AlertRepository
from app.services.broker_dealers import BrokerDealerRepository
from app.services.service_models import FilingAlertRecord


class FilingMonitorService:
    def __init__(self) -> None:
        self.alert_repository = AlertRepository()
        self.repository = BrokerDealerRepository()

    async def run(self, db: AsyncSession, *, trigger_source: str = "manual") -> PipelineRun:
        broker_dealers = (await db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))).scalars().all()
        if settings.filing_monitor_offset > 0:
            broker_dealers = broker_dealers[settings.filing_monitor_offset :]
        if settings.filing_monitor_limit:
            broker_dealers = broker_dealers[: settings.filing_monitor_limit]

        run = PipelineRun(
            pipeline_name="daily_filing_monitor",
            trigger_source=trigger_source,
            status="running",
            total_items=0,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes="Scanning Form BD and 17a-11 filings.",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

        if settings.data_source_mode == "sample":
            records = self._generate_sample_alerts(broker_dealers)
        else:
            records = await self._fetch_live_alerts(broker_dealers)
        async with SessionLocal() as write_db:
            await self.alert_repository.upsert_many(write_db, records)
            refreshed_broker_dealers = (
                await write_db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))
            ).scalars().all()
            deficiency_rows = (
                await write_db.execute(
                    select(FilingAlert.bd_id, FilingAlert.filed_at).where(FilingAlert.form_type == "Form 17a-11")
                )
            ).all()
            await self._refresh_deficiency_flags(write_db, refreshed_broker_dealers, deficiency_rows)
            await write_db.commit()
            await self.repository.refresh_lead_scores(write_db)

            run = await write_db.get(PipelineRun, run_id)
            if run is None:
                raise RuntimeError(f"Pipeline run {run_id} could not be reloaded for filing monitor finalization.")

            run.total_items = len(records)
            run.processed_items = len(records)
            run.success_count = len(records)
            run.failure_count = 0
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            run.notes = f"Processed {len(records)} filing alerts in {settings.data_source_mode} mode."
            await write_db.commit()
            await write_db.refresh(run)
            return run

    async def _fetch_live_alerts(self, broker_dealers: list[BrokerDealer]) -> list[FilingAlertRecord]:
        alerts: list[FilingAlertRecord] = []
        cutoff_30_days = date.today() - timedelta(days=30)
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "application/json",
        }
        total_bds = len(broker_dealers)
        sec_delay = 1.0 / settings.edgar_rate_limit_per_second if settings.edgar_rate_limit_per_second > 0 else 0
        skipped = 0
        errors = 0

        async with httpx.AsyncClient(
            timeout=settings.sec_request_timeout_seconds,
            headers=headers,
            follow_redirects=True,
        ) as client:
            for bd_index, broker_dealer in enumerate(broker_dealers):
                if (bd_index + 1) % 50 == 0 or bd_index == 0:
                    logger.info(
                        "Filing monitor progress: %d/%d scanned, %d alerts found so far.",
                        bd_index + 1, total_bds, len(alerts),
                    )

                if not broker_dealer.filings_index_url:
                    skipped += 1
                    continue

                try:
                    response = await client.get(broker_dealer.filings_index_url)
                    if response.status_code == 429:
                        retry_after = response.headers.get("retry-after")
                        wait = float(retry_after) if retry_after else 5.0
                        logger.warning("SEC rate limited at BD %d, waiting %.1fs", broker_dealer.id, wait)
                        await asyncio.sleep(wait)
                        response = await client.get(broker_dealer.filings_index_url)
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    errors += 1
                    continue

                # Respect SEC rate limit between requests
                if sec_delay > 0:
                    await asyncio.sleep(sec_delay)

                recent = payload.get("filings", {}).get("recent", []) if isinstance(payload.get("filings"), dict) else []
                if not isinstance(recent, dict):
                    continue

                forms = recent.get("form", [])
                filing_dates = recent.get("filingDate", [])
                accession_numbers = recent.get("accessionNumber", [])

                for form, filing_date_raw, accession_number in zip(forms, filing_dates, accession_numbers, strict=False):
                    if not isinstance(form, str) or not isinstance(filing_date_raw, str):
                        continue
                    normalized_form = form.strip().upper().replace("FORM ", "")
                    if normalized_form not in {"BD", "17A-11", "17A11", "17-A"}:
                        continue

                    try:
                        filed_date = date.fromisoformat(filing_date_raw)
                    except ValueError:
                        continue

                    if normalized_form == "BD" and filed_date < cutoff_30_days:
                        continue

                    priority = "critical" if "17" in normalized_form else "high"
                    canonical_form = "Form 17a-11" if "17" in normalized_form else "Form BD"
                    summary = (
                        "Capital deficiency notice detected; route firm to the Alternative List for review."
                        if canonical_form == "Form 17a-11"
                        else "New broker-dealer registration detected in the SEC filing monitor."
                    )
                    filed_at = datetime.combine(filed_date, time(hour=12), tzinfo=timezone.utc)
                    alerts.append(
                        FilingAlertRecord(
                            bd_id=broker_dealer.id,
                            dedupe_key=f"{canonical_form}:{broker_dealer.id}:{accession_number or filing_date_raw}",
                            form_type=canonical_form,
                            priority=priority,
                            filed_at=filed_at,
                            summary=summary,
                            source_filing_url=broker_dealer.filings_index_url,
                        )
                    )

        form_bd_count = sum(1 for a in alerts if a.form_type == "Form BD")
        deficiency_count = sum(1 for a in alerts if a.form_type == "Form 17a-11")
        logger.info(
            "Filing monitor complete: %d/%d scanned, %d alerts (%d Form BD, %d Form 17a-11), %d skipped, %d errors.",
            total_bds - skipped, total_bds, len(alerts), form_bd_count, deficiency_count, skipped, errors,
        )

        alerts.sort(key=lambda item: item.filed_at, reverse=True)
        return alerts

    def _generate_sample_alerts(self, broker_dealers: list[BrokerDealer]) -> list[FilingAlertRecord]:
        today = date.today()
        now = datetime.now(timezone.utc)
        alerts: list[FilingAlertRecord] = []

        # Pick BDs for sample Form BD alerts.  Prefer those with registration
        # dates, but fall back to any BD if none have dates populated.
        form_bd_candidates = sorted(
            [bd for bd in broker_dealers if bd.registration_date is not None],
            key=lambda item: item.registration_date or today,
            reverse=True,
        )[:18]
        if not form_bd_candidates:
            form_bd_candidates = broker_dealers[:18]
        for index, broker_dealer in enumerate(form_bd_candidates):
            filing_date = datetime.combine(
                broker_dealer.registration_date or (today - timedelta(days=index)),
                time(hour=9 + (index % 6), minute=(index * 7) % 60),
                tzinfo=timezone.utc,
            )
            alerts.append(
                FilingAlertRecord(
                    bd_id=broker_dealer.id,
                    dedupe_key=f"form-bd:{broker_dealer.id}:{filing_date.date().isoformat()}",
                    form_type="Form BD",
                    priority="high",
                    filed_at=filing_date,
                    summary="New broker-dealer registration detected in the daily EDGAR monitor.",
                    source_filing_url=broker_dealer.filings_index_url,
                )
            )

        deficiency_candidates = [
            broker_dealer
            for broker_dealer in broker_dealers
            if broker_dealer.latest_net_capital is not None and broker_dealer.required_min_capital is not None
        ]
        deficiency_candidates = sorted(
            deficiency_candidates,
            key=lambda item: (
                0 if item.health_status == "at_risk" else 1,
                float(item.latest_net_capital or 0) - float(item.required_min_capital or 0),
                item.id,
            ),
        )[:24]

        for index, broker_dealer in enumerate(deficiency_candidates):
            deficiency_date = datetime.combine(
                today - timedelta(days=index % 14),
                time(hour=13 + (index % 4), minute=(index * 11) % 60),
                tzinfo=timezone.utc,
            )
            alerts.append(
                FilingAlertRecord(
                    bd_id=broker_dealer.id,
                    dedupe_key=f"17a-11:{broker_dealer.id}:{deficiency_date.date().isoformat()}",
                    form_type="Form 17a-11",
                    priority="critical",
                    filed_at=deficiency_date,
                    summary="Capital deficiency notice detected; route firm to the Alternative List for review.",
                    source_filing_url=broker_dealer.filings_index_url,
                )
            )

        alerts.sort(key=lambda item: item.filed_at, reverse=True)
        return alerts

    async def _refresh_deficiency_flags(
        self,
        db: AsyncSession,
        broker_dealers: list[BrokerDealer],
        deficiency_rows: list[tuple[int, datetime]],
    ) -> None:
        latest_deficiency_by_bd: dict[int, date] = {}
        for bd_id, filed_at in deficiency_rows:
            latest = latest_deficiency_by_bd.get(bd_id)
            alert_date = filed_at.date()
            if latest is None or alert_date > latest:
                latest_deficiency_by_bd[bd_id] = alert_date

        for broker_dealer in broker_dealers:
            latest_deficiency = latest_deficiency_by_bd.get(broker_dealer.id)
            broker_dealer.is_deficient = latest_deficiency is not None
            broker_dealer.latest_deficiency_filed_at = latest_deficiency
            if latest_deficiency is not None:
                broker_dealer.health_status = "at_risk"

        await db.flush()
