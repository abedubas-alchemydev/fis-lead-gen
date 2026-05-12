from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.broker_dealers import schedule_auto_refresh_financials_batch

from app.db.session import SessionLocal, get_db_session
from app.models.filing_alert import FilingAlert
from app.schemas.alerts import (
    AlertListItem,
    AlertListResponse,
    AlertReadResponse,
    AlertsBulkReadResponse,
    FilingMonitorRunResponse,
)
from app.schemas.auth import AuthenticatedUser
from app.services.alert_events import alert_event_bus
from app.services.alerts import AlertRepository
from app.services.auth import get_current_user
from app.services.filing_monitor import FilingMonitorService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts")
repository = AlertRepository()
filing_monitor_service = FilingMonitorService()

# Cloud Run HTTP/1 caps requests at 60 min. Self-close at 55 min so the client
# EventSource reconnects with Last-Event-ID rather than getting a hard 503.
_STREAM_MAX_SECONDS = 55 * 60
_STREAM_KEEPALIVE_SECONDS = 25
# Cap replayed alerts on reconnect — if the client missed more than this we
# defer to its REST fallback rather than blast a giant catch-up batch.
_STREAM_REPLAY_LIMIT = 100


def _parse_values(values: list[str] | None) -> list[str]:
    if not values:
        return []

    parsed: list[str] = []
    for value in values:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return parsed


