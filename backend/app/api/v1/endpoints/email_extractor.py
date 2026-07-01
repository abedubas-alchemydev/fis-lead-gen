from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import get_db_session
from app.models.discovered_email import DiscoveredEmail
from app.models.email_verification import EmailVerification
from app.models.extraction_run import ExtractionRun, RunStatus
from app.models.verification_run import VerificationRun
from app.core.feature_permissions import EMAIL_EXTRACTOR
from app.schemas.auth import AuthenticatedUser
from app.schemas.email_extractor import (
    DiscoveredEmailResponse,
    EnrichAllResponse,
    ScanCreateRequest,
    ScanListItem,
    ScanResponse,
    VerificationRunCreateResponse,
    VerificationRunResponse,
    VerifyRequest,
    VerifyResultItem,
)
from app.services.auth import ensure_feature, get_current_user
from app.services.email_extractor import aggregator
from app.services.email_extractor.enrichment import (
    NOT_CONFIGURED_MESSAGE,
    NOT_FOUND_MESSAGE,
    EnrichmentError,
    any_provider_configured,
    enrich_discovered_email,
)
from app.services.email_extractor.bulk_enrichment import run_bulk_enrichment
from app.services.email_extractor.verification_runner import run_smtp_verification

router = APIRouter(prefix="/email-extractor", tags=["email-extractor"])


def _require_email_extractor(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    ensure_feature(user, EMAIL_EXTRACTOR)
    return user


@router.post("/scans", status_code=status.HTTP_202_ACCEPTED, response_model=ScanResponse)
async def create_scan(
    payload: ScanCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(_require_email_extractor),
) -> ExtractionRun:
    scan = ExtractionRun(
        domain=payload.domain,
        person_name=payload.person_name,
        bd_id=payload.bd_id,
        advisor_id=payload.advisor_id,
        status=RunStatus.queued.value,
    )
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    background_tasks.add_task(aggregator.run, scan.id)
    return scan


@router.get("/scans", response_model=list[ScanListItem])
async def list_scans(
    bd_id: int | None = Query(default=None, description="Filter scans tied to a specific broker-dealer."),
    advisor_id: int | None = Query(default=None, description="Filter scans tied to a specific investment advisor."),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(_require_email_extractor),
) -> list[ExtractionRun]:
    """Recent scans across all users, sorted by created_at desc.

    Powers the 'Recent scans' list on /email-extractor and the per-firm
    history section on broker-dealer and advisor detail pages.
    """
    stmt = select(ExtractionRun).order_by(ExtractionRun.created_at.desc())
    if bd_id is not None:
        stmt = stmt.where(ExtractionRun.bd_id == bd_id)
    if advisor_id is not None:
        stmt = stmt.where(ExtractionRun.advisor_id == advisor_id)
    stmt = stmt.offset(offset).limit(limit)
    return (await db.execute(stmt)).scalars().all()


@router.post(
    "/discovered-emails/{discovered_email_id}/enrich",
    response_model=DiscoveredEmailResponse,
)
async def enrich_email(
    discovered_email_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(_require_email_extractor),
) -> DiscoveredEmail:
    """Enrich a discovered email with the person behind it -- name, title,
    company, LinkedIn, an alternate email, and phone -- by walking the
    enrichment provider chain. Writes the merged result onto the row itself.

    Errors stay provider-agnostic on the wire: a failure surfaces a generic
    message, never the specific provider that failed.
    """
    try:
        return await enrich_discovered_email(db, discovered_email_id)
    except EnrichmentError as exc:
        message = str(exc)
        if message == NOT_FOUND_MESSAGE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="This email no longer exists.",
            ) from exc
        if message == NOT_CONFIGURED_MESSAGE:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Enrichment is not configured on this deployment.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't complete enrichment — please try again.",
        ) from exc


@router.post(
    "/scans/{run_id}/enrich-all",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EnrichAllResponse,
)
async def enrich_all_for_scan(
    run_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(_require_email_extractor),
) -> EnrichAllResponse:
    """Enqueue per-row enrichment for every unenriched email in a scan.

    Returns 202 immediately with a snapshot of what's queued so the
    frontend can display "Enriching N..." before polling
    GET /scans/{run_id} for per-row progress. Each row walks the enrichment
    provider chain; per-email failures are isolated by the background task so
    one bad row never aborts the batch.
    """
    scan = await db.get(ExtractionRun, run_id)
    if scan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scan not found")

    if not any_provider_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Enrichment is not configured on this deployment.",
        )

    total_stmt = select(func.count(DiscoveredEmail.id)).where(DiscoveredEmail.run_id == run_id)
    already_stmt = select(func.count(DiscoveredEmail.id)).where(
        DiscoveredEmail.run_id == run_id,
        DiscoveredEmail.enrichment_status == "enriched",
    )
    total = int((await db.execute(total_stmt)).scalar_one())
    already = int((await db.execute(already_stmt)).scalar_one())
    queued = total - already

    # Clear any prior cancel so a re-run isn't immediately short-circuited
    # by the bulk loop's per-row cancel check.
    if scan.enrich_cancelled_at is not None:
        scan.enrich_cancelled_at = None
        await db.commit()

    background_tasks.add_task(run_bulk_enrichment, run_id)

    return EnrichAllResponse(
        scan_id=run_id,
        candidates_total=total,
        candidates_skipped_already_enriched=already,
        candidates_queued=queued,
        status="queued",
    )


