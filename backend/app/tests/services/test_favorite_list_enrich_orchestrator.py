"""Unit tests for the favorite-list bulk-enrich orchestrator.

Mostly DB-free: the worker loop, gates, officer-seed dedupe, per-item roll-up,
and summary are exercised by mocking the DB/dispatch boundary (``_write_parent``
/ ``_dispatch_item``) so no Postgres or provider is required. The lifetime
heartbeat's read-modify-write (:func:`_touch_heartbeat`) is the one exception:
it is exercised against a real in-memory SQLite database (via aiosqlite, same
pattern as ``test_pipeline_reaper.py``) so the actual notes JSON round-trip and
"don't clobber per-firm progress" invariant are proven, not just mocked.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.services import favorite_list_enrich_orchestrator as o


async def _make_sqlite_session() -> tuple[object, async_sessionmaker[AsyncSession]]:
    """In-memory SQLite engine + sessionmaker with just the pipeline_runs table.

    Mirrors ``test_pipeline_reaper._make_session`` so the heartbeat RMW is
    exercised against a real DB round-trip without needing Postgres.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(PipelineRun.__table__.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _user(*, role: str = "viewer", features: list[str] | None = None) -> AuthenticatedUser:
    return AuthenticatedUser(
        id="u1",
        name="Test User",
        email="test@example.com",
        role=role,
        feature_permissions=features or [],
        session_expires_at=datetime(2999, 1, 1, tzinfo=timezone.utc),
    )


def _item(entity_type: str, entity_id: int = 1, name: str = "Firm") -> o.EnrichItem:
    return o.EnrichItem(item_id=entity_id, entity_type=entity_type, entity_id=entity_id, name=name)


# ── feature gate (admin bypass mirrors services.auth.ensure_feature) ──────────


def test_feature_allowed_admin_bypasses_every_gate() -> None:
    admin = _user(role="admin", features=[])
    assert o._feature_allowed(admin, "master_list") is True
    assert o._feature_allowed(admin, "email_extractor") is True


def test_feature_allowed_viewer_needs_explicit_grant() -> None:
    viewer = _user(role="viewer", features=["master_list"])
    assert o._feature_allowed(viewer, "master_list") is True
    assert o._feature_allowed(viewer, "banks") is False


# ── domain helper ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "website,expected",
    [
        ("https://www.example.com/path?x=1", "example.com"),
        ("http://Example.COM", "example.com"),
        ("example.com", "example.com"),
        ("www.foo.co.uk/bar", "foo.co.uk"),
        (None, None),
        ("   ", None),
    ],
)
def test_domain_from_url(website: str | None, expected: str | None) -> None:
    assert o._domain_from_url(website) == expected


# ── BD officer seed (mirrors FE dedup + cap) ──────────────────────────────────


def test_build_bd_officer_requests_dedupes_person_and_org_and_caps() -> None:
    bd = SimpleNamespace(
        direct_owners=[
            {"name": "DOE, JANE", "title": "CEO"},
            {"name": "ACME HOLDINGS LLC", "title": "Owner"},
        ],
        executive_officers=[
            {"name": "DOE, JANE", "title": "CEO"},  # dup person — dropped
            {"name": "SMITH, JOHN", "title": "CFO"},
        ],
    )
    reqs = o._build_bd_officer_requests(bd)
    kinds = [(r.type, getattr(r, "first_name", None), getattr(r, "org_name", None)) for r in reqs]
    assert ("person", "Jane", None) in kinds
    assert ("person", "John", None) in kinds
    assert ("organization", None, "ACME HOLDINGS LLC") in kinds
    assert len(reqs) == 3  # the duplicate Jane Doe is deduped


def test_build_bd_officer_requests_caps_at_max() -> None:
    bd = SimpleNamespace(
        direct_owners=[{"name": f"LAST{i}, FIRST{i}", "title": "X"} for i in range(25)],
        executive_officers=[],
    )
    reqs = o._build_bd_officer_requests(bd)
    assert len(reqs) == o._MAX_BD_OFFICERS


def test_build_bd_officer_requests_handles_empty_and_malformed() -> None:
    bd = SimpleNamespace(direct_owners=None, executive_officers=[{}, {"name": "   "}, "junk"])
    assert o._build_bd_officer_requests(bd) == []


# ── per-item roll-up ──────────────────────────────────────────────────────────


def test_item_outcome_done_when_any_step_ran() -> None:
    out = o._ItemOutcome()
    out.step("refresh-all: completed", True)
    out.step("focus: error", False)
    assert out.status == "done"
    assert "refresh-all: completed" in out.reason


