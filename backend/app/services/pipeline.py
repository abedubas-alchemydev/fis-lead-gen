from __future__ import annotations

import json
from datetime import datetime, timezone
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer
from app.models.clearing_arrangement import ClearingArrangement
from app.models.pipeline_run import PipelineRun
from app.services.broker_dealers import (
    BrokerDealerRepository,
    apply_clearing_rollup,
    pick_authoritative_arrangement,
)
from app.services.classification import apply_classification_to_all
from app.services.clearing_validator import (
    ClearingSignals,
    format_signals_for_prompt,
    load_clearing_signals,
    validate_clearing,
)
from app.services.competitors import CompetitorProviderService
from app.services.extraction_status import classify_pipeline_exception
from app.services.pdf_downloader import PdfDownloaderService, pdf_tempdir
from app.services.pdf_processor import PdfProcessorService

logger = logging.getLogger(__name__)


class ClearingPipelineService:
    def __init__(self) -> None:
        self.repository = BrokerDealerRepository()
        self.downloader = PdfDownloaderService()
        self.processor = PdfProcessorService()
        self.competitors = CompetitorProviderService()

    async def run(
        self,
        db: AsyncSession,
        *,
        trigger_source: str = "manual",
        only_failed: bool = False,
        only_null_partner: bool = False,
        only_needs_review: bool = False,
        target_bd_ids: list[int] | None = None,
    ) -> PipelineRun:
        # Targeted-rerun selector flags are mutually exclusive — only one
        # custom universe at a time. The sum check guards against silent
        # overlap if a future flag is added without revisiting the matrix.
        # ``target_bd_ids`` is the explicit-IDs override and supersedes all
        # selector flags when supplied (operator already chose the universe).
        if sum([only_failed, only_null_partner, only_needs_review]) > 1:
            raise ValueError(
                "only_failed, only_null_partner, and only_needs_review are mutually exclusive."
            )
        if target_bd_ids is not None:
            fetched = (
                await db.execute(
                    select(BrokerDealer).where(BrokerDealer.id.in_(target_bd_ids))
                )
            ).scalars().all()
            # Preserve caller-supplied order so operator-visible sequencing
            # (e.g., the master-list page's name-ASC display order) is
            # honored end-to-end. Drops any ids that don't resolve to a row.
            by_id = {bd.id: bd for bd in fetched}
            broker_dealers = [by_id[i] for i in target_bd_ids if i in by_id]
        elif only_failed:
            broker_dealers = (await db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))).scalars().all()
            failed_ids = await self.repository.list_failed_clearing_broker_dealer_ids(db)
            broker_dealers = [item for item in broker_dealers if item.id in failed_ids]
        elif only_null_partner:
            broker_dealers = await self._select_null_partner_targets(db)
        elif only_needs_review:
            broker_dealers = await self._select_needs_review_targets(db)
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
                # ``no-extract`` aggregates non-"parsed" outcomes — primarily
                # source-side absences (``missing_pdf``: firm has no X-17A-5
                # on EDGAR) and low-confidence extractions
                # (``needs_review``). Real script crashes show up as
                # ``Clearing extraction failed for broker-dealer X`` lines via
                # ``logger.exception`` in ``_extract_one_bd``; they are NOT
                # counted here as "no-extract" without also leaving a stack
                # trace for the operator to inspect.
                logger.info(
                    "Clearing pipeline progress: %d/%d (parsed: %d, no-extract: %d)",
                    bd_index + 1, total_bds, pipeline_run.success_count, pipeline_run.failure_count,
                )
            result = await self._extract_one_bd(broker_dealer, pipeline_run.id, competitors)
            extraction_results.append(result)
            pipeline_run.processed_items += 1
            if result["extraction_status"] == "parsed":
                pipeline_run.success_count += 1
            else:
                pipeline_run.failure_count += 1

        pipeline_run.status = "completed_with_errors" if pipeline_run.failure_count else "completed"
        pipeline_run.completed_at = datetime.now(timezone.utc)
        provider_descriptor = settings.llm_provider
        if settings.llm_provider == "openai":
            provider_descriptor = f"openai:{settings.openai_pdf_model}"
        pipeline_run.notes = (
            f"Processed {pipeline_run.processed_items} filings via {provider_descriptor}. "
            f"Parsed: {pipeline_run.success_count}. "
            f"No-extract (missing_pdf / needs_review / pipeline_error): {pipeline_run.failure_count}."
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

    async def _extract_one_bd(
        self,
        broker_dealer: BrokerDealer,
        pipeline_run_id: int,
        competitors: list,
        *,
        signals: ClearingSignals | None = None,
    ) -> dict[str, object]:
        """Run clearing extraction for a single BD and return an upsert-ready
        dict. Always returns a dict — failures land as ``missing_pdf`` /
        ``pipeline_error`` records that the caller upserts the same way as
        successes. Caller is responsible for incrementing pipeline_run
        counters based on ``result["extraction_status"]``.
        """
        try:
            with pdf_tempdir(prefix="clearing_extract_") as tmp_dir:
                pdf_record = await self.downloader.download_latest_x17a5_pdf(
                    broker_dealer, tmp_dir
                )
                if pdf_record is None:
                    return {
                        "bd_id": broker_dealer.id,
                        "pipeline_run_id": pipeline_run_id,
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
                        "clearing_statement_text": None,
                    }

                signals_text = (
                    format_signals_for_prompt(signals) if signals is not None else None
                )
                parsed = await self.processor.process_downloaded_pdf(
                    pdf_record, signals_text=signals_text
                )

                clearing_type = parsed.clearing_type
                clearing_partner = parsed.clearing_partner
                extraction_status = parsed.extraction_status
                extraction_notes = parsed.extraction_notes
                is_verified = False
                # Deterministic guardrail: reconcile Gemini's label against the
                # 15c3-1 capital tier + clearing-agency membership + FINRA
                # partner. Confident corrections are stamped is_verified so the
                # rollup won't clobber them; unresolved contradictions go to
                # needs_review.
                if signals is not None and clearing_type:
                    decision = validate_clearing(
                        clearing_type=clearing_type,
                        clearing_partner=clearing_partner,
                        confidence=float(parsed.extraction_confidence or 0.0),
                        signals=signals,
                    )
                    if decision.action != "pass":
                        clearing_type = decision.clearing_type
                        clearing_partner = decision.clearing_partner
                        extraction_notes = (
                            f"[clearing_validator:{decision.action}] "
                            f"{decision.rationale} | {extraction_notes or ''}"
                        ).strip()
                        if decision.corrected:
                            is_verified = True
                            extraction_status = "parsed"
                        elif decision.needs_review:
                            extraction_status = "needs_review"

                return {
                    "bd_id": parsed.bd_id,
                    "pipeline_run_id": pipeline_run_id,
                    "filing_year": parsed.filing_year,
                    "report_date": parsed.report_date,
                    "source_filing_url": parsed.source_filing_url,
                    "source_pdf_url": parsed.source_pdf_url,
                    "local_document_path": parsed.local_document_path,
                    "clearing_partner": clearing_partner,
                    "normalized_partner": self.repository.normalize_partner_name(clearing_partner),
                    "clearing_type": clearing_type,
                    "agreement_date": parsed.agreement_date,
                    "extraction_confidence": parsed.extraction_confidence,
                    "extraction_status": extraction_status,
                    "extraction_notes": extraction_notes,
                    "is_competitor": self.repository.match_competitor(clearing_partner, competitors),
                    "is_verified": is_verified,
                    "extracted_at": parsed.extracted_at,
                    "clearing_statement_text": parsed.clearing_statement_text,
                }
        except Exception as exc:
            logger.exception("Clearing extraction failed for broker-dealer %s", broker_dealer.id)
            # Map the raw exception to a sanitized status + note (#399).
            # The classifier distinguishes transient network failures from
            # other pipeline errors so the gap-fill runner can auto-retry
            # the former, and ensures the persisted note never contains a
            # raw OS-level error string that the UI would surface verbatim.
            status, note = classify_pipeline_exception(exc)
            return {
                "bd_id": broker_dealer.id,
                "pipeline_run_id": pipeline_run_id,
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
                "extraction_status": status,
                "extraction_notes": note,
                "is_competitor": False,
                "is_verified": False,
                "extracted_at": datetime.now(timezone.utc),
                "clearing_statement_text": None,
            }

    async def extract_clearing_for_broker_dealer(
        self,
        bd_id: int,
        *,
        pipeline_run_id: int,
        trigger_source: str = "manual_single",
    ) -> None:
        """Single-firm clearing extraction. Mirrors
        ``FocusReportService.load_financial_metrics_for_broker_dealer`` so the
        refresh-all orchestrator can wrap it as a child sub-pipeline (see
        ``_run_refresh_clearing`` in ``refresh_all_orchestrator.py``).

        Caller creates the ``PipelineRun`` row in 'queued' status and passes
        its id. This method drives it through 'running' to a terminal state
        and writes a structured ``notes`` JSON the parent can summarize.

        Skips the global side-effects ``run()`` performs at the end of the
        batch path (``apply_classification_to_all`` + ``refresh_lead_scores``)
        — those are wasteful per-firm. The orchestrator's health-check
        sub-pipeline already re-derives ``clearing_classification`` per BD,
        and the bulk gap-fill runner re-scores once at the end.
        """
        async with SessionLocal() as db:
            run = await db.get(PipelineRun, pipeline_run_id)
            if run is not None:
                run.status = "running"
                await db.commit()

        async with SessionLocal() as db:
            bd = await db.get(BrokerDealer, bd_id)
            if bd is None:
                async with SessionLocal() as fail_db:
                    run = await fail_db.get(PipelineRun, pipeline_run_id)
                    if run is not None:
                        run.status = "failed"
                        run.processed_items = 1
                        run.failure_count = 1
                        run.completed_at = datetime.now(timezone.utc)
                        run.notes = json.dumps(
                            {"summary": f"Broker-dealer {bd_id} not found."}
                        )
                        await fail_db.commit()
                return
            await self.competitors.seed_defaults(db)
            competitors = await self.competitors.list_active(db)
            signals = await load_clearing_signals(db, bd)

        extraction_result = await self._extract_one_bd(
            bd, pipeline_run_id, competitors, signals=signals
        )

        async with SessionLocal() as write_db:
            await self.repository.upsert_clearing_arrangements(
                write_db, [extraction_result]
            )
            await write_db.commit()
            await self._refresh_clearing_rollup_for_bd(write_db, bd_id)
            await write_db.commit()

            # Unify: keep the clearing_classification column in lock-step with
            # the validated current_clearing_type so the two columns stop
            # disagreeing (unknown/missing -> unknown).
            bd_row = await write_db.get(BrokerDealer, bd_id)
            if bd_row is not None:
                ctype_final = bd_row.current_clearing_type
                bd_row.clearing_classification = (
                    ctype_final
                    if ctype_final and ctype_final != "unknown"
                    else "unknown"
                )
                await write_db.commit()

            run = await write_db.get(PipelineRun, pipeline_run_id)
            if run is None:
                return
            status = extraction_result["extraction_status"]
            partner = extraction_result.get("clearing_partner")
            ctype = extraction_result.get("clearing_type")
            if status == "parsed":
                run.status = "completed"
                run.success_count = 1
                run.failure_count = 0
                if partner:
                    summary = f"Clearing partner: {partner} ({ctype})."
                else:
                    summary = f"Clearing type: {ctype} (no third-party partner)."
            elif status == "missing_pdf":
                run.status = "completed_with_errors"
                run.success_count = 0
                run.failure_count = 1
                summary = "No X-17A-5 PDF available."
            elif status == "needs_review":
                run.status = "completed_with_errors"
                run.success_count = 0
                run.failure_count = 1
                notes = str(extraction_result.get("extraction_notes") or "")[:200]
                summary = f"Needs review: {notes}"
            elif status == "pipeline_error":
                run.status = "failed"
                run.success_count = 0
                run.failure_count = 1
                notes = str(extraction_result.get("extraction_notes") or "")[:200]
                summary = f"Pipeline error: {notes}"
            else:
                run.status = "completed_with_errors"
                run.success_count = 0
                run.failure_count = 1
                summary = f"Status: {status}."
            run.processed_items = 1
            run.completed_at = datetime.now(timezone.utc)
            run.notes = json.dumps({"summary": summary[:500]})
            await write_db.commit()

    async def _refresh_clearing_rollup_for_bd(
        self, db: AsyncSession, bd_id: int
    ) -> None:
        """Per-BD version of ``BrokerDealerRepository.refresh_clearing_rollups``.

        The repository's method scans every BD + every clearing_arrangement —
        wasteful for a single-firm refresh. This picks the authoritative
        arrangement for ``bd_id`` (verified-preferred, then most-recent) and
        updates that one BD's ``current_clearing_*`` fields.
        """
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return
        rows = (
            await db.execute(
                select(ClearingArrangement).where(ClearingArrangement.bd_id == bd_id)
            )
        ).scalars().all()
        # Verified-preferred, most-recent pick (shared with the batch rollup in
        # broker_dealers.py) so a fresh unverified re-extraction can't clobber a
        # FINRA-reconciled or validator-confirmed label.
        apply_clearing_rollup(bd, pick_authoritative_arrangement(rows))
        await db.flush()

    async def _select_null_partner_targets(self, db: AsyncSession) -> list[BrokerDealer]:
        # Backfill mode: target firms whose ``current_clearing_partner`` is
        # still NULL but for which an X-17A-5 filing is reachable (i.e. a
        # ``filings_index_url`` is on file). The default mode also gates on
        # ``filings_index_url IS NOT NULL`` indirectly via the no-row branch
        # — we make it explicit here so a backfill run does not waste Gemini
        # calls on firms with no filings index. Honors the same offset/limit
        # knobs as the default run so an operator can chunk a large backfill
        # without re-engineering the selector.
        broker_dealers = (await db.execute(
            select(BrokerDealer)
            .where(
                BrokerDealer.current_clearing_partner.is_(None),
                BrokerDealer.filings_index_url.is_not(None),
            )
            .order_by(BrokerDealer.id.asc())
        )).scalars().all()

        if settings.clearing_pipeline_offset > 0:
            broker_dealers = broker_dealers[settings.clearing_pipeline_offset :]
        if settings.clearing_pipeline_limit:
            broker_dealers = broker_dealers[: settings.clearing_pipeline_limit]

        return broker_dealers

    async def _select_needs_review_targets(self, db: AsyncSession) -> list[BrokerDealer]:
        # Targeted re-run mode for firms whose LATEST clearing extraction
        # landed as ``needs_review``. Used after a prompt change to flip the
        # fixable subset (e.g. M&A advisory firms / no-customer-accounts
        # boilerplate) without re-processing the already-parsed universe.
        #
        # The MAX(extracted_at) subquery scopes the EXISTS to the most-
        # recent clearing_arrangement row per bd_id — a firm whose newer
        # row is already 'parsed' is excluded even if an older row was
        # 'needs_review'. Honors the same offset/limit knobs as the other
        # selectors so an operator can chunk a large re-run.
        latest_extracted_at_subq = (
            select(func.max(ClearingArrangement.extracted_at))
            .where(ClearingArrangement.bd_id == BrokerDealer.id)
            .correlate(BrokerDealer)
            .scalar_subquery()
        )
        broker_dealers = (await db.execute(
            select(BrokerDealer)
            .where(
                select(ClearingArrangement.id)
                .where(
                    ClearingArrangement.bd_id == BrokerDealer.id,
                    ClearingArrangement.extraction_status == "needs_review",
                    ClearingArrangement.extracted_at == latest_extracted_at_subq,
                )
                .exists()
            )
            .order_by(BrokerDealer.id.asc())
        )).scalars().all()

        if settings.clearing_pipeline_offset > 0:
            broker_dealers = broker_dealers[settings.clearing_pipeline_offset :]
        if settings.clearing_pipeline_limit:
            broker_dealers = broker_dealers[: settings.clearing_pipeline_limit]

        return broker_dealers

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
