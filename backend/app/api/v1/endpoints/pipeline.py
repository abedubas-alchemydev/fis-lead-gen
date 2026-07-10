from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal, get_db_session
from app.models.audit_log import AuditLog
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.schemas.pipeline import (
    ActiveRefreshResponse,
    CompetitorProvidersResponse,
    PipelineRunStatusResponse,
    PipelineStatusResponse,
    PipelineTriggerResponse,
    SetFilesApiFlagRequest,
    SetFilesApiFlagResponse,
    WipeBdDataRequest,
    WipeBdDataResponse,
)
from app.api.v1.endpoints.broker_dealers import schedule_auto_refresh_financials_batch
from app.services.auth import _ensure_admin_or_scheduler_sa, get_current_user
from app.services.broker_dealers import BrokerDealerRepository
from app.services.chatbot_semantic import run_post_pipeline_embed_backfill
from app.services.investment_advisors import InvestmentAdvisorRepository
from app.services.cloud_run_client import (
    CloudRunUpdateError,
    update_env_var as cloud_run_update_env_var,
)
from app.services.deficiency_watcher import DeficiencyWatcherService
from app.services.filing_monitor import FilingMonitorService
from app.services.form4_watcher import Form4WatcherService
from app.services.pipeline import ClearingPipelineService
from app.services.pipeline_names import FAVORITE_LIST_ENRICH_PIPELINE
from app.services.registration_watcher import RegistrationWatcherService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline/clearing")
scheduled_router = APIRouter(prefix="/pipeline/run")
admin_destructive_router = APIRouter(prefix="/pipeline")
# Non-admin pipeline status queries (any authenticated user). Kept on its
# own router so it doesn't share a path namespace with the per-run
# ``/pipeline/run/{run_id}`` reader, which would shadow string suffixes
# trying to parse as int.
status_router = APIRouter(prefix="/pipeline")


# Pipeline names whose existence is reassuring rather than alarming for a
# client to see surfaced on the dashboard. Only PARENT runs — sub-pipeline
# names (``*_owners_officers``, ``*_resolve_website``, etc.) fire as
# children of these and would duplicate the signal.
USER_FACING_REFRESH_PIPELINES: frozenset[str] = frozenset(
    {
        "investment_advisor_gap_fill",
        "broker_dealer_gap_fill",
        "investment_advisor_refresh_all",
        "broker_dealer_refresh_all",
        "populate_all",
        "daily_filing_monitor",
    }
)
repository = BrokerDealerRepository()
advisor_repository = InvestmentAdvisorRepository()
pipeline_service = ClearingPipelineService()
filing_monitor_service = FilingMonitorService()
registration_watcher_service = RegistrationWatcherService()
deficiency_watcher_service = DeficiencyWatcherService()
form4_watcher_service = Form4WatcherService()

