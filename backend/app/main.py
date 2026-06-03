from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.services.alert_events import alert_event_bus
from app.services.pipeline_reaper import reap_stale_refresh_runs

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _configure_root_logging() -> None:
    """Send root-logger output to stdout so application ``logger.info`` calls
    surface in Cloud Run logs.

    Without this, only the ``uvicorn`` / ``uvicorn.access`` loggers (which
    configure their own handlers) write anywhere visible; every
    ``logging.getLogger(__name__).info(...)`` call from app code is silently
    dropped because root has no handler. Result: the only signal you can
    grep for in Cloud Run is the access log + uncaught exception tracebacks,
    which makes incidents like "did the webhook actually update any rows?"
    much harder to diagnose than they need to be.

    ``basicConfig(force=False)`` is a no-op when root already has handlers
    (the case under pytest's ``caplog`` fixture and any future uvicorn
    config that wires root). Library namespaces that are gratuitously noisy
    at INFO are bumped to WARNING so the signal stays usable.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
        force=False,
    )
    for noisy in ("sqlalchemy.engine", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_root_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: the SQLAlchemy engine is already constructed at module import
    # time (see app.db.session). httpx clients are per-request — each call
    # site uses `async with httpx.AsyncClient(...) as client`, so there are
    # no long-lived HTTP pools to initialize here.
    #
    # The alert_event_bus listener task holds one dedicated psycopg3
    # connection per Cloud Run instance (outside the SQLAlchemy pool) for
    # LISTEN filing_alerts_new so SSE subscribers on any instance see new
    # filing alerts regardless of which instance ran the upsert.
    listener_task = asyncio.create_task(alert_event_bus.run_listener())

    # Recover refresh-on-visit runs orphaned by a previous instance's
    # shutdown/replacement. Their in-process BackgroundTask died without
    # finalizing the PipelineRun row, leaving it stuck "running" — which the
    # detail page's in-flight guard would otherwise re-attach to forever.
    # Best-effort: a failure here must never block the app from serving.
    try:
        async with SessionLocal() as db:
            reaped = await reap_stale_refresh_runs(db)
        if reaped:
            logger.info("Startup reaper marked %d stale refresh run(s) as failed", reaped)
    except Exception:  # noqa: BLE001
        logger.warning("Startup reaper failed", exc_info=True)

    try:
        yield
    finally:
        listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener_task
        # Shutdown: drain the async SQLAlchemy engine so Neon observes clean
        # TCP FIN rather than having to reclaim the connections via idle
        # detection on Cloud Run revision swap / scale-down.
        # Ref: .claude/focus-fix/diagnosis.md §9 ticket S-3.
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001
            logger.warning("Engine dispose raised during shutdown", exc_info=True)


app = FastAPI(
    title=settings.app_name,
    docs_url=f"{settings.api_v1_prefix}/docs",
    openapi_url=f"{settings.api_v1_prefix}/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def root_health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router, prefix=settings.api_v1_prefix)
