from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer
from app.models.clearing_arrangement import ClearingArrangement
from app.models.pipeline_run import PipelineRun
from app.services.broker_dealers import BrokerDealerRepository
from app.services.classification import apply_classification_to_all
from app.services.competitors import CompetitorProviderService
from app.services.pdf_downloader import PdfDownloaderService, pdf_tempdir
from app.services.pdf_processor import PdfProcessorService

logger = logging.getLogger(__name__)


class ClearingPipelineService:
    def __init__(self) -> None:
        self.repository = BrokerDealerRepository()
        self.downloader = PdfDownloaderService()
        self.processor = PdfProcessorService()
        self.competitors = CompetitorProviderService()

    async def run(self, db: AsyncSession, *, trigger_source: str = "manual", only_failed: bool = False) -> PipelineRun:
        if only_failed:
            broker_dealers = (await db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))).scalars().all()
            failed_ids = await self.repository.list_failed_clearing_broker_dealer_ids(db)
            broker_dealers = [item for item in broker_dealers if item.id in failed_ids]
        else:
            broker_dealers = await self._select_default_targets(db)

        pipeline_run = PipelineRun(
            pipeline_name="clearing_pdf_pipeline",
            trigger_source=trigger_source,
            status="running",
            total_items=len(broker_dealers),
            processed_items=0,
            success_count=0,
            failure_count=0,
        )
        db.add(pipeline_run)
        await db.flush()
        run_id = pipeline_run.id

        await self.competitors.seed_defaults(db)
        competitors = await self.competitors.list_active(db)
        await db.commit()
        extraction_results: list[dict[str, object]] = []
        total_bds = len(broker_dealers)

        for bd_index, broker_dealer in enumerate(broker_dealers):
            if (bd_index + 1) % 10 == 0 or bd_index == 0:
                logger.info(
                    "Clearing pipeline progress: %d/%d (success: %d, failed: %d)",
                    bd_index + 1, total_bds, pipeline_run.success_count, pipeline_run.failure_count,
                )
            try:
                # Per-iteration tempdir replaces the persistent PDF cache.
                # Download + LLM parse both happen inside this block; on exit
                # the PDF is wiped so the container footprint stays flat.
                # Re-extraction re-downloads (the extracted values are
                # already persisted to the DB).
                with pdf_tempdir(prefix="clearing_extract_") as tmp_dir:
                    pdf_record = await self.downloader.download_latest_x17a5_pdf(
                        broker_dealer, tmp_dir
                    )
                    if pdf_record is None:
                        extraction_results.append(
                            {
                                "bd_id": broker_dealer.id,
                                "pipeline_run_id": pipeline_run.id,
                                "filing_year": datetime.now(timezone.utc).year,
                                "report_date": broker_dealer.last_filing_date,
                                "source_filing_url": broker_dealer.filings_index_url,
                                "source_pdf_url": None,
                                "local_document_path": None,
                                "clearing_partner": None,
                                "normalized_partner": None,
                                "clearing_type": "unknown",
                                "agreement_date": None,
                                "extraction_confidence": 0.0,
                                "extraction_status": "missing_pdf",
                                "extraction_notes": "No X-17A-5 PDF available for this broker-dealer.",
                                "is_competitor": False,
                                "is_verified": False,
                                "extracted_at": datetime.now(timezone.utc),
                            }
                        )
                        pipeline_run.processed_items += 1
                        pipeline_run.failure_count += 1
                        continue

                    parsed = await self.processor.process_downloaded_pdf(pdf_record)
                    extraction_results.append(
                        {
                            "bd_id": parsed.bd_id,
                            "pipeline_run_id": pipeline_run.id,
                            "filing_year": parsed.filing_year,
                            "report_date": parsed.report_date,
                            "source_filing_url": parsed.source_filing_url,
                            "source_pdf_url": parsed.source_pdf_url,
                            "local_document_path": parsed.local_document_path,
                            "clearing_partner": parsed.clearing_partner,
                            "normalized_partner": self.repository.normalize_partner_name(parsed.clearing_partner),
                            "clearing_type": parsed.clearing_type,
                            "agreement_date": parsed.agreement_date,
                            "extraction_confidence": parsed.extraction_confidence,
                            "extraction_status": parsed.extraction_status,
                            "extraction_notes": parsed.extraction_notes,
                            "is_competitor": self.repository.match_competitor(parsed.clearing_partner, competitors),
                            "is_verified": False,
                            "extracted_at": parsed.extracted_at,
                        }
                    )
                    pipeline_run.processed_items += 1
                    if parsed.extraction_status == "parsed":
                        pipeline_run.success_count += 1
                    else:
                        pipeline_run.failure_count += 1
            except Exception as exc:
                logger.exception("Clearing extraction failed for broker-dealer %s", broker_dealer.id)
                extraction_results.append(
                    {
                        "bd_id": broker_dealer.id,
                        "pipeline_run_id": pipeline_run.id,
                        "filing_year": datetime.now(timezone.utc).year,
                        "report_date": broker_dealer.last_filing_date,
                        "source_filing_url": broker_dealer.filings_index_url,
                        "source_pdf_url": None,
                        "local_document_path": None,
                        "clearing_partner": None,
                        "normalized_partner": None,
                        "clearing_type": "unknown",
                        "agreement_date": None,
                        "extraction_confidence": 0.0,
                        "extraction_status": "pipeline_error",
                        "extraction_notes": str(exc)[:1000],
                        "is_competitor": False,
                        "is_verified": False,
                        "extracted_at": datetime.now(timezone.utc),
                    }
                )
                pipeline_run.processed_items += 1
                pipeline_run.failure_count += 1

        pipeline_run.status = "completed_with_errors" if pipeline_run.failure_count else "completed"
        pipeline_run.completed_at = datetime.now(timezone.utc)
        provider_descriptor = settings.llm_provider
        if settings.llm_provider == "openai":
            provider_descriptor = f"openai:{settings.openai_pdf_model}"
        pipeline_run.notes = (
            f"Processed {pipeline_run.processed_items} filings via {provider_descriptor}. "
            f"Successful extractions: {pipeline_run.success_count}. Flagged or failed: {pipeline_run.failure_count}."
        )

        async with SessionLocal() as write_db:
            await self.repository.upsert_clearing_arrangements(write_db, extraction_results)
            await write_db.commit()
            await self.repository.refresh_clearing_rollups(write_db)
            await apply_classification_to_all(write_db)
            await self.repository.refresh_lead_scores(write_db)

            persisted_run = await write_db.get(PipelineRun, run_id)
            if persisted_run is None:
                raise RuntimeError(f"Pipeline run {run_id} could not be reloaded for clearing finalization.")

            persisted_run.total_items = pipeline_run.total_items
            persisted_run.processed_items = pipeline_run.processed_items
            persisted_run.success_count = pipeline_run.success_count
            persisted_run.failure_count = pipeline_run.failure_count
            persisted_run.status = pipeline_run.status
            persisted_run.completed_at = pipeline_run.completed_at
            persisted_run.notes = pipeline_run.notes
            await write_db.commit()
            await write_db.refresh(persisted_run)
            return persisted_run

    async def _select_default_targets(self, db: AsyncSession) -> list[BrokerDealer]:
        # Prioritize firms that have a filings_index_url but no clearing row
        # yet, so small batches land on firms we have never attempted. Firms
        # that already have at least one clearing_arrangement row come after
        # as a refresh tail. Mirrors the financial pipeline's Fix E ordering
        # (focus_reports.py) so a starvation loop against the same top-100
        # firms cannot keep the other ~2,900 URL-bearing firms permanently
        # unattempted.
        bds_without_clearing = (await db.execute(
            select(BrokerDealer)
            .where(
                BrokerDealer.filings_index_url.is_not(None),
                ~select(ClearingArrangement.id)
                .where(ClearingArrangement.bd_id == BrokerDealer.id)
                .exists(),
            )
            .order_by(BrokerDealer.id.asc())
        )).scalars().all()

        bds_with_clearing = (await db.execute(
            select(BrokerDealer)
            .where(
                select(ClearingArrangement.id)
                .where(ClearingArrangement.bd_id == BrokerDealer.id)
                .exists()
            )
            .order_by(BrokerDealer.id.asc())
        )).scalars().all()

        broker_dealers = bds_without_clearing + bds_with_clearing

        if settings.clearing_pipeline_offset > 0:
            broker_dealers = broker_dealers[settings.clearing_pipeline_offset :]
        if settings.clearing_pipeline_limit:
            broker_dealers = broker_dealers[: settings.clearing_pipeline_limit]

        return broker_dealers
