"""Regression for the LISTEN/NOTIFY hook on ``AlertRepository.upsert_many``.

The realtime activity feed (plans/how-does-the-activity-velvety-gosling.md)
broadcasts ``alert.inserted`` events only for rows that were freshly
INSERTED, not for rows that hit the ON CONFLICT DO UPDATE branch — otherwise
re-running the filing monitor would re-emit every previously-seen alert.
Postgres exposes this distinction via the system column ``xmax``, which is
``0`` for fresh inserts and the conflicting transaction's xid for updates.

These tests stage a mock session that returns mixed (id, is_new) rows from
the RETURNING clause and assert:

  1. The repository's hook calls ``publish_via_session`` with only the
     ``id`` values whose ``is_new`` flag is True.
  2. When every row is a conflict update, no notification is published.
  3. When the input batch is empty, the early-return path skips both the
     upsert AND the publish.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.alerts import AlertRepository
from app.services.service_models import FilingAlertRecord


def _record(bd_id: int, dedupe: str) -> FilingAlertRecord:
    return FilingAlertRecord(
        bd_id=bd_id,
        dedupe_key=dedupe,
        form_type="Form BD",
        priority="high",
        filed_at=datetime(2026, 5, 11, tzinfo=timezone.utc),
        summary="test alert",
        source_filing_url=None,
    )


class _StagedSession:
    """AsyncSession mock that returns a pre-staged RETURNING result.

    Captures executed statements so the test can verify that the upsert
    happened and the pg_notify text() also ran (or didn't).
    """

    def __init__(self, returning_rows: list[tuple[int, int, str, bool]]) -> None:
        # rows are (id, bd_id, form_type, is_new) per the RETURNING clause
        self._returning_rows = returning_rows
        self.executed_statements: list[Any] = []
        self.executed_params: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.executed_statements.append(statement)
        self.executed_params.append(params)
        result = MagicMock()
        # Mimic the row tuple shape that .all() returns. SQLAlchemy rows
        # expose named attributes; MagicMock with configured attributes
        # gives us .id/.bd_id/.form_type/.is_new without pulling in a
        # real Row class.
        rows = []
        for row_id, bd_id, form_type, is_new in self._returning_rows:
            row = MagicMock()
            row.id = row_id
            row.bd_id = bd_id
            row.form_type = form_type
            row.is_new = is_new
            rows.append(row)
        result.all.return_value = rows
        return result

    async def flush(self) -> None:
        return None


@pytest.fixture
def repository() -> AlertRepository:
    return AlertRepository()


@pytest.mark.asyncio
async def test_upsert_many_publishes_only_new_ids(
    monkeypatch: pytest.MonkeyPatch, repository: AlertRepository
) -> None:
    """Rows with xmax==0 (is_new=True) get broadcast AND get surfaced in
    the return value; updates do not."""
    publish_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.alerts.alert_event_bus.publish_via_session", publish_mock
    )

    session = _StagedSession(
        returning_rows=[
            (101, 1, "Form X-17A-5", True),
            (102, 2, "Form BD", False),
            (103, 3, "Form X-17A-5", True),
        ]
    )

    inserted = await repository.upsert_many(
        session,
        [_record(1, "a"), _record(2, "b"), _record(3, "c")],
    )

    # Only the is_new=True rows propagate to the caller.
    assert inserted == [(101, 1, "Form X-17A-5"), (103, 3, "Form X-17A-5")]
    publish_mock.assert_awaited_once()
    awaited_session, awaited_payload = publish_mock.await_args.args
    assert awaited_session is session
    assert awaited_payload["type"] == "alert.inserted"
    assert awaited_payload["alert_ids"] == [101, 103]


@pytest.mark.asyncio
async def test_upsert_many_skips_publish_when_all_are_updates(
    monkeypatch: pytest.MonkeyPatch, repository: AlertRepository
) -> None:
    """Re-running the filing monitor on already-known filings must not
    re-emit alerts — the publish call must be suppressed when no row
    landed on the INSERT branch, and the caller receives an empty list."""
    publish_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.alerts.alert_event_bus.publish_via_session", publish_mock
    )

    session = _StagedSession(
        returning_rows=[(201, 1, "Form BD", False), (202, 2, "Form BD", False)]
    )

    inserted = await repository.upsert_many(
        session, [_record(1, "x"), _record(2, "y")]
    )

    assert inserted == []
    publish_mock.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_many_empty_batch_short_circuits(
    monkeypatch: pytest.MonkeyPatch, repository: AlertRepository
) -> None:
    """An empty batch returns an empty list without touching the DB or
    the bus."""
    publish_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.alerts.alert_event_bus.publish_via_session", publish_mock
    )

    session = _StagedSession(returning_rows=[])

    inserted = await repository.upsert_many(session, [])

    assert inserted == []
    assert session.executed_statements == []
    publish_mock.assert_not_called()
