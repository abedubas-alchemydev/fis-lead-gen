"""Unit tests for ``run_bulk_enrichment`` -- DB-free.

The bulk enricher is a thin loop around ``enrich_discovered_email`` plus a
candidate-id query. We mock both the session factory and the per-row
enricher so the test stays in the default suite (no Postgres dependency)
and exercises the failure-isolation contract directly: one bad row must
not abort the batch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.email_extractor import bulk_enrichment
from app.services.email_extractor.apollo_enrichment import EnrichmentError


def _patch_session(monkeypatch: pytest.MonkeyPatch, candidate_ids: list[int]) -> AsyncMock:
    """Wire ``bulk_enrichment.SessionLocal`` to a context manager that yields a
    mock session whose ``execute(...).scalars().all()`` returns ``candidate_ids``.
    Returns the inner session mock so tests can assert against it.
    """
    scalars_result = MagicMock()
    scalars_result.all.return_value = candidate_ids

    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result

    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result)

    cm = AsyncMock()
    cm.__aenter__.return_value = session
    cm.__aexit__.return_value = None

    monkeypatch.setattr(bulk_enrichment, "SessionLocal", lambda: cm)
    return session


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the inter-row pause so tests run fast."""

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(bulk_enrichment.asyncio, "sleep", _instant)


async def test_processes_all_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, candidate_ids=[101, 102, 103])
    seen: list[int] = []

    async def _fake_enrich(_db: Any, email_id: int) -> None:
        seen.append(email_id)

    monkeypatch.setattr(bulk_enrichment, "enrich_discovered_email", _fake_enrich)

    await bulk_enrichment.run_bulk_enrichment(scan_id=42)

    assert seen == [101, 102, 103]


async def test_per_email_failure_does_not_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """One row raising ``EnrichmentError`` must not stop subsequent rows."""
    _patch_session(monkeypatch, candidate_ids=[1, 2, 3, 4])
    attempts: list[int] = []

    async def _flaky(_db: Any, email_id: int) -> None:
        attempts.append(email_id)
        if email_id == 2:
            raise EnrichmentError("simulated apollo failure")

    monkeypatch.setattr(bulk_enrichment, "enrich_discovered_email", _flaky)

    await bulk_enrichment.run_bulk_enrichment(scan_id=7)

    assert attempts == [1, 2, 3, 4]


async def test_no_candidates_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, candidate_ids=[])
    enrich_mock = AsyncMock()
    monkeypatch.setattr(bulk_enrichment, "enrich_discovered_email", enrich_mock)

    await bulk_enrichment.run_bulk_enrichment(scan_id=99)

    enrich_mock.assert_not_called()


async def test_candidate_query_filters_by_run_and_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The candidate-id query must filter on both ``run_id`` and the
    ``enrichment_status != 'enriched'`` predicate so a re-run skips
    successful rows.
    """
    session = _patch_session(monkeypatch, candidate_ids=[10])

    async def _noop(_db: Any, _email_id: int) -> None:
        return None

    monkeypatch.setattr(bulk_enrichment, "enrich_discovered_email", _noop)

    await bulk_enrichment.run_bulk_enrichment(scan_id=123)

    assert session.execute.await_count == 1
    stmt = session.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "discovered_email.run_id = 123" in compiled
    assert "discovered_email.enrichment_status != 'enriched'" in compiled


async def test_inter_row_pause_invoked_between_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """``asyncio.sleep`` should be awaited once per candidate."""
    _patch_session(monkeypatch, candidate_ids=[1, 2, 3])

    async def _ok(_db: Any, _email_id: int) -> None:
        return None

    monkeypatch.setattr(bulk_enrichment, "enrich_discovered_email", _ok)

    sleep_calls: list[float] = []

    async def _track_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(bulk_enrichment.asyncio, "sleep", _track_sleep)

    await bulk_enrichment.run_bulk_enrichment(scan_id=1)

    assert len(sleep_calls) == 3
    assert all(seconds == bulk_enrichment.INTER_ROW_PAUSE_SECONDS for seconds in sleep_calls)
