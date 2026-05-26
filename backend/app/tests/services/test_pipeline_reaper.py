"""Tests for the stale-refresh-run reaper.

Uses a real in-memory SQLite database (via aiosqlite) so the actual SQL
filter — pipeline_name in the refresh family, status running/queued, started
before the cutoff — is exercised, not just the Python row-mutation.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.pipeline_run import PipelineRun
from app.services.pipeline_reaper import STALE_REFRESH_RUN_AGE, reap_stale_refresh_runs


async def _make_session() -> tuple[object, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(PipelineRun.__table__.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _run(
    *,
    run_id: int,
    pipeline_name: str,
    status: str,
    minutes_ago: float,
) -> PipelineRun:
    return PipelineRun(
        id=run_id,
        pipeline_name=pipeline_name,
        trigger_source="test",
        status=status,
        notes=json.dumps({"bd_id": run_id}),
        started_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


async def test_reaps_only_stale_refresh_family_runs() -> None:
    engine, maker = await _make_session()
    try:
        async with maker() as db:
            db.add_all(
                [
                    # Should be reaped: stale refresh-family parent + child.
                    _run(run_id=1, pipeline_name="investment_advisor_refresh_all", status="running", minutes_ago=45),
                    _run(run_id=2, pipeline_name="financial_pdf_pipeline_single", status="running", minutes_ago=30),
                    _run(run_id=3, pipeline_name="broker_dealer_refresh_all", status="queued", minutes_ago=20),
                    # Should be left alone: recent refresh-family run (still alive).
                    _run(run_id=4, pipeline_name="broker_dealer_refresh_all", status="running", minutes_ago=1),
                    # Should be left alone: bulk pipeline, even though it's old
                    # (may legitimately run long; not in the refresh family).
                    _run(run_id=5, pipeline_name="broker_dealer_gap_fill", status="running", minutes_ago=120),
                    _run(run_id=6, pipeline_name="initial_load", status="running", minutes_ago=600),
                    # Should be left alone: already terminal.
                    _run(run_id=7, pipeline_name="broker_dealer_refresh_all", status="completed", minutes_ago=90),
                ]
            )
            await db.commit()

        async with maker() as db:
            reaped = await reap_stale_refresh_runs(db)

        assert reaped == 3

        async with maker() as db:
            rows = {r.id: r for r in (await db.execute(select(PipelineRun))).scalars().all()}

        # Stale refresh-family → failed, completed_at set, summary recorded.
        for rid in (1, 2, 3):
            assert rows[rid].status == "failed"
            assert rows[rid].completed_at is not None
            assert json.loads(rows[rid].notes)["reaped"] is True

        # Recent refresh-family, old bulk runs, and terminal run → untouched.
        assert rows[4].status == "running"
        assert rows[5].status == "running"
        assert rows[6].status == "running"
        assert rows[7].status == "completed"
    finally:
        await engine.dispose()


async def test_reaper_noop_when_nothing_stale() -> None:
    engine, maker = await _make_session()
    try:
        async with maker() as db:
            db.add(_run(run_id=1, pipeline_name="broker_dealer_refresh_all", status="running", minutes_ago=1))
            await db.commit()

        async with maker() as db:
            reaped = await reap_stale_refresh_runs(db)

        assert reaped == 0
    finally:
        await engine.dispose()


async def test_stale_threshold_is_ten_minutes() -> None:
    # Guard against an accidental change that would make the reaper too
    # aggressive (killing live runs) or too lax (leaving zombies).
    assert STALE_REFRESH_RUN_AGE == timedelta(minutes=10)