@router.get("", response_model=AlertListResponse)
async def list_alerts(
    form_type: list[str] | None = Query(default=None),
    priority: list[str] | None = Query(default=None),
    read: bool | None = Query(default=None),
    broker_dealer_id: int | None = Query(default=None),
    category: Literal["form_bd", "deficiency", "all"] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AlertListResponse:
    return await repository.list_alerts(
        db,
        form_types=_parse_values(form_type),
        priorities=_parse_values(priority),
        is_read=read,
        broker_dealer_id=broker_dealer_id,
        category=category,
        page=page,
        limit=limit,
    )


@router.patch("/{alert_id}/read", response_model=AlertReadResponse)
async def mark_alert_read(
    alert_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AlertReadResponse:
    alert = await repository.mark_alert_read(db, alert_id, is_read=True)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found.")
    return AlertReadResponse(id=alert.id, is_read=alert.is_read)


@router.post("/mark-all-read", response_model=AlertsBulkReadResponse)
async def mark_all_alerts_read(
    form_type: list[str] | None = Query(default=None),
    priority: list[str] | None = Query(default=None),
    broker_dealer_id: int | None = Query(default=None),
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AlertsBulkReadResponse:
    updated_count = await repository.mark_all_read(
        db,
        form_types=_parse_values(form_type),
        priorities=_parse_values(priority),
        broker_dealer_id=broker_dealer_id,
    )
    return AlertsBulkReadResponse(updated_count=updated_count)


@router.post("/monitor/run", response_model=FilingMonitorRunResponse)
async def run_filing_monitor(
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FilingMonitorRunResponse:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")

    run, auto_extract_bd_ids = await filing_monitor_service.run(
        db, trigger_source="manual"
    )
    await schedule_auto_refresh_financials_batch(
        db,
        background_tasks,
        auto_extract_bd_ids,
        trigger_source=f"auto:filing_monitor:{run.id}",
    )
    return FilingMonitorRunResponse(
        run_id=run.id,
        total_items=run.total_items,
        success_count=run.success_count,
        failure_count=run.failure_count,
        status=run.status,
    )


async def _authenticate_stream_request(request: Request) -> AuthenticatedUser:
    """Authenticate the SSE handshake using a short-lived DB session.

    We deliberately do NOT use ``Depends(get_db_session)`` on the stream
    endpoint: FastAPI keeps dependency-injected sessions open for the
    lifetime of the response, and the SSE response lives for up to 55
    minutes. A pinned session per subscriber would exhaust the SQLAlchemy
    pool (pool_size=5 + overflow=10) at 15 concurrent dashboards. Instead we
    spend one short session here for the auth check and let the streaming
    generator open fresh sessions only on the moments it needs to hydrate
    alerts.
    """
    async with SessionLocal() as db:
        return await get_current_user(request, db)


async def _hydrate_alerts(alert_ids: list[int]) -> list[AlertListItem]:
    """Fetch the AlertListItem projection for a batch of newly-inserted ids.

    Goes through ``AlertRepository.list_alerts`` so the priority-override
    rule (Form BD → "high", see :mod:`app.services.alerts`) stays the
    single source of truth across REST and SSE.
    """
    if not alert_ids:
        return []
    async with SessionLocal() as db:
        # ``list_alerts`` orders by filed_at DESC; we pass an inflated limit
        # then filter to the requested ids in memory. The batches are tiny
        # (one filing monitor run yields at most a few dozen new alerts).
        response = await repository.list_alerts(
            db,
            form_types=[],
            priorities=[],
            is_read=None,
            broker_dealer_id=None,
            page=1,
            limit=max(len(alert_ids), 1),
        )
    requested = set(alert_ids)
    return [item for item in response.items if item.id in requested]


async def _replay_missed_alerts(last_event_id: int) -> list[AlertListItem]:
    async with SessionLocal() as db:
        stmt = (
            select(FilingAlert.id)
            .where(FilingAlert.id > last_event_id)
            .order_by(FilingAlert.id.asc())
            .limit(_STREAM_REPLAY_LIMIT)
        )
        ids = [row[0] for row in (await db.execute(stmt)).all()]
    return await _hydrate_alerts(ids)


def _format_event(event: str, data: dict, event_id: int | None = None) -> str:
    parts = [f"event: {event}"]
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"data: {json.dumps(data, default=str, separators=(',', ':'))}")
    return "\n".join(parts) + "\n\n"


async def _alert_event_stream(last_event_id_header: str | None) -> AsyncIterator[str]:
    started = time.monotonic()
    # Register the subscriber FIRST. The bus's subscribe() registers
    # synchronously, so any NOTIFY that lands between now and the consumer
    # loop below is queued instead of dropped. (If we subscribed after the
    # ready frame yield, the handler would suspend at the yield with no
    # subscriber registered, and a NOTIFY arriving during that window
    # would be lost.)
    subscription = alert_event_bus.subscribe()
    try:
        # Emit a ready frame first thing so the proxy flushes its first
        # chunk and the browser hands control back to the EventSource
        # onopen handler.
        yield _format_event(
            "ready",
            {"ts": datetime.now(timezone.utc).isoformat()},
        )

        # Replay missed events if the client supplied a Last-Event-ID.
        last_id_int: int | None = None
        if last_event_id_header:
            try:
                last_id_int = int(last_event_id_header)
            except ValueError:
                last_id_int = None
        if last_id_int is not None:
            replayed = await _replay_missed_alerts(last_id_int)
            if replayed:
                yield _format_event(
                    "alert.inserted",
                    {"items": [item.model_dump(mode="json") for item in replayed]},
                    event_id=max(item.id for item in replayed),
                )

        while True:
            if time.monotonic() - started > _STREAM_MAX_SECONDS:
                # Polite close before Cloud Run's 60-min ceiling — the client
                # reconnects with Last-Event-ID and resumes seamlessly.
                return
            payload = await subscription.get(timeout=_STREAM_KEEPALIVE_SECONDS)
            if payload is None:
                # Comment frame keeps proxies and browser tabs from treating
                # the connection as idle.
                yield ": keepalive\n\n"
                continue
            if payload.get("type") != "alert.inserted":
                continue
            ids = payload.get("alert_ids") or []
            items = await _hydrate_alerts(list(ids))
            if not items:
                continue
            yield _format_event(
                "alert.inserted",
                {"items": [item.model_dump(mode="json") for item in items]},
                event_id=max(item.id for item in items),
            )
    finally:
        subscription.close()


@router.get("/stream")
async def stream_alerts(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    # Auth runs against a short-lived session; see _authenticate_stream_request.
    await _authenticate_stream_request(request)
    return StreamingResponse(
        _alert_event_stream(last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Nginx / Cloud Run frontends honor X-Accel-Buffering to disable
            # response buffering — without it the proxy may hold chunks until
            # a buffer fills, defeating the whole point of SSE.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