def test_item_outcome_failed_only_when_all_counted_steps_failed() -> None:
    out = o._ItemOutcome()
    out.step("refresh-all: failed", False)
    out.step("contacts: failed", False)
    assert out.status == "failed"


def test_item_outcome_note_is_neutral() -> None:
    out = o._ItemOutcome()
    out.note("email: email_extractor not granted, skipped")
    # A note alone (no counted steps) is not a failure.
    assert out.status == "done"
    assert out.failed == 0 and out.ran_ok == 0


# ── summary string ────────────────────────────────────────────────────────────


def test_build_summary_head_only_when_all_done() -> None:
    results = [{"name": "A", "status": "done"}, {"name": "B", "status": "done"}]
    assert o._build_summary(2, 0, 0, results) == "2 enriched, 0 skipped, 0 failed."


def test_build_summary_lists_troubled_firms() -> None:
    results = [
        {"name": "A", "status": "done"},
        {"name": "B", "status": "skipped"},
        {"name": "C", "status": "failed"},
    ]
    summary = o._build_summary(1, 1, 1, results)
    assert summary.startswith("1 enriched, 1 skipped, 1 failed.")
    assert "B (skipped)" in summary and "C (failed)" in summary


# ── dispatch gating (no DB touched on these paths) ────────────────────────────


async def test_dispatch_skips_unsupported_entity_types() -> None:
    user = _user(role="admin")
    status, reason = await o._dispatch_item(
        _item("institutional_investor"), user=user, bulk_run_id=1
    )
    assert status == "skipped" and "not supported" in reason

    status, reason = await o._dispatch_item(
        _item("reporting_owner"), user=user, bulk_run_id=1
    )
    assert status == "skipped" and "not supported" in reason


async def test_dispatch_skips_when_feature_gate_blocks() -> None:
    viewer = _user(role="viewer", features=[])  # no master_list / banks / advisors
    for entity_type, needle in [
        ("broker_dealer", "master_list"),
        ("advisor", "investment_advisors"),
        ("bank", "banks"),
    ]:
        status, reason = await o._dispatch_item(
            _item(entity_type), user=viewer, bulk_run_id=1
        )
        assert status == "skipped"
        assert needle in reason


# ── worker loop: sequential pointer, counters, terminal status + summary ──────


async def test_run_worker_sequences_counters_and_finalizes(monkeypatch) -> None:
    writes: list[dict] = []

    async def fake_write_parent(run_id, **kwargs):  # noqa: ANN001
        writes.append(kwargs)

    # Canned per-item outcome keyed on the item name.
    outcomes = {
        "BD-ok": ("done", "refresh-all: completed"),
        "ADV-fail": ("failed", "contacts: failed"),
        "II-skip": ("skipped", "institutional_investor enrichment not supported yet"),
    }

    async def fake_dispatch(item, *, user, bulk_run_id):  # noqa: ANN001
        return outcomes[item.name]

    monkeypatch.setattr(o, "_write_parent", fake_write_parent)
    monkeypatch.setattr(o, "_dispatch_item", fake_dispatch)

    items = [
        _item("broker_dealer", 1, "BD-ok"),
        _item("advisor", 2, "ADV-fail"),
        _item("institutional_investor", 3, "II-skip"),
    ]
    await o.run_favorite_list_enrich(99, 7, items, user=_user(role="admin"))

    final = writes[-1]
    assert final["status"] == "completed_with_errors"  # 1 done, 1 failed, 1 skipped
    assert final["success"] == 1  # done count
    assert final["failure"] == 1  # failed count
    assert final["processed"] == 3
    assert final["completed"] is True

    notes = final["notes"]
    assert notes["stage"] == "done"
    assert notes["current_index"] is None  # pointer cleared at finish
    assert notes["total"] == 3
    assert notes["summary"].startswith("1 enriched, 1 skipped, 1 failed.")
    # Per-row statuses recorded in order.
    statuses = {row["name"]: row["status"] for row in notes["items"]}
    assert statuses == {"BD-ok": "done", "ADV-fail": "failed", "II-skip": "skipped"}


async def test_run_worker_all_failed_is_terminal_failed(monkeypatch) -> None:
    async def fake_write_parent(run_id, **kwargs):  # noqa: ANN001
        fake_write_parent.calls.append(kwargs)

    fake_write_parent.calls = []

    async def fake_dispatch(item, *, user, bulk_run_id):  # noqa: ANN001
        return "failed", "boom"

    monkeypatch.setattr(o, "_write_parent", fake_write_parent)
    monkeypatch.setattr(o, "_dispatch_item", fake_dispatch)

    await o.run_favorite_list_enrich(
        1, 1, [_item("broker_dealer", 1, "X")], user=_user(role="admin")
    )
    assert fake_write_parent.calls[-1]["status"] == "failed"


