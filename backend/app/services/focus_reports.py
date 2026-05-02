from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer

logger = logging.getLogger(__name__)
from app.models.financial_metric import FinancialMetric
from app.models.pipeline_run import PipelineRun
from app.services.extraction_status import (
    STATUS_NEEDS_REVIEW,
    STATUS_PARSED,
    classify_financial_extraction_status,
)
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiResponsesClient,
)
from app.services.pdf_downloader import PdfDownloaderService, pdf_tempdir
from app.services.scoring import calculate_yoy_growth, classify_health_status
from app.services.service_models import FinancialMetricRecord


FINANCIAL_PIPELINE_NAME = "financial_pdf_pipeline"


@dataclass(slots=True)
class FinancialExtractionResult:
    records: list[FinancialMetricRecord] = field(default_factory=list)
    target_count: int = 0
    skipped_no_url: int = 0
    skipped_no_pdf: int = 0
    skipped_extraction_error: int = 0
    # Counts rows that couldn't be persisted at all -- Gemini produced no
    # net_capital / no report_date, so the NOT NULL columns rule them out.
    # Rows that DID have net_capital but below the confidence threshold now
    # land in ``records`` tagged ``needs_review`` instead of being dropped.
    skipped_low_confidence: int = 0
    needs_review_count: int = 0


