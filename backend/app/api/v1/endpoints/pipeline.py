from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.pipeline import CompetitorProvidersResponse, PipelineStatusResponse, PipelineTriggerResponse
from app.services.auth import get_current_user
from app.services.broker_dealers import BrokerDealerRepository
from app.services.pipeline import ClearingPipelineService

router = APIRouter(prefix="/pipeline/clearing")
repository = BrokerDealerRepository()
pipeline_service = ClearingPipelineService()


def _ensure_admin(current_user: AuthenticatedUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")


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