async def test_run_worker_catches_dispatch_exception_and_continues(monkeypatch) -> None:
    async def fake_write_parent(run_id, **kwargs):  # noqa: ANN001
        fake_write_parent.last = kwargs

    async def fake_dispatch(item, *, user, bulk_run_id):  # noqa: ANN001
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(o, "_write_parent", fake_write_parent)
    monkeypatch.setattr(o, "_dispatch_item", fake_dispatch)

    items = [_item("broker_dealer", 1, "A"), _item("bank", 2, "B")]
    await o.run_favorite_list_enrich(1, 1, items, user=_user(role="admin"))

    notes = fake_write_parent.last["notes"]
    # Both items recorded as failed with the exception surfaced as the reason;
    # the run still reached its terminal write (no abort on the first blow-up).
    assert [r["status"] for r in notes["items"]] == ["failed", "failed"]
    assert "RuntimeError" in notes["items"][0]["reason"]
    assert fake_write_parent.last["status"] == "failed"


def test_progress_notes_shape_is_json_serializable() -> None:
    notes = o._progress_notes(
        list_id=1,
        total=2,
        current_index=0,
        current=_item("broker_dealer", 5, "Acme"),
        items=[],
        stage="running",
    )
    # Must round-trip through json.dumps (it's stored in PipelineRun.notes as text).
    round_tripped = json.loads(json.dumps(notes))
    assert round_tripped["current_name"] == "Acme"
    assert round_tripped["current_entity_type"] == "broker_dealer"
    assert round_tripped["stage"] == "running"


def test_progress_notes_stamps_a_fresh_parseable_heartbeat() -> None:
    """Every progress write carries ``last_progress_at`` so liveness is judged
    from last-advance, not started_at (the double-spend fix hinges on this)."""
    before = datetime.now(timezone.utc)
    notes = o._progress_notes(
        list_id=1,
        total=1,
        current_index=0,
        current=_item("broker_dealer", 5, "Acme"),
        items=[],
        stage="running",
    )
    after = datetime.now(timezone.utc)

    stamped = datetime.fromisoformat(notes["last_progress_at"])
    assert stamped.tzinfo is not None  # tz-aware so the reaper can compare it
    assert before <= stamped <= after


# ── constant consolidation (single source of truth) ───────────────────────────


def test_pipeline_name_is_single_sourced() -> None:
    """The orchestrator, the reaper's staleness family, and the lightweight
    shared module all resolve to ONE literal — a rename via pipeline_names can't
    silently desync the writer (this worker) from the matchers (reaper + guard)."""
    from app.services import pipeline_names, pipeline_reaper

    assert (
        o.FAVORITE_LIST_ENRICH_PIPELINE_NAME
        == pipeline_names.FAVORITE_LIST_ENRICH_PIPELINE
        == pipeline_reaper.FAVORITE_LIST_ENRICH_PIPELINE
        == "favorite_list_enrich"
    )
    # The exact name this worker stamps onto parent runs is in the reaper family,
    # so the reaper/guard actually match the runs the worker creates.
    assert o.FAVORITE_LIST_ENRICH_PIPELINE_NAME in pipeline_reaper.REFRESH_FAMILY_PIPELINES


def test_heartbeat_interval_is_well_under_stale_threshold() -> None:
    """The lifetime beat must fire comfortably faster than the reaper's staleness
    window, with headroom for a couple of missed beats (transient DB blip)."""
    from app.services.pipeline_reaper import STALE_REFRESH_RUN_AGE

    assert o._HEARTBEAT_INTERVAL_SECONDS < STALE_REFRESH_RUN_AGE.total_seconds()
    assert o._HEARTBEAT_INTERVAL_SECONDS <= STALE_REFRESH_RUN_AGE.total_seconds() / 3


# ── lifetime heartbeat: read-modify-write keeps liveness fresh, no clobber ─────