@router.post(
    "/scans/{run_id}/enrich-all/cancel",
    response_model=ScanResponse,
)
async def cancel_enrich_all_for_scan(
    run_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(_require_email_extractor),
) -> ExtractionRun:
    """Stamp ``enrich_cancelled_at`` so the bulk enrichment loop exits
    on its next iteration.

    Idempotent: re-calling with an already-cancelled scan leaves the
    original timestamp in place. Already-enriched rows are preserved by
    the loop's design -- nothing here rolls back per-row state.
    """
    stmt = (
        select(ExtractionRun)
        .where(ExtractionRun.id == run_id)
        .options(selectinload(ExtractionRun.discovered_emails))
    )
    scan = (await db.execute(stmt)).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scan not found")

    if scan.enrich_cancelled_at is None:
        scan.enrich_cancelled_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(scan)

    return scan


@router.get("/scans/{run_id}", response_model=ScanResponse)
async def get_scan(
    run_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(_require_email_extractor),
) -> ExtractionRun:
    stmt = (
        select(ExtractionRun).where(ExtractionRun.id == run_id).options(selectinload(ExtractionRun.discovered_emails))
    )
    result = await db.execute(stmt)
    scan = result.scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scan not found")
    return scan


@router.post(
    "/verify",
    response_model=VerificationRunCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def verify_emails(
    payload: VerifyRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(_require_email_extractor),
) -> VerificationRunCreateResponse:
    if len(payload.email_ids) > settings.smtp_verify_max_batch:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"batch size {len(payload.email_ids)} exceeds cap {settings.smtp_verify_max_batch}",
        )

    existing = await db.execute(select(DiscoveredEmail.id).where(DiscoveredEmail.id.in_(payload.email_ids)))
    if existing.scalars().first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no matching email_ids")

    run = VerificationRun(
        email_ids=list(payload.email_ids),
        status=RunStatus.queued.value,
        total_items=len(payload.email_ids),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(
        run_smtp_verification,
        verify_run_id=run.id,
        email_ids=list(payload.email_ids),
    )
    return VerificationRunCreateResponse(verify_run_id=run.id, status=run.status)


@router.get("/verify-runs/{run_id}", response_model=VerificationRunResponse)
async def get_verify_run(
    run_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(_require_email_extractor),
) -> VerificationRunResponse:
    run = await db.get(VerificationRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="verify run not found")

    requested_ids: list[int] = list(run.email_ids or [])

    # Latest EmailVerification per discovered_email_id, scoped to this run's ids.
    # Postgres window function picks row_number()=1 per group ordered by checked_at desc.
    results: list[VerifyResultItem] = []
    if requested_ids:
        ranked = (
            select(
                EmailVerification.discovered_email_id.label("discovered_email_id"),
                EmailVerification.smtp_status.label("smtp_status"),
                EmailVerification.smtp_message.label("smtp_message"),
                EmailVerification.checked_at.label("checked_at"),
                DiscoveredEmail.email.label("email"),
                func.row_number()
                .over(
                    partition_by=EmailVerification.discovered_email_id,
                    order_by=EmailVerification.checked_at.desc(),
                )
                .label("rn"),
            )
            .join(DiscoveredEmail, DiscoveredEmail.id == EmailVerification.discovered_email_id)
            .where(EmailVerification.discovered_email_id.in_(requested_ids))
            .subquery()
        )

        latest_stmt = select(
            ranked.c.discovered_email_id,
            ranked.c.smtp_status,
            ranked.c.smtp_message,
            ranked.c.checked_at,
            ranked.c.email,
        ).where(ranked.c.rn == 1)
        rows = (await db.execute(latest_stmt)).all()

        by_email_id: dict[int, VerifyResultItem] = {}
        for row in rows:
            by_email_id[row.discovered_email_id] = VerifyResultItem(
                email_id=row.discovered_email_id,
                email=row.email,
                smtp_status=row.smtp_status,
                smtp_message=row.smtp_message,
                checked_at=row.checked_at,
            )

        results = [by_email_id[eid] for eid in requested_ids if eid in by_email_id]

    return VerificationRunResponse(
        id=run.id,
        status=run.status,
        total_items=run.total_items,
        processed_items=run.processed_items,
        success_count=run.success_count,
        failure_count=run.failure_count,
        error_message=run.error_message,
        created_at=run.created_at,
        completed_at=run.completed_at,
        results=results,
    )