class FocusReportService:
    def __init__(self) -> None:
        self.downloader = PdfDownloaderService()
        self.gemini_client = GeminiResponsesClient()

    async def load_financial_metrics(self, db: AsyncSession, *, trigger_source: str = "manual") -> int:
        # Prioritize BDs that still need financials so small batches land on
        # unprocessed firms first; BDs with existing data come after as a
        # refresh-tail fallback.
        bds_with_data = (await db.execute(
            select(BrokerDealer)
            .where(BrokerDealer.latest_net_capital.is_not(None))
            .order_by(BrokerDealer.latest_net_capital.desc())
        )).scalars().all()

        bds_with_urls_no_data = (await db.execute(
            select(BrokerDealer)
            .where(
                BrokerDealer.filings_index_url.is_not(None),
                BrokerDealer.latest_net_capital.is_(None),
            )
            .order_by(BrokerDealer.id.asc())
        )).scalars().all()

        # Prioritized list: firms that still need financials first, then refresh tail.
        broker_dealers = bds_with_urls_no_data + bds_with_data
        target_broker_dealers = self._apply_batch_window(
            broker_dealers,
            offset=settings.financial_pipeline_offset,
            limit=settings.financial_pipeline_limit,
        )

        # Commit the pipeline_run row in its own transaction before any
        # extraction work begins, so a crash mid-loop still leaves a
        # discoverable audit row in status='running'.
        pipeline_run = PipelineRun(
            pipeline_name=FINANCIAL_PIPELINE_NAME,
            trigger_source=trigger_source,
            status="running",
            total_items=len(target_broker_dealers),
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes=json.dumps(
                {
                    "stage": "started",
                    "offset": settings.financial_pipeline_offset,
                    "limit": settings.financial_pipeline_limit,
                    "target_count": len(target_broker_dealers),
                    "provider": self._provider_descriptor(),
                }
            ),
        )
        db.add(pipeline_run)
        await db.flush()
        run_id = pipeline_run.id
        await db.commit()

        try:
            incremental_target_ids: list[int] | None = None
            extraction = await self._load_live_records(target_broker_dealers)
            records = extraction.records
            if settings.financial_pipeline_limit and not settings.focus_reports_csv_path:
                incremental_target_ids = [broker_dealer.id for broker_dealer in target_broker_dealers]

            async with SessionLocal() as write_db:
                if incremental_target_ids is not None:
                    # Narrow the DELETE to the (bd_id, report_date) pairs the
                    # current run is about to re-insert. Prevents wiping prior
                    # fiscal-year history for the same bd once the multi-year
                    # extractor ships (Phase 2C-code). Matches the grain of the
                    # uq_financial_metrics_bd_report_date constraint.
                    target_pairs = sorted({(record.bd_id, record.report_date) for record in records})
                    if target_pairs:
                        await write_db.execute(
                            delete(FinancialMetric).where(
                                tuple_(FinancialMetric.bd_id, FinancialMetric.report_date).in_(target_pairs)
                            )
                        )
                else:
                    await write_db.execute(delete(FinancialMetric))
                write_db.add_all(
                    [
                        FinancialMetric(
                            bd_id=record.bd_id,
                            report_date=record.report_date,
                            net_capital=record.net_capital,
                            excess_net_capital=record.excess_net_capital,
                            total_assets=record.total_assets,
                            required_min_capital=record.required_min_capital,
                            source_filing_url=record.source_filing_url,
                            extraction_status=record.extraction_status,
                        )
                        for record in records
                    ]
                )
                await write_db.flush()
                refreshed_broker_dealers = (
                    await write_db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))
                ).scalars().all()
                await self._refresh_broker_dealer_rollups(write_db, refreshed_broker_dealers)
                await write_db.commit()

                await self._finalize_pipeline_run(write_db, run_id, extraction)
            return len(records)
        except Exception as exc:
            logger.exception("Financial extraction pipeline failed for run %s", run_id)
            await self._mark_pipeline_run_failed(run_id, exc, len(target_broker_dealers))
            raise

    async def load_financial_metrics_for_broker_dealer(
        self,
        bd_id: int,
        *,
        trigger_source: str = "manual_single",
        pipeline_run_id: int,
    ) -> int:
        """Run the X-17A-5 → Gemini extraction for a single broker-dealer.

        Used by ``POST /broker-dealers/{id}/refresh-financials`` to fill
        in missing financial fields (``latest_net_capital``,
        ``latest_excess_net_capital``, ``yoy_growth``, ``health_status``)
        for a specific firm without running the full batch window.

        The caller (the endpoint's background-task wrapper) creates the
        ``PipelineRun`` row in ``status="queued"`` and passes its id in;
        this method moves the run through ``running → completed`` (or
        ``→ failed``) by reusing :meth:`_finalize_pipeline_run` and
        :meth:`_mark_pipeline_run_failed`.

        DELETE narrowing (``tuple_(bd_id, report_date) IN target_pairs``)
        keeps OTHER firms' fiscal-year history untouched. The bare
        ``DELETE FROM financial_metrics`` fallback used by the batch
        path when ``limit`` is unset is intentionally not reachable here
        — this method is single-firm only.

        Returns the count of FinancialMetric rows persisted.
        """
        async with SessionLocal() as load_db:
            broker_dealer = await load_db.get(BrokerDealer, bd_id)

        if broker_dealer is None:
            error = ValueError(f"Broker-dealer {bd_id} not found.")
            await self._mark_pipeline_run_failed(pipeline_run_id, error, target_count=0)
            raise error

        async with SessionLocal() as run_db:
            persisted_run = await run_db.get(PipelineRun, pipeline_run_id)
            if persisted_run is None:
                raise RuntimeError(
                    f"Pipeline run {pipeline_run_id} could not be reloaded for single-firm extraction."
                )
            persisted_run.status = "running"
            persisted_run.total_items = 1
            persisted_run.trigger_source = trigger_source
            persisted_run.notes = json.dumps(
                {
                    "bd_id": bd_id,
                    "stage": "running",
                    "provider": self._provider_descriptor(),
                }
            )
            await run_db.commit()

        try:
            extraction = await self._extract_live_records_from_pdfs([broker_dealer])
            records = extraction.records

            async with SessionLocal() as write_db:
                target_pairs = sorted({(record.bd_id, record.report_date) for record in records})
                if target_pairs:
                    await write_db.execute(
                        delete(FinancialMetric).where(
                            tuple_(FinancialMetric.bd_id, FinancialMetric.report_date).in_(target_pairs)
                        )
                    )
                write_db.add_all(
                    [
                        FinancialMetric(
                            bd_id=record.bd_id,
                            report_date=record.report_date,
                            net_capital=record.net_capital,
                            excess_net_capital=record.excess_net_capital,
                            total_assets=record.total_assets,
                            required_min_capital=record.required_min_capital,
                            source_filing_url=record.source_filing_url,
                            extraction_status=record.extraction_status,
                        )
                        for record in records
                    ]
                )
                await write_db.flush()
                # Reload the BD inside this session so the rollup
                # mutations track on a managed instance and commit
                # together with the FinancialMetric inserts.
                managed_bd = await write_db.get(BrokerDealer, bd_id)
                if managed_bd is not None:
                    await self._refresh_broker_dealer_rollups(write_db, [managed_bd])
                await write_db.commit()

                await self._finalize_pipeline_run(write_db, pipeline_run_id, extraction)
            return len(records)
        except Exception as exc:
            logger.exception(
                "Single-firm financial extraction failed for run %s (bd_id=%s)",
                pipeline_run_id,
                bd_id,
            )
            await self._mark_pipeline_run_failed(pipeline_run_id, exc, target_count=1)
            raise

    async def _finalize_pipeline_run(
        self,
        write_db: AsyncSession,
        run_id: int,
        extraction: FinancialExtractionResult,
    ) -> None:
        persisted_run = await write_db.get(PipelineRun, run_id)
        if persisted_run is None:
            raise RuntimeError(
                f"Pipeline run {run_id} could not be reloaded for financial finalization."
            )

        failure_count = (
            extraction.skipped_no_url
            + extraction.skipped_no_pdf
            + extraction.skipped_extraction_error
            + extraction.skipped_low_confidence
        )
        provider_descriptor = self._provider_descriptor()
        summary = (
            f"Processed {extraction.target_count} filings via {provider_descriptor}. "
            f"Records extracted: {len(extraction.records)} "
            f"({extraction.needs_review_count} needs_review). "
            f"Skipped: {extraction.skipped_no_url} no URL, "
            f"{extraction.skipped_no_pdf} no PDF, "
            f"{extraction.skipped_extraction_error} errors, "
            f"{extraction.skipped_low_confidence} low confidence."
        )
        persisted_run.total_items = extraction.target_count
        persisted_run.processed_items = extraction.target_count
        persisted_run.success_count = len(extraction.records)
        persisted_run.failure_count = failure_count
        persisted_run.status = "completed_with_errors" if failure_count else "completed"
        persisted_run.completed_at = datetime.now(timezone.utc)
        persisted_run.notes = json.dumps(
            {
                "summary": summary,
                "records": len(extraction.records),
                "needs_review": extraction.needs_review_count,
                "skipped_no_url": extraction.skipped_no_url,
                "skipped_no_pdf": extraction.skipped_no_pdf,
                "skipped_extraction_error": extraction.skipped_extraction_error,
                "skipped_low_confidence": extraction.skipped_low_confidence,
                "target_count": extraction.target_count,
                "offset": settings.financial_pipeline_offset,
                "limit": settings.financial_pipeline_limit,
                "provider": provider_descriptor,
            }
        )
        await write_db.commit()

    async def _mark_pipeline_run_failed(
        self,
        run_id: int,
        exc: BaseException,
        target_count: int,
    ) -> None:
        # Use a fresh session so the failure write is not bound to the
        # extraction path's rolled-back transaction state.
        try:
            async with SessionLocal() as fail_db:
                persisted_run = await fail_db.get(PipelineRun, run_id)
                if persisted_run is None:
                    return
                persisted_run.status = "failed"
                persisted_run.completed_at = datetime.now(timezone.utc)
                persisted_run.notes = json.dumps(
                    {
                        "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                        "offset": settings.financial_pipeline_offset,
                        "limit": settings.financial_pipeline_limit,
                        "target_count": target_count,
                        "provider": self._provider_descriptor(),
                    }
                )
                await fail_db.commit()
        except Exception:
            logger.exception("Failed to mark pipeline run %s as failed", run_id)

    def _provider_descriptor(self) -> str:
        provider = settings.llm_provider
        if provider == "openai":
            return f"openai:{settings.openai_pdf_model}"
        return provider

    async def _load_live_records(self, broker_dealers: list[BrokerDealer]) -> FinancialExtractionResult:
        csv_records = self._load_live_records_from_csv(broker_dealers)
        if csv_records:
            logger.info("Loaded %d financial records from CSV.", len(csv_records))
            return FinancialExtractionResult(
                records=csv_records,
                target_count=len(broker_dealers),
            )

        if settings.focus_reports_csv_path:
            logger.warning(
                "CSV path configured (%s) but produced zero records — falling through to PDF extraction.",
                settings.focus_reports_csv_path,
            )

        result = await self._extract_live_records_from_pdfs(broker_dealers)
        if not result.records:
            logger.warning(
                "Financial extraction produced zero records for %d broker-dealers. "
                "Check that GEMINI_API_KEY is set and broker-dealers have filings_index_url populated.",
                len(broker_dealers),
            )
        return result


    def _load_live_records_from_csv(self, broker_dealers: list[BrokerDealer]) -> list[FinancialMetricRecord]:
        csv_path_value = settings.focus_reports_csv_path
        if not csv_path_value:
            return []

        csv_path = Path(csv_path_value)
        if not csv_path.exists():
            return []

        by_cik = {broker_dealer.cik: broker_dealer for broker_dealer in broker_dealers if broker_dealer.cik}
        by_crd = {
            str(broker_dealer.crd_number): broker_dealer
            for broker_dealer in broker_dealers
            if broker_dealer.crd_number
        }
        by_sec = {
            str(broker_dealer.sec_file_number): broker_dealer
            for broker_dealer in broker_dealers
            if broker_dealer.sec_file_number
        }

        records: list[FinancialMetricRecord] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                broker_dealer = self._match_broker_dealer(row, by_cik=by_cik, by_crd=by_crd, by_sec=by_sec)
                if broker_dealer is None:
                    continue

                report_date_raw = (row.get("report_date") or "").strip()
                net_capital_raw = (row.get("net_capital") or "").strip()
                if not report_date_raw or not net_capital_raw:
                    continue

                try:
                    report_date = date.fromisoformat(report_date_raw)
                    net_capital = float(net_capital_raw)
                except ValueError:
                    continue

                records.append(
                    FinancialMetricRecord(
                        bd_id=broker_dealer.id,
                        report_date=report_date,
                        net_capital=net_capital,
                        excess_net_capital=self._parse_optional_float(row.get("excess_net_capital")),
                        total_assets=self._parse_optional_float(row.get("total_assets")),
                        required_min_capital=self._parse_optional_float(row.get("required_min_capital")),
                        source_filing_url=(row.get("source_filing_url") or "").strip() or broker_dealer.filings_index_url,
                    )
                )

        return records

    async def _extract_live_records_from_pdfs(self, broker_dealers: list[BrokerDealer]) -> FinancialExtractionResult:
        records: list[FinancialMetricRecord] = []
        total = len(broker_dealers)
        skipped_no_url = 0
        skipped_no_pdf = 0
        skipped_extraction_error = 0
        skipped_low_confidence = 0

        for index, broker_dealer in enumerate(broker_dealers):
            if (index + 1) % 25 == 0 or index == 0:
                logger.info(
                    "Financial extraction progress: %d/%d processed, %d records extracted so far.",
                    index + 1, total, len(records),
                )

            if not broker_dealer.filings_index_url:
                skipped_no_url += 1
                continue

            # Per-firm tempdir replaces the persistent PDF cache. The
            # download + Gemini extraction both happen inside this block;
            # bytes_base64 is captured into memory at download time so the
            # Gemini calls don't depend on the file persisting on disk past
            # the ``with`` exit.
            with pdf_tempdir(prefix="financial_extract_") as tmp_dir:
                # Download the 2 most recent X-17A-5 PDFs for multi-year data
                try:
                    pdf_records = await self.downloader.download_recent_x17a5_pdfs(
                        broker_dealer, tmp_dir, count=2
                    )
                except Exception as exc:
                    logger.warning("PDF download failed for BD %d (%s): %s", broker_dealer.id, broker_dealer.name, exc)
                    skipped_extraction_error += 1
                    continue

                if not pdf_records:
                    skipped_no_pdf += 1
                    continue

                seen_dates: set[str] = set()
                for pdf_record in pdf_records:
                    try:
                        extractions = await self.gemini_client.extract_multi_year_financial_data(
                            pdf_bytes_base64=pdf_record.bytes_base64,
                            prompt=self._build_financial_prompt(),
                        )
                    except (GeminiConfigurationError, GeminiExtractionError) as exc:
                        logger.warning(
                            "Gemini multi-year extraction failed for BD %d filing_year %d: %s",
                            broker_dealer.id, pdf_record.filing_year, exc,
                        )
                        skipped_extraction_error += 1
                        continue
                    except Exception as exc:
                        logger.warning(
                            "Unexpected error in multi-year extraction for BD %d filing_year %d: %s",
                            broker_dealer.id, pdf_record.filing_year, exc,
                        )
                        skipped_extraction_error += 1
                        continue

                    if not extractions:
                        logger.warning(
                            "Gemini multi-year extraction returned zero rows for BD %d filing_year %d",
                            broker_dealer.id, pdf_record.filing_year,
                        )
                        skipped_extraction_error += 1
                        continue

                    # Per-year evaluation (#54 multi-year + #56/Fix G tagging):
                    # the multi-year response is an array of per-fiscal-year
                    # records, each independently eligible. A firm may have year N
                    # pass confidence while year N-1 lands as needs_review.
                    # Counter catalog:
                    #   - records list (parsed + needs_review): YEAR-grain rows
                    #     persisted; parsed vs needs_review determined by the
                    #     confidence classifier. needs_review_count is derived
                    #     from the list after the loop.
                    #   - skipped_low_confidence: YEAR-grain for rows that can't
                    #     persist at all because net_capital is NULL (NOT NULL
                    #     column rules them out regardless of tag).
                    #   - skipped_extraction_error: PDF- or year-grain for
                    #     provider errors, empty arrays, unparseable report_date.
                    #   - skipped_no_url / skipped_no_pdf: FIRM-grain.
                    # Invariant: (parsed rows) + needs_review_count + skipped_no_url
                    # + skipped_no_pdf + skipped_extraction_error +
                    # skipped_low_confidence == total units of work attempted.
                    for extraction in extractions:
                        # Rows with NULL net_capital can't be persisted at ANY
                        # extraction_status because net_capital is NOT NULL.
                        # Route to skipped_low_confidence -- "unpersistable" is
                        # its own signal, distinct from provider errors.
                        if extraction.net_capital is None:
                            logger.warning(
                                "Financial extraction skipped BD %s: net_capital=None "
                                "(confidence=%s) cannot persist under NOT NULL constraint",
                                broker_dealer.id,
                                extraction.confidence_score,
                            )
                            skipped_low_confidence += 1
                            continue

                        report_date = self._parse_report_date(extraction.report_date) or pdf_record.report_date
                        if report_date is None:
                            logger.warning(
                                "Financial extraction skipped BD %s: unparseable report_date",
                                broker_dealer.id,
                            )
                            skipped_extraction_error += 1
                            continue

                        # Dedup across PDFs within a firm: if two PDFs each return
                        # year N (e.g. an amendment + its original), keep the first
                        # parse of that date. uq_financial_metrics_bd_report_date
                        # would reject the second insert anyway, but the set skip
                        # saves a wasted ORM flush. Not counted as a skip bucket --
                        # already attributed to the first occurrence.
                        date_key = report_date.isoformat()
                        if date_key in seen_dates:
                            continue
                        seen_dates.add(date_key)

                        # Tag based on LLM confidence (#56 / Fix G). Below-threshold
                        # rows with a valid net_capital still persist, tagged
                        # 'needs_review', so the review queue can surface them
                        # instead of a silent drop. See app.services.extraction_status.
                        extraction_status = classify_financial_extraction_status(
                            confidence_score=extraction.confidence_score,
                            min_confidence=settings.financial_extraction_min_confidence,
                        )
                        if extraction_status == STATUS_NEEDS_REVIEW:
                            logger.warning(
                                "Financial extraction BD %s tagged needs_review: confidence=%s below min_confidence=%s",
                                broker_dealer.id,
                                extraction.confidence_score,
                                settings.financial_extraction_min_confidence,
                            )

                        records.append(
                            FinancialMetricRecord(
                                bd_id=broker_dealer.id,
                                report_date=report_date,
                                net_capital=extraction.net_capital,
                                excess_net_capital=extraction.excess_net_capital,
                                total_assets=extraction.total_assets,
                                required_min_capital=extraction.required_min_capital,
                                source_filing_url=pdf_record.source_pdf_url or pdf_record.source_filing_url,
                                extraction_status=extraction_status,
                            )
                        )

        needs_review_count = sum(
            1 for record in records if record.extraction_status == STATUS_NEEDS_REVIEW
        )
        logger.info(
            "Financial extraction complete: %d/%d extracted (%d needs_review). "
            "Skipped: %d no URL, %d no PDF, %d errors, %d low confidence (unpersistable).",
            len(records), total, needs_review_count,
            skipped_no_url, skipped_no_pdf, skipped_extraction_error, skipped_low_confidence,
        )
        logger.warning(
            "Financial extraction summary: total=%d skipped_no_url=%d "
            "skipped_no_pdf=%d skipped_extraction_error=%d "
            "skipped_low_confidence=%d needs_review=%d records=%d",
            total,
            skipped_no_url,
            skipped_no_pdf,
            skipped_extraction_error,
            skipped_low_confidence,
            needs_review_count,
            len(records),
        )
        return FinancialExtractionResult(
            records=records,
            target_count=total,
            skipped_no_url=skipped_no_url,
            skipped_no_pdf=skipped_no_pdf,
            skipped_extraction_error=skipped_extraction_error,
            skipped_low_confidence=skipped_low_confidence,
            needs_review_count=needs_review_count,
        )

    def _apply_batch_window(self, broker_dealers: list[BrokerDealer], *, offset: int, limit: int | None) -> list[BrokerDealer]:
        if offset < 0:
            offset = 0
        window = broker_dealers[offset:]
        if limit is not None:
            return window[:limit]
        return window

    def _match_broker_dealer(
        self,
        row: dict[str, str],
        *,
        by_cik: dict[str, BrokerDealer],
        by_crd: dict[str, BrokerDealer],
        by_sec: dict[str, BrokerDealer],
    ) -> BrokerDealer | None:
        cik = (row.get("cik") or "").strip()
        if cik and cik in by_cik:
            return by_cik[cik]

        crd_number = (row.get("crd_number") or "").strip()
        if crd_number and crd_number in by_crd:
            return by_crd[crd_number]

        sec_file_number = (row.get("sec_file_number") or "").strip()
        if sec_file_number and sec_file_number in by_sec:
            return by_sec[sec_file_number]

        return None

    def _parse_optional_float(self, value: str | None) -> float | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _parse_report_date(self, value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def _build_financial_prompt(self) -> str:
        return (
            "Read the broker-dealer annual audit (X-17A-5) PDF and extract the financial metrics. "
            "Look for the Net Capital computation section (usually near the end of the filing). "
            "Extract: report_date (fiscal year-end as YYYY-MM-DD), net_capital, excess_net_capital, "
            "total_assets, and required_min_capital. "
            "IMPORTANT: Return ALL values in FULL US DOLLARS (not thousands, not millions). "
            "If the document says 'in thousands' multiply by 1,000. If it says 'in millions' multiply by 1,000,000. "
            "For example, if the PDF shows '$3,970' in millions, return 3970000000. "
            "Use null for values not explicitly stated. "
            "Assign a confidence_score between 0 and 1."
        )

    async def _refresh_broker_dealer_rollups(self, db: AsyncSession, broker_dealers: list[BrokerDealer]) -> None:
        # Filter to parsed rows only. needs_review rows carry below-threshold
        # confidence; feeding them into yoy/health rollups would corrupt the
        # master-list numbers with low-quality data. The review-queue surface
        # (Phase 2B-bis) reads needs_review rows directly, not through this
        # rollup.
        metrics_by_bd: dict[int, list[FinancialMetric]] = {}
        all_metrics = (
            await db.execute(
                select(FinancialMetric)
                .where(FinancialMetric.extraction_status == STATUS_PARSED)
                .order_by(FinancialMetric.report_date.desc())
            )
        ).scalars().all()
        for metric in all_metrics:
            metrics_by_bd.setdefault(metric.bd_id, []).append(metric)

        for broker_dealer in broker_dealers:
            metrics = metrics_by_bd.get(broker_dealer.id, [])
            if not metrics:
                broker_dealer.required_min_capital = None
                broker_dealer.latest_net_capital = None
                broker_dealer.latest_excess_net_capital = None
                broker_dealer.latest_total_assets = None
                broker_dealer.yoy_growth = None
                broker_dealer.health_status = None
                continue

            ordered = sorted(metrics, key=lambda metric: metric.report_date, reverse=True)
            latest = ordered[0]
            yoy_growth = calculate_yoy_growth(ordered)
            broker_dealer.required_min_capital = float(latest.required_min_capital) if latest.required_min_capital is not None else None
            broker_dealer.latest_net_capital = float(latest.net_capital)
            broker_dealer.latest_excess_net_capital = (
                float(latest.excess_net_capital) if latest.excess_net_capital is not None else None
            )
            broker_dealer.latest_total_assets = float(latest.total_assets) if latest.total_assets is not None else None
            broker_dealer.yoy_growth = yoy_growth
            broker_dealer.health_status = classify_health_status(
                latest_net_capital=float(latest.net_capital),
                required_min_capital=float(latest.required_min_capital) if latest.required_min_capital is not None else None,
                yoy_growth=yoy_growth,
            )