async def test_touch_heartbeat_bumps_liveness_without_clobbering_progress(
    monkeypatch,
) -> None:
    """The core RMW invariant: the lifetime beat advances ``last_progress_at``
    while leaving the per-firm pointer/items/counters untouched, so a run stuck
    inside one long firm reads fresh without the beat reverting UI progress."""
    engine, maker = await _make_sqlite_session()
    try:
        stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        async with maker() as db:
            db.add(
                PipelineRun(
                    id=1,
                    pipeline_name=o.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
                    trigger_source="t",
                    status="running",
                    notes=json.dumps(
                        {
                            "list_id": 7,
                            "stage": "running",
                            "total": 5,
                            "current_index": 3,
                            "current_name": "Acme",
                            "items": [{"id": 1, "status": "done"}],
                            "last_progress_at": stale_ts,
                        }
                    ),
                    started_at=datetime.now(timezone.utc) - timedelta(minutes=40),
                )
            )
            await db.commit()

        # _touch_heartbeat opens its own SessionLocal — point that at SQLite.
        monkeypatch.setattr(o, "SessionLocal", maker)
        await o._touch_heartbeat(1)

        async with maker() as db:
            row = await db.get(PipelineRun, 1)
            notes = json.loads(row.notes)

        # Heartbeat advanced to ~now …
        stamped = datetime.fromisoformat(notes["last_progress_at"])
        assert stamped > datetime.now(timezone.utc) - timedelta(minutes=1)
        # … and NONE of the per-firm progress fields were clobbered.
        assert notes["current_index"] == 3
        assert notes["current_name"] == "Acme"
        assert notes["items"] == [{"id": 1, "status": "done"}]
        assert notes["total"] == 5
    finally:
        await engine.dispose()


async def test_touch_heartbeat_seeds_notes_when_missing_or_malformed(
    monkeypatch,
) -> None:
    """A run whose notes are absent/garbage still gets a valid heartbeat stamped
    (the beat must never raise on a weird row)."""
    engine, maker = await _make_sqlite_session()
    try:
        async with maker() as db:
            db.add_all(
                [
                    PipelineRun(
                        id=1,
                        pipeline_name=o.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
                        trigger_source="t",
                        status="running",
                        notes=None,
                        started_at=datetime.now(timezone.utc),
                    ),
                    PipelineRun(
                        id=2,
                        pipeline_name=o.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
                        trigger_source="t",
                        status="running",
                        notes="not json",
                        started_at=datetime.now(timezone.utc),
                    ),
                ]
            )
            await db.commit()

        monkeypatch.setattr(o, "SessionLocal", maker)
        await o._touch_heartbeat(1)
        await o._touch_heartbeat(2)
        # A vanished run is a clean no-op (must not raise).
        await o._touch_heartbeat(999)

        async with maker() as db:
            for rid in (1, 2):
                notes = json.loads((await db.get(PipelineRun, rid)).notes)
                assert isinstance(notes, dict)
                assert datetime.fromisoformat(notes["last_progress_at"]).tzinfo is not None
    finally:
        await engine.dispose()


# ── lifetime heartbeat: periodic loop fires, cancels cleanly, survives errors ──


