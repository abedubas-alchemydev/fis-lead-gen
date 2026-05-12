from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,
    # Cloud Run runs 2 uvicorn workers per container (see backend/Dockerfile).
    # pool_size=5 + max_overflow=10 = up to 15 connections per worker, 30 per
    # container — sized to stay under Neon's pooled-connection ceiling while
    # absorbing burst from the Tier-2 pipeline fan-out. pool_recycle keeps
    # idle conns under Neon's idle-timeout so we never hand a dead socket to
    # a request handler.
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
