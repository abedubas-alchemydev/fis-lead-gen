from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.db.session import engine

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: the SQLAlchemy engine is already constructed at module import
    # time (see app.db.session). httpx clients are per-request — each call
    # site uses `async with httpx.AsyncClient(...) as client`, so there are
    # no long-lived HTTP pools to initialize here.
    try:
        yield
    finally:
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
