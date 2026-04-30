from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal, get_db_session
from app.models.audit_log import AuditLog
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.schemas.pipeline import (
    CompetitorProvidersResponse,
    PipelineStatusResponse,
    PipelineTriggerResponse,
    WipeBdDataRequest,
    WipeBdDataResponse,
)
from app.services.auth import _ensure_admin_or_scheduler_sa, get_current_user
from app.services.broker_dealers import BrokerDealerRepository
from app.services.filing_monitor import FilingMonitorService
from app.services.pipeline import ClearingPipelineService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline/clearing")
scheduled_router = APIRouter(prefix="/pipeline/run")
admin_destructive_router = APIRouter(prefix="/pipeline")
repository = BrokerDealerRepository()
pipeline_service = ClearingPipelineService()
filing_monitor_service = FilingMonitorService()


def _ensure_admin(current_user: AuthenticatedUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")


def _trigger_response(run: PipelineRun) -> PipelineTriggerResponse:
    return PipelineTriggerResponse(
        run_id=run.id,
        status=run.status,
        total_items=run.total_items,
        processed_items=run.processed_items,
        success_count=run.success_count,
        failure_count=run.failure_count,
    )


async def _create_queued_run(db: AsyncSession, *, pipeline_name: str, trigger_source: str, notes: str) -> PipelineRun:
    """Create and commit a ``status="queued"`` PipelineRun stub for long
    pipelines that run in a background task. Returns the persisted row so the
    handler can hand the ``run_id`` back to the caller immediately."""
    run = PipelineRun(
        pipeline_name=pipeline_name,
        trigger_source=trigger_source,
        status="queued",
        total_items=0,
        processed_items=0,
        success_count=0,
        failure_count=0,
        notes=notes,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _run_populate_all_background(run_id: int, trigger_source: str) -> None:
    """Background task: filing monitor → clearing pipeline → lead-score
    refresh. Updates the queued PipelineRun row with consolidated counts and
    final status. All work uses a fresh SessionLocal so it does not depend on
    the request session, which is closed by the time this fires."""
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            logger.error("populate-all background: PipelineRun %d not found.", run_id)
            return
        run.status = "running"
        await db.commit()

    notes_parts: list[str] = []
    total_success = 0
    total_failure = 0
    total_items = 0
    failed = False

    try:
        async with SessionLocal() as db:
            filing_run = await filing_monitor_service.run(
                db, trigger_source=f"populate_all:{trigger_source}"
            )
            total_items += filing_run.total_items
            total_success += filing_run.success_count
            total_failure += filing_run.failure_count
            notes_parts.append(
                f"filing_monitor run #{filing_run.id} {filing_run.status}"
            )

        async with SessionLocal() as db:
            clearing_run = await pipeline_service.run(
                db, trigger_source=f"populate_all:{trigger_source}"
            )
            total_items += clearing_run.total_items
            total_success += clearing_run.success_count
            total_failure += clearing_run.failure_count
            notes_parts.append(
                f"clearing run #{clearing_run.id} {clearing_run.status}"
            )

        async with SessionLocal() as db:
            await repository.refresh_lead_scores(db)
            await db.commit()
            notes_parts.append("lead_scores refreshed")
    except Exception as exc:
        failed = True
        logger.exception("populate-all background failed: %s", exc)
        notes_parts.append(f"failed: {exc}")

    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            return
        run.total_items = total_items
        run.processed_items = total_success + total_failure
        run.success_count = total_success
        run.failure_count = total_failure
        run.status = "failed" if failed else "completed"
        run.completed_at = datetime.now(timezone.utc)
        run.notes = "; ".join(notes_parts) if notes_parts else run.notes
        await db.commit()


async def _run_initial_load_background(run_id: int, trigger_source: str) -> None:
    """Background task: full FINRA + SEC EDGAR re-harvest, merge, then the
    same downstream filing monitor that :mod:`scripts.initial_load` runs.
    Mirrors the script in-process so the Cloud Scheduler-triggered request
    can return 200 immediately while the harvest finishes server-side."""
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            logger.error("initial-load background: PipelineRun %d not found.", run_id)
            return
        run.status = "running"
        await db.commit()

    notes_parts: list[str] = []
    failed = False

    try:
        # Lazy imports so cold-start of the API process stays light and
        # circular imports through model registration don't trip at module
        # load time.
        from app.core.config import settings as app_settings
        from app.services.data_merge import BrokerDealerMergeService
        from app.services.edgar import EdgarService
        from app.services.finra import FinraService

        finra_service = FinraService()
        edgar_service = EdgarService()
        merge_service = BrokerDealerMergeService()

        finra_records = await finra_service.fetch_broker_dealers(
            limit=app_settings.initial_load_limit
        )
        finra_records = await finra_service.enrich_with_detail(finra_records)
        sec_file_numbers = [r.sec_file_number for r in finra_records if r.sec_file_number]
        edgar_records = await edgar_service.fetch_records_for_sec_numbers(sec_file_numbers)
        merged = merge_service.merge(finra_records, edgar_records)

        async with SessionLocal() as db:
            await repository.upsert_many(db, merged)
            await db.commit()

        notes_parts.append(
            f"finra={len(finra_records)} edgar={len(edgar_records)} merged={len(merged)}"
        )

        async with SessionLocal() as db:
            filing_run = await filing_monitor_service.run(
                db, trigger_source=f"initial_load:{trigger_source}"
            )
            notes_parts.append(
                f"filing_monitor run #{filing_run.id} {filing_run.status}"
            )

        async with SessionLocal() as db:
            await repository.refresh_lead_scores(db)
            await db.commit()
            notes_parts.append("lead_scores refreshed")
    except Exception as exc:
        failed = True
        logger.exception("initial-load background failed: %s", exc)
        notes_parts.append(f"failed: {exc}")

    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            return
        run.status = "failed" if failed else "completed"
        run.completed_at = datetime.now(timezone.utc)
        run.notes = "; ".join(notes_parts) if notes_parts else run.notes
        await db.commit()


@router.get("", response_model=PipelineStatusResponse)
async def get_pipeline_status(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineStatusResponse:
    _ensure_admin(current_user)
    return PipelineStatusResponse(
        latest_run=await repository.get_latest_pipeline_run(db),
        recent_runs=await repository.list_recent_pipeline_runs(db),
        recent_failures=await repository.list_recent_clearing_failures(db),
    )


@router.post("/run", response_model=PipelineTriggerResponse)
async def trigger_pipeline_run(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    _ensure_admin(current_user)
    run = await pipeline_service.run(db, trigger_source=f"manual:{current_user.email}")
    return PipelineTriggerResponse(
        run_id=run.id,
        status=run.status,
        total_items=run.total_items,
        processed_items=run.processed_items,
        success_count=run.success_count,
        failure_count=run.failure_count,
    )


@router.post("/retry-failed", response_model=PipelineTriggerResponse)
async def retry_failed_extractions(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    _ensure_admin(current_user)
    run = await pipeline_service.run(db, trigger_source=f"retry_failed:{current_user.email}", only_failed=True)
    return PipelineTriggerResponse(
        run_id=run.id,
        status=run.status,
        total_items=run.total_items,
        processed_items=run.processed_items,
        success_count=run.success_count,
        failure_count=run.failure_count,
    )


@router.get("/competitors", response_model=CompetitorProvidersResponse)
async def list_competitors(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CompetitorProvidersResponse:
    _ensure_admin(current_user)
    return CompetitorProvidersResponse(items=await repository.list_competitor_providers(db))


# ───────────────────────────────────────────────────────────────────────────
# Tier 2 scheduled trigger endpoints (admin cookie OR Cloud Scheduler SA OIDC)
# ───────────────────────────────────────────────────────────────────────────


@scheduled_router.post("/filing-monitor", response_model=PipelineTriggerResponse)
async def run_filing_monitor(
    caller: str = Depends(_ensure_admin_or_scheduler_sa),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    """Trigger the daily SEC filing monitor.

    Synchronous: typical run is ~5–15 minutes which fits inside Cloud Run's
    request timeout. The handler awaits :class:`FilingMonitorService.run`
    and returns the completed PipelineRun shape so Cloud Scheduler logs the
    final outcome alongside the 200.
    """
    run = await filing_monitor_service.run(db, trigger_source=f"scheduled:{caller}")
    return _trigger_response(run)


@scheduled_router.post("/populate-all", response_model=PipelineTriggerResponse)
async def run_populate_all(
    background_tasks: BackgroundTasks,
    caller: str = Depends(_ensure_admin_or_scheduler_sa),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    """Trigger the full daily orchestration: filing monitor → clearing
    pipeline → lead-score refresh.

    Asynchronous: end-to-end runtime is 30–90 minutes which exceeds Cloud
    Scheduler's 30-minute attempt deadline. The handler creates a
    ``status="queued"`` PipelineRun row, schedules the work as a FastAPI
    BackgroundTask, and returns 200 immediately so the scheduler attempt
    succeeds. Progress and final status are tracked on the row.
    """
    run = await _create_queued_run(
        db,
        pipeline_name="populate_all",
        trigger_source=f"scheduled:{caller}",
        notes="Queued from /pipeline/run/populate-all.",
    )
    background_tasks.add_task(_run_populate_all_background, run.id, caller)
    return _trigger_response(run)


@scheduled_router.post("/initial-load", response_model=PipelineTriggerResponse)
async def run_initial_load(
    background_tasks: BackgroundTasks,
    caller: str = Depends(_ensure_admin_or_scheduler_sa),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    """Trigger the FINRA + SEC EDGAR re-bootstrap.

    Asynchronous: harvest-and-merge runs 15–30 minutes. Same queued-run
    pattern as ``/populate-all`` so Cloud Scheduler gets a fast 200.
    """
    run = await _create_queued_run(
        db,
        pipeline_name="initial_load",
        trigger_source=f"scheduled:{caller}",
        notes="Queued from /pipeline/run/initial-load.",
    )
    background_tasks.add_task(_run_initial_load_background, run.id, caller)
    return _trigger_response(run)


# ───────────────────────────────────────────────────────────────────────────
# Destructive admin-only endpoint (cookie auth ONLY — no SA OIDC fallback)
# ───────────────────────────────────────────────────────────────────────────


WIPE_BD_DATA_TABLES: list[str] = [
    "filing_alerts",
    "financial_metrics",
    "clearing_arrangements",
    "executive_contacts",
    "favorite_list_item",
    "broker_dealers",
]


@admin_destructive_router.post(
    "/wipe-bd-data",
    response_model=WipeBdDataResponse,
    status_code=200,
)
async def wipe_bd_data(
    request: WipeBdDataRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> WipeBdDataResponse:
    """TRUNCATE the broker-dealer data tables for a Fresh Regen.

    Wipes ``filing_alerts``, ``financial_metrics``, ``clearing_arrangements``,
    ``executive_contacts``, ``favorite_list_item``, ``broker_dealers``. Users,
    sessions, audit logs, and ``favorite_list`` parents are preserved.

    Strict guards:

    * Only admin role on a BetterAuth session cookie may call this. The
      Cloud Scheduler SA OIDC dual path used by the ``/run/*`` endpoints is
      explicitly **not** wired up here — wipes are too destructive to let
      a service-account bearer trigger them.
    * The request body's ``confirmation`` must equal
      ``WIPE-BD-DATA-YYYY-MM-DD`` where ``YYYY-MM-DD`` is today's UTC date.
      Yesterday's confirmation strings are rejected so a copy-pasted curl or
      a replayed request can't accidentally wipe.
    * The ``audit_log`` row is INSERTed (via ``flush``) **before** the
      TRUNCATE statements run. Both happen in the same transaction — if any
      TRUNCATE fails, the audit row rolls back too, so there is no
      "wipe without an audit trail" state.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )

    today_utc = datetime.now(timezone.utc).date().isoformat()
    expected = f"WIPE-BD-DATA-{today_utc}"
    if request.confirmation != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Confirmation must be exactly '{expected}'. "
                f"Got: '{request.confirmation}'."
            ),
        )

    audit = AuditLog(
        user_id=current_user.id,
        action="bd_data_wiped",
        details=json.dumps(
            {
                "confirmation": request.confirmation,
                "tables": list(WIPE_BD_DATA_TABLES),
            }
        ),
    )
    db.add(audit)
    await db.flush()
    audit_log_id = str(audit.id)

    rows_before_result = await db.execute(text("SELECT COUNT(*) FROM broker_dealers"))
    rows_before = rows_before_result.scalar() or 0

    for tbl in WIPE_BD_DATA_TABLES:
        await db.execute(text(f"TRUNCATE TABLE {tbl} CASCADE"))

    await db.commit()

    logger.warning(
        "BD data wiped by admin %s (audit_log_id=%s, rows_before=%d)",
        current_user.email,
        audit_log_id,
        rows_before,
    )

    return WipeBdDataResponse(
        affected_tables=list(WIPE_BD_DATA_TABLES),
        rows_deleted=rows_before,
        audit_log_id=audit_log_id,
        wiped_at=datetime.now(timezone.utc),
    )
