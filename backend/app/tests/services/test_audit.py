"""Unit tests for the ``record_audit`` helper — no DB, stubbed session."""

from __future__ import annotations

import json

from app.services.audit import record_audit


class _StubSession:
    """Captures add/flush/commit without touching a real DB."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushed = 0
        self.committed = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1

    async def commit(self) -> None:
        self.committed += 1


async def test_record_audit_flushes_and_serialises_details() -> None:
    db = _StubSession()

    await record_audit(
        db,
        action="finra_health_check",
        user_id="user-1",
        details={"broker_dealer_id": 42, "crd_number": "123"},
    )

    assert len(db.added) == 1
    row = db.added[0]
    assert row.action == "finra_health_check"
    assert row.user_id == "user-1"
    assert json.loads(row.details) == {"broker_dealer_id": 42, "crd_number": "123"}
    # Default joins the caller's transaction — flush only, no standalone commit.
    assert db.flushed == 1
    assert db.committed == 0


async def test_record_audit_commit_true_and_null_details() -> None:
    db = _StubSession()

    await record_audit(db, action="finra_bulk_refresh", commit=True)

    row = db.added[0]
    assert row.user_id is None
    assert row.details is None
    assert db.committed == 1
    assert db.flushed == 0