async def test_heartbeat_loop_fires_periodically_and_cancels_cleanly(
    monkeypatch,
) -> None:
    calls: list[int] = []

    async def fake_touch(run_id: int) -> None:
        calls.append(run_id)

    monkeypatch.setattr(o, "_touch_heartbeat", fake_touch)
    monkeypatch.setattr(o, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    task = asyncio.create_task(o._heartbeat_loop(42, asyncio.Lock()))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls and all(rid == 42 for rid in calls)
    beats_at_cancel = len(calls)
    # After cancellation the loop is truly stopped — no further beats.
    await asyncio.sleep(0.03)
    assert len(calls) == beats_at_cancel


async def test_heartbeat_loop_survives_a_failed_beat(monkeypatch) -> None:
    """A transient beat error is contained and the loop keeps beating — a DB blip
    must never silently stop the liveness backstop."""
    calls: list[int] = []

    async def flaky_touch(run_id: int) -> None:
        calls.append(run_id)
        if len(calls) == 1:
            raise RuntimeError("transient DB blip")

    monkeypatch.setattr(o, "_touch_heartbeat", flaky_touch)
    monkeypatch.setattr(o, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    task = asyncio.create_task(o._heartbeat_loop(1, asyncio.Lock()))
    await asyncio.sleep(0.06)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert len(calls) >= 2  # kept beating after the first beat raised


# ── worker wiring: heartbeat runs mid-firm and is torn down with the worker ────


async def test_worker_runs_lifetime_heartbeat_during_a_long_firm_and_stops_it(
    monkeypatch,
) -> None:
    """The backstop in action: a SINGLE firm outlives several beat intervals (the
    exact mid-firm case per-firm writes can't cover), yet the lifetime heartbeat
    keeps firing while it is in flight — then stops once the worker returns."""
    beats: list[int] = []

    async def fake_touch(run_id: int) -> None:
        beats.append(run_id)

    async def fake_write_parent(run_id, **kwargs):  # noqa: ANN001
        return None

    async def slow_dispatch(item, *, user, bulk_run_id):  # noqa: ANN001
        # One firm that spans many beats — no per-firm write happens meanwhile.
        await asyncio.sleep(0.08)
        return "done", "ok"

    monkeypatch.setattr(o, "_touch_heartbeat", fake_touch)
    monkeypatch.setattr(o, "_write_parent", fake_write_parent)
    monkeypatch.setattr(o, "_dispatch_item", slow_dispatch)
    monkeypatch.setattr(o, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    await o.run_favorite_list_enrich(
        7, 1, [_item("broker_dealer", 1, "Slow Firm")], user=_user(role="admin")
    )

    # Beat(s) landed WHILE the single firm was dispatching …
    assert len(beats) >= 1 and all(rid == 7 for rid in beats)
    beats_after_run = len(beats)
    # … and the heartbeat was cancelled with the worker (no leak past return).
    await asyncio.sleep(0.03)
    assert len(beats) == beats_after_run


async def test_worker_cancels_heartbeat_even_when_body_raises(monkeypatch) -> None:
    """The ``finally`` tears the heartbeat down even if the worker body blows up,
    so a dead worker never leaves an orphaned beat keeping the run falsely alive."""
    beats: list[int] = []

    async def fake_touch(run_id: int) -> None:
        beats.append(run_id)

    async def boom_write_parent(run_id, **kwargs):  # noqa: ANN001
        raise RuntimeError("write failed")

    monkeypatch.setattr(o, "_touch_heartbeat", fake_touch)
    monkeypatch.setattr(o, "_write_parent", boom_write_parent)
    monkeypatch.setattr(o, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    with pytest.raises(RuntimeError):
        await o.run_favorite_list_enrich(
            7, 1, [_item("broker_dealer", 1, "X")], user=_user(role="admin")
        )

    beats_at_raise = len(beats)
    await asyncio.sleep(0.03)
    assert len(beats) == beats_at_raise  # heartbeat stopped by the finally


# ── end-to-end core scenario: quiet per-firm progress + fresh lifetime beat ────


async def test_lifetime_heartbeat_keeps_progress_quiet_run_out_of_the_reaper(
    monkeypatch,
) -> None:
    """Close the double-spend gap end-to-end (finding M1): a firm stuck mid-
    dispatch for >10 min leaves the per-firm pointer quiet, but the LIFETIME beat
    keeps ``last_progress_at`` fresh — so ``is_run_stale`` stays False and the
    reaper leaves the live run alone. The converse (a truly dead worker whose
    heartbeat has gone quiet) is stale and gets reaped/superseded."""
    from app.services.pipeline_reaper import is_run_stale, reap_stale_refresh_runs

    engine, maker = await _make_sqlite_session()
    try:
        quiet_progress = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()

        def _seed(run_id: int, name: str) -> PipelineRun:
            # Both: started 40 min ago, last PER-FIRM write 20 min ago (pointer
            # frozen at index 2) — i.e. per-firm progress has been quiet > 10 min.
            return PipelineRun(
                id=run_id,
                pipeline_name=o.FAVORITE_LIST_ENRICH_PIPELINE_NAME,
                trigger_source="t",
                status="running",
                notes=json.dumps(
                    {
                        "list_id": run_id,
                        "stage": "running",
                        "total": 9,
                        "current_index": 2,
                        "current_name": name,
                        "items": [],
                        "last_progress_at": quiet_progress,
                    }
                ),
                started_at=datetime.now(timezone.utc) - timedelta(minutes=40),
            )

        async with maker() as db:
            db.add_all([_seed(1, "Live (stuck) Firm"), _seed(2, "Dead Firm")])
            await db.commit()

        # The lifetime beat fires for the LIVE worker (run 1) only; run 2's worker
        # is dead, so nothing re-stamps its heartbeat.
        monkeypatch.setattr(o, "SessionLocal", maker)
        await o._touch_heartbeat(1)

        async with maker() as db:
            live = await db.get(PipelineRun, 1)
            dead = await db.get(PipelineRun, 2)
            # The per-firm pointer stayed quiet on BOTH (the beat didn't touch it) …
            assert json.loads(live.notes)["current_index"] == 2
            assert json.loads(dead.notes)["current_index"] == 2
            # … but the live run reads fresh, the dead one stale.
            assert is_run_stale(live) is False
            assert is_run_stale(dead) is True

        async with maker() as db:
            reaped = await reap_stale_refresh_runs(db)
        assert reaped == 1  # only the genuinely-dead run

        async with maker() as db:
            assert (await db.get(PipelineRun, 1)).status == "running"  # live survives
            assert (await db.get(PipelineRun, 2)).status == "failed"  # dead reaped
    finally:
        await engine.dispose()