# FINRA firms processed per chunk in the OOM-hardened initial-load harvest.
# The full ~3.2k-firm set was previously enriched (heavy Form BD fields:
# executive_officers / direct_owners / firm_operations_text) + merged +
# upserted all at once, peaking ~2.1 GiB and OOM-killing prod at the old
# 2 GiB ceiling. Streaming in slices of this size — enrich -> merge_chunk ->
# upsert + commit -> drop — caps peak memory at roughly one chunk's worth.
_INITIAL_LOAD_CHUNK_SIZE = 250


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
            # ``run`` returns (run, auto_extract_bd_ids) since the watched-firm
            # auto re-extraction landed (#397); this call site kept the old
            # single-value shape and made every populate-all fail on
            # ``tuple.total_items``. The auto-extract hook needs a request's
            # BackgroundTasks to schedule against, which doesn't exist inside
            # a background task — the scheduled /filing-monitor trigger owns
            # that hook, so populate-all just drops the ids.
            filing_run, _ = await filing_monitor_service.run(
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

    # Incremental Doxie embed pass: new/changed firms become semantically
    # searchable without the manual admin backfill. Deliberately after the
    # final status commit, and the hook swallows its own errors + opens its
    # own session — embed duration or failure can never affect the run row.
    await run_post_pipeline_embed_backfill()


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
        from app.services.service_models import MergeQAReport

        finra_service = FinraService()
        edgar_service = EdgarService()
        merge_service = BrokerDealerMergeService()

        # ── Light harvest ── fetch the CHEAP pre-enrich FINRA list (search
        # pages only, no Form BD PDF detail) plus the full EDGAR set, once.
        # enrich_with_detail is what balloons each record with the heavy Form
        # BD fields, so it is deferred into the per-chunk loop below and never
        # held for the whole ~3.2k-firm set at once.
        finra_records = await finra_service.fetch_broker_dealers(
            limit=app_settings.initial_load_limit
        )
        sec_file_numbers = [r.sec_file_number for r in finra_records if r.sec_file_number]
        edgar_records = await edgar_service.fetch_records_for_sec_numbers(sec_file_numbers)

        # ── Build the EDGAR index ONCE, then stream FINRA past it in chunks ──
        # report / seen_sec_numbers / matched_edgar_secs are the cross-chunk
        # accumulators that make N merge_chunk() calls identical to a single
        # merge() over the whole set. build_edgar_index records EDGAR-side
        # bad/duplicate rows into the SAME report so the QA counts match a
        # one-shot merge(); finalize() runs once at the very end so
        # EDGAR-unresolved rows are reported a single time, not per chunk.
        total_finra = len(finra_records)
        report = MergeQAReport(
            edgar_input_count=len(edgar_records),
            finra_input_count=total_finra,
        )
        edgar_index = merge_service.build_edgar_index(edgar_records, report)
        seen_sec_numbers: set[str] = set()
        matched_edgar_secs: set[str] = set()

        # Drain finra_records from the front so each processed (now heavily
        # enriched) slice becomes collectable as soon as its chunk is
        # committed — the parent list never pins the whole enriched set, which
        # is precisely what blew past the 2 GiB ceiling before.
        while finra_records:
            finra_chunk = finra_records[:_INITIAL_LOAD_CHUNK_SIZE]
            del finra_records[:_INITIAL_LOAD_CHUNK_SIZE]

            # enrich_with_detail mutates the slice in place with the heavy Form
            # BD fields; scope those allocations to this iteration.
            await finra_service.enrich_with_detail(finra_chunk)
            chunk_merged = merge_service.merge_chunk(
                finra_chunk,
                edgar_index,
                seen_sec_numbers=seen_sec_numbers,
                matched_edgar_secs=matched_edgar_secs,
                report=report,
            )

            # Commit per chunk: upsert_many already batches at 500 internally,
            # and the commit is REQUIRED so the no-CIK select-then-write path
            # sees earlier chunks' rows when matching later chunks.
            # replace_dataset is deliberately NOT used here — it DELETEs the
            # whole table first, which would wipe prior chunks.
            async with SessionLocal() as db:
                await repository.upsert_many(db, chunk_merged)
                await db.commit()

            # Release this window's heavy records before the next slice.
            del finra_chunk, chunk_merged

        # ── EDGAR-unresolved accounting + output_count, exactly once ──
        merge_service.finalize(edgar_index, matched_edgar_secs, report)

        notes_parts.append(
            f"finra={report.finra_input_count} edgar={report.edgar_input_count} "
            f"merged={report.output_count} both={report.matched_both_count} "
            f"finra_only={report.finra_only_count} "
            f"edgar_unresolved={report.edgar_unresolved_count}"
        )

        async with SessionLocal() as db:
            # ``run`` returns (run, auto_extract_bd_ids); initial-load drops the
            # ids. The auto re-extraction hook needs a request's BackgroundTasks
            # to schedule against, which a background task doesn't have — the
            # scheduled /filing-monitor trigger owns that hook. Same unpack the
            # populate-all background does above; without it ``filing_run`` was a
            # tuple and ``filing_run.id`` raised, marking the run "failed".
            filing_run, _ = await filing_monitor_service.run(
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
    background_tasks: BackgroundTasks,
    caller: str = Depends(_ensure_admin_or_scheduler_sa),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    """Trigger the daily SEC filing monitor.

    Synchronous: typical run is ~5–15 minutes which fits inside Cloud Run's
    request timeout. The handler awaits :class:`FilingMonitorService.run`
    and returns the completed PipelineRun shape so Cloud Scheduler logs the
    final outcome alongside the 200.

    After the monitor commits, any newly-inserted X-17A-5 alerts whose firm
    is "watched" (favorited by any user OR ``lead_priority='hot'``) get
    a per-firm financial re-extraction queued as BackgroundTasks. The
    extractions run after this response returns; their PipelineRuns are
    visible in the existing pipelines admin UI tagged
    ``trigger_source="auto:filing_monitor:<this run id>"``.
    """
    run, auto_extract_bd_ids = await filing_monitor_service.run(
        db, trigger_source=f"scheduled:{caller}"
    )
    await schedule_auto_refresh_financials_batch(
        db,
        background_tasks,
        auto_extract_bd_ids,
        trigger_source=f"auto:filing_monitor:{run.id}",
    )
    return _trigger_response(run)


@scheduled_router.post("/registration-monitor", response_model=PipelineTriggerResponse)
async def run_registration_monitor(
    caller: str = Depends(_ensure_admin_or_scheduler_sa),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    """Trigger the FINRA Form BD registration watcher.

    Restores the PRD-spec'd "Registrations" alert category (was empty
    pre-2026-05-04 because the old filing monitor read EDGAR which
    doesn't carry Form BD signals; see ``services/registration_watcher.py``
    for the full rationale).

    Synchronous: the watcher is a single SQL scan over ``broker_dealers``
    plus an idempotent ``filing_alerts`` upsert — completes in seconds,
    well inside Cloud Run's request timeout.
    """
    run = await registration_watcher_service.run(
        db, trigger_source=f"scheduled:{caller}"
    )
    return _trigger_response(run)


@scheduled_router.post("/deficiency-monitor", response_model=PipelineTriggerResponse)
async def run_deficiency_monitor(
    caller: str = Depends(_ensure_admin_or_scheduler_sa),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    """Trigger the SEC Rule 17a-11 deficiency-notice watcher.

    Polls SEC EDGAR full-text search for filings mentioning "17a-11" in
    the last 30 days, joins matching CIKs against ``broker_dealers``, and
    inserts ``Form 17a-11`` alerts at ``priority="critical"``. Restores
    the PRD-spec'd "Critical: Deficiencies" alert tab. See
    ``services/deficiency_watcher.py`` for why this can't be done by
    form-code matching alone.

    Synchronous: a single EFTS query (paginated up to ~100 hits per
    page; rarely more than one page given ~60 hits/year nationwide)
    plus an idempotent upsert. Completes in seconds.
    """
    run = await deficiency_watcher_service.run(
        db, trigger_source=f"scheduled:{caller}"
    )
    return _trigger_response(run)


@scheduled_router.post("/form4-watcher", response_model=PipelineTriggerResponse)
async def run_form4_watcher(
    caller: str = Depends(_ensure_admin_or_scheduler_sa),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    """Trigger the SEC Form 4 (insider transactions) watcher.

    Populates ``form4_transactions`` which backs the Investors tab.
    Streams the last ``settings.form4_lookback_days`` days of Form 4
    filings out of EDGAR's full-text search, parses each XML, and
    upserts one row per (reportingOwner × transaction) pair that
    clears the $50K value floor.

    Synchronous: typical run is 30-90 seconds (a day's worth of Form 4
    filings is ~200-500, each costs one index.json + one XML fetch at
    the 8 req/sec pacing). Well inside Cloud Run's request timeout.
    """
    run = await form4_watcher_service.run(
        db, trigger_source=f"scheduled:{caller}"
    )
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


@status_router.get("/active-refreshes", response_model=ActiveRefreshResponse)
async def get_active_refreshes(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ActiveRefreshResponse:
    """Return whether any user-visible refresh pipeline is in flight.

    Drives the dashboard's "refreshing your records" banner. Filters on
    ``USER_FACING_REFRESH_PIPELINES`` so the banner only lights up for
    parent runs the user would recognize as "data refresh" work —
    sub-pipeline children are excluded to avoid the banner flickering
    on/off as siblings complete.

    Auth: any authenticated user. The response exposes only the
    presence of in-flight work, not which firm or what fields.
    """
    stmt = (
        select(PipelineRun.started_at)
        .where(
            PipelineRun.status.in_(("running", "queued")),
            PipelineRun.pipeline_name.in_(USER_FACING_REFRESH_PIPELINES),
        )
        .order_by(PipelineRun.started_at.asc())
        .limit(1)
    )
    earliest_started_at = (await db.execute(stmt)).scalar_one_or_none()
    return ActiveRefreshResponse(
        is_active=earliest_started_at is not None,
        started_at=earliest_started_at,
    )


@scheduled_router.get("/{run_id}", response_model=PipelineRunStatusResponse)
async def get_pipeline_run_status(
    run_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineRunStatusResponse:
    """Per-run status used by FE polling.

    Returns the current state of any PipelineRun by id. Powered by the
    same row that ``POST /broker-dealers/{id}/refresh-financials``
    queues, so the FE can poll until ``status`` flips to ``completed``,
    ``completed_with_errors``, or ``failed`` and then refetch the firm
    detail to render the now-populated financial fields.

    Auth: any authenticated user — the run row only exposes status
    counters and notes, no broker-dealer PII or filing payloads. The
    one exception is ``favorite_list_enrich`` runs, whose notes carry a
    whole favorite list's firm names + membership: those are readable
    only by the owner (whose email is stamped into ``trigger_source``)
    or an admin, and 404 to anyone else. All other pipeline types stay
    readable by any authenticated user.
    """
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found.",
        )
    if (
        run.pipeline_name == FAVORITE_LIST_ENRICH_PIPELINE
        and current_user.role != "admin"
        and run.trigger_source != f"favorite_list_enrich:{current_user.email}"
    ):
        # Owner-scope bulk-enrich runs: their notes carry the whole list's
        # firm names + membership. 404 (not 403) so a non-owner can't even
        # confirm the run exists. All other pipeline types are unchanged.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found.",
        )
    return PipelineRunStatusResponse(
        run_id=run.id,
        pipeline_name=run.pipeline_name,
        status=run.status,
        total_items=run.total_items,
        processed_items=run.processed_items,
        success_count=run.success_count,
        failure_count=run.failure_count,
        notes=run.notes,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@scheduled_router.post("/initial-load", response_model=PipelineTriggerResponse)
async def run_initial_load(
    caller: str = Depends(_ensure_admin_or_scheduler_sa),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    """Trigger the FINRA + SEC EDGAR re-bootstrap.

    Synchronous: the handler awaits the harvest in-process (15–30 min)
    before returning, mirroring ``filing-monitor``. Cloud Scheduler must
    use ``--attempt-deadline=1800s``. The earlier ``BackgroundTasks``
    pattern silently dropped the work on Cloud Run because the request
    lifecycle ended before the coroutine could run — every prior staging
    ``initial_load`` run sat ``status='running'`` indefinitely.
    """
    run = await _create_queued_run(
        db,
        pipeline_name="initial_load",
        trigger_source=f"scheduled:{caller}",
        notes="Queued from /pipeline/run/initial-load.",
    )
    await _run_initial_load_background(run.id, caller)
    await db.refresh(run)
    return _trigger_response(run)


async def _run_initial_load_advisors_background(
    run_id: int, trigger_source: str
) -> None:
    """Background task: SEC IAPD bulk + EDGAR EFTS 13F-HR enumeration.

    Mirrors :mod:`scripts.initial_load_advisors` in-process so a Cloud
    Scheduler trigger can return 200 immediately while the ~5-15 minute
    download + parse + 13F walk + upsert finishes server-side.
    """
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            logger.error(
                "initial-load-advisors background: PipelineRun %d not found.", run_id
            )
            return
        run.status = "running"
        await db.commit()

    notes_parts: list[str] = []
    failed = False

    try:
        # Lazy imports — keeps cold-start of the API process light and
        # avoids any model-registration circular import surprises during
        # FastAPI startup.
        from app.core.config import settings as app_settings
        from app.services.advisor_merge import AdvisorMergeService
        from app.services.iapd import IapdService
        from app.services.thirteen_f_filter import ThirteenFFilterService

        iapd_service = IapdService()
        thirteen_f_service = ThirteenFFilterService()
        merge_service = AdvisorMergeService()

        iapd_records = await iapd_service.fetch_compilation_records(
            limit=app_settings.initial_load_advisors_limit
        )
        thirteen_f_ciks = await thirteen_f_service.fetch_recent_filer_ciks()
        merged, report = merge_service.merge(iapd_records, thirteen_f_ciks)

        async with SessionLocal() as db:
            await advisor_repository.upsert_many(db, merged)
            await db.commit()

        notes_parts.append(
            f"iapd={report.iapd_input_count} "
            f"13f_filers={report.thirteen_f_filer_count} "
            f"merged={report.merged_count} "
            f"files_13f={report.files_13f_count}"
        )
    except Exception as exc:
        failed = True
        logger.exception("initial-load-advisors background failed: %s", exc)
        notes_parts.append(f"failed: {exc}")

    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            return
        run.status = "failed" if failed else "completed"
        run.completed_at = datetime.now(timezone.utc)
        run.notes = "; ".join(notes_parts) if notes_parts else run.notes
        await db.commit()


@scheduled_router.post("/initial-load-advisors", response_model=PipelineTriggerResponse)
async def run_initial_load_advisors(
    background_tasks: BackgroundTasks,
    caller: str = Depends(_ensure_admin_or_scheduler_sa),
    db: AsyncSession = Depends(get_db_session),
) -> PipelineTriggerResponse:
    """Trigger the SEC IAPD + EDGAR 13F-HR re-bootstrap for advisors.

    Asynchronous: ~5-15 minutes for a full pass (download + parse 17k
    rows + walk weekly 13F windows + upsert ~5k rows).
    """
    run = await _create_queued_run(
        db,
        pipeline_name="initial_load_advisors",
        trigger_source=f"scheduled:{caller}",
        notes="Queued from /pipeline/run/initial-load-advisors.",
    )
    background_tasks.add_task(_run_initial_load_advisors_background, run.id, caller)
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


@admin_destructive_router.post(
    "/set-files-api-flag",
    response_model=SetFilesApiFlagResponse,
    status_code=200,
)
async def set_files_api_flag(
    request: SetFilesApiFlagRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SetFilesApiFlagResponse:
    """Flip ``LLM_USE_FILES_API`` on the live ``fis-backend`` service.

    The Fresh Regen UI uses this so an operator can toggle the LLM
    Files-API code path in-flow, instead of running ``gcloud run
    services update --update-env-vars=...`` between Phase 0 and the
    actual regeneration. The endpoint:

    1. Rejects anything that isn't an admin role on a BetterAuth
       session cookie. The Cloud Scheduler SA OIDC dual-path used by
       the ``/run/*`` endpoints is intentionally **not** wired here —
       flipping a global LLM flag mid-flight is destructive of running
       configuration.
    2. Inserts an ``audit_log`` row (action ``files_api_flag_flipped``)
       and **commits** before the Cloud Run update fires. Cloud Run
       updates are not transactional with our DB, so the audit row is
       committed in its own transaction so a paper trail exists even
       if the Cloud Run rollout fails afterwards.
    3. Calls :func:`app.services.cloud_run_client.update_env_var`
       which has its own application-layer allowlist — only the env
       name ``LLM_USE_FILES_API`` is accepted. Any other name raises
       ``ValueError`` before any RPC. This is the guard that keeps
       the runtime SA's ``roles/run.developer`` IAM grant narrow.
    4. Polls the Cloud Run service until the new revision is ready
       (or 120s timeout). Returns the new revision name so the FE can
       proceed to the regen step with confidence the flag is live.

    Surfaces:

    * ``403`` if the caller is not an admin.
    * ``503`` if the Cloud Run rollout fails or times out (the audit
      row is still committed in this case — paper trail intact, the
      flip just didn't take effect).
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )

    audit = AuditLog(
        user_id=current_user.id,
        action="files_api_flag_flipped",
        details=json.dumps({"enabled": request.enabled}),
    )
    db.add(audit)
    await db.commit()

    new_value = "true" if request.enabled else "false"
    try:
        result = await cloud_run_update_env_var(
            name="LLM_USE_FILES_API",
            value=new_value,
        )
    except CloudRunUpdateError as exc:
        logger.error("Cloud Run rollout failed for set-files-api-flag: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Cloud Run rollout failed: {exc}",
        ) from exc
    except ValueError as exc:
        # Defense in depth — the handler hardcodes the env name to
        # LLM_USE_FILES_API, so the wrapper's allowlist check should
        # never reject it. If it does, something has been altered
        # (e.g. someone narrowed ALLOWED_ENV_VARS without updating
        # this caller). Surface as 500 rather than swallowing it.
        logger.error("cloud_run_client allowlist violation: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal allowlist violation.",
        ) from exc

    ready_at = datetime.fromisoformat(result["ready_at"])
    return SetFilesApiFlagResponse(
        previous_state=result["previous_value"] == "true",
        new_state=result["new_value"] == "true",
        revision_name=result["revision_name"],
        ready_at=ready_at,
    )
