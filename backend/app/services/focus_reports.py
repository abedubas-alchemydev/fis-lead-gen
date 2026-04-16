from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer

logger = logging.getLogger(__name__)
from app.models.financial_metric import FinancialMetric
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiResponsesClient,
)
from app.services.pdf_downloader import PdfDownloaderService
from app.services.scoring import calculate_yoy_growth, classify_health_status
from app.services.service_models import FinancialMetricRecord


class FocusReportService:
    def __init__(self) -> None:
        self.downloader = PdfDownloaderService()
        self.gemini_client = GeminiResponsesClient()

    async def load_financial_metrics(self, db: AsyncSession) -> int:
        # Prioritize BDs that already have financial data (proven to have filings)
        # then fill remaining slots with BDs that have filing URLs but no data yet.
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

        # Prioritized list: firms with proven filings first
        broker_dealers = bds_with_data + bds_with_urls_no_data
        if not broker_dealers:
            return 0
        await db.commit()
        target_broker_dealers = self._apply_batch_window(
            broker_dealers,
            offset=settings.financial_pipeline_offset,
            limit=settings.financial_pipeline_limit,
        )

        incremental_target_ids: list[int] | None = None
        records = await self._load_live_records(target_broker_dealers)
        if settings.financial_pipeline_limit and not settings.focus_reports_csv_path:
            incremental_target_ids = [broker_dealer.id for broker_dealer in target_broker_dealers]

        async with SessionLocal() as write_db:
            if incremental_target_ids is not None:
                await write_db.execute(delete(FinancialMetric).where(FinancialMetric.bd_id.in_(incremental_target_ids)))
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
        return len(records)

    async def _load_live_records(self, broker_dealers: list[BrokerDealer]) -> list[FinancialMetricRecord]:
        csv_records = self._load_live_records_from_csv(broker_dealers)
        if csv_records:
            logger.info("Loaded %d financial records from CSV.", len(csv_records))
            return csv_records

        if settings.focus_reports_csv_path:
            logger.warning(
                "CSV path configured (%s) but produced zero records — falling through to PDF extraction.",
                settings.focus_reports_csv_path,
            )

        records = await self._extract_live_records_from_pdfs(broker_dealers)
        if not records:
            logger.warning(
                "Financial extraction produced zero records for %d broker-dealers. "
                "Check that GEMINI_API_KEY is set and broker-dealers have filings_index_url populated.",
                len(broker_dealers),
            )
        return records


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

    async def _extract_live_records_from_pdfs(self, broker_dealers: list[BrokerDealer]) -> list[FinancialMetricRecord]:
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

            # Download the 2 most recent X-17A-5 PDFs for multi-year data
            try:
                pdf_records = await self.downloader.download_recent_x17a5_pdfs(broker_dealer, count=2)
            except Exception as exc:
                logger.debug("PDF download failed for BD %d (%s): %s", broker_dealer.id, broker_dealer.name, exc)
                skipped_extraction_error += 1
                continue

            if not pdf_records:
                skipped_no_pdf += 1
                continue

            added_for_bd = 0
            seen_dates: set[str] = set()
            for pdf_record in pdf_records:
                try:
                    extraction = await self.gemini_client.extract_financial_data(
                        pdf_bytes_base64=pdf_record.bytes_base64,
                        prompt=self._build_financial_prompt(),
                    )
                except (GeminiConfigurationError, GeminiExtractionError) as exc:
                    logger.debug("Gemini extraction failed for BD %d year %d: %s", broker_dealer.id, pdf_record.filing_year, exc)
                    continue
                except Exception as exc:
                    logger.debug("Unexpected error for BD %d year %d: %s", broker_dealer.id, pdf_record.filing_year, exc)
                    continue

                if (
                    extraction.net_capital is None
                    or extraction.confidence_score < settings.financial_extraction_min_confidence
                ):
                    continue

                report_date = self._parse_report_date(extraction.report_date) or pdf_record.report_date
                if report_date is None:
                    continue

                # Avoid duplicates for the same date
                date_key = report_date.isoformat()
                if date_key in seen_dates:
                    continue
                seen_dates.add(date_key)

                records.append(
                    FinancialMetricRecord(
                        bd_id=broker_dealer.id,
                        report_date=report_date,
                        net_capital=extraction.net_capital,
                        excess_net_capital=extraction.excess_net_capital,
                        total_assets=extraction.total_assets,
                        required_min_capital=extraction.required_min_capital,
                        source_filing_url=pdf_record.source_pdf_url or pdf_record.source_filing_url,
                    )
                )
                added_for_bd += 1

            if added_for_bd == 0:
                skipped_low_confidence += 1

        logger.info(
            "Financial extraction complete: %d/%d extracted. Skipped: %d no URL, %d no PDF, %d errors, %d low confidence.",
            len(records), total, skipped_no_url, skipped_no_pdf, skipped_extraction_error, skipped_low_confidence,
        )
        return records

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
        metrics_by_bd: dict[int, list[FinancialMetric]] = {}
        all_metrics = (await db.execute(select(FinancialMetric).order_by(FinancialMetric.report_date.desc()))).scalars().all()
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
