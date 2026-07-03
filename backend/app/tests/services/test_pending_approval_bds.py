"""Tests for the "Pending Approval BDs" filter (Feature 2 backend half).

Pending approval = filed with FINRA/SEC but no approval observed:
``crd_number IS NOT NULL AND registration_date IS NULL AND
registration_checked_at IS NOT NULL`` (the last is the nightly
registration-refresh sentinel — without it, never-checked rows whose date
is merely un-backfilled would pollute the card).

Three layers, mirroring ``test_broker_dealers_range_filters.py``:

1. SQL-shape: the repository emits the three-part predicate, retargets the
   30/90-day window onto the filed-date proxy ``created_at::date`` in
   pending mode, and leaves normal mode untouched (registration_date
   windowing).
2. Disjointness: the New BD predicate and the pending predicate can never
   both hold — proven structurally (registration_date >= cutoff vs
   registration_date IS NULL).
3. Endpoint: ``pending_approval=true`` parses and reaches the repository;
   the default is False.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

import httpx
import pytest

import app.api.v1.endpoints.broker_dealers as bd_endpoints
from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.schemas.broker_dealer import BrokerDealerListMeta, BrokerDealerListResponse
from app.services.auth import get_current_user
from app.services.broker_dealers import BrokerDealerRepository


class _StagedSession:
    """Captures statements; returns count -> items -> pipeline-run results."""

    def __init__(self) -> None:
        self.executed_statements: list[object] = []
        self._call_count = 0

    async def execute(self, statement: object) -> object:
        self.executed_statements.append(statement)
        self._call_count += 1
        result = MagicMock()
        if self._call_count == 1:
            result.scalar_one.return_value = 0
        elif self._call_count == 2:
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
        else:
            result.scalar_one_or_none.return_value = None
        return result


def _compile_sql(statement: object) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True})).lower()


def _captured_where_sql(session: _StagedSession) -> str:
    assert len(session.executed_statements) >= 2
    sql = _compile_sql(session.executed_statements[1])
    return sql.split("where", 1)[1] if "where" in sql else ""


def _default_kwargs() -> dict[str, object]:
    return {
        "search": None,
        "states": [],
        "statuses": [],
        "health_statuses": [],
        "lead_priorities": [],
        "clearing_partners": [],
        "clearing_types": [],
        "types_of_business": [],
        "list_mode": "all",
        "sort_by": "name",
        "sort_dir": "asc",
        "page": 1,
        "limit": 25,
    }


@pytest.fixture
def repository() -> BrokerDealerRepository:
    return BrokerDealerRepository()


# ── Repository SQL shape ────────────────────────────────────────────────────


async def test_pending_mode_emits_three_part_predicate(
    repository: BrokerDealerRepository,
) -> None:
    session = _StagedSession()
    await repository.list_broker_dealers(session, **_default_kwargs(), pending_approval=True)
    where = _captured_where_sql(session)
    assert "broker_dealers.crd_number is not null" in where
    assert "broker_dealers.registration_date is null" in where
    assert "broker_dealers.registration_checked_at is not null" in where


async def test_pending_mode_windows_on_filed_date_proxy(
    repository: BrokerDealerRepository,
) -> None:
    """In pending mode the 30/90-day params retarget to created_at::date —
    every pending row's registration_date is NULL, so windowing on it would
    return zero rows; and formation_date is the wrong anchor (it predates
    filing by years, which zeroed the card — see pending_filed_date_proxy)."""
    session = _StagedSession()
    await repository.list_broker_dealers(
        session,
        **_default_kwargs(),
        pending_approval=True,
        registered_after=date(2026, 4, 3),
        registered_before=date(2026, 7, 2),
    )
    where = _captured_where_sql(session)
    assert "cast(broker_dealers.created_at as date) >= '2026-04-03'" in where
    assert "cast(broker_dealers.created_at as date) <= '2026-07-02'" in where
    # NOT anchored on formation_date (the bug that zeroed the card) ...
    assert "formation_date" not in where
    # ... and NOT windowed on registration_date (which stays only in the IS
    # NULL predicate).
    assert "broker_dealers.registration_date >=" not in where
    assert "broker_dealers.registration_date <=" not in where


async def test_normal_mode_still_windows_on_registration_date(
    repository: BrokerDealerRepository,
) -> None:
    """Regression guard: the New BD path is untouched."""
    session = _StagedSession()
    await repository.list_broker_dealers(
        session, **_default_kwargs(), registered_after=date(2026, 4, 3)
    )
    where = _captured_where_sql(session)
    assert "broker_dealers.registration_date >= '2026-04-03'" in where
    assert "coalesce" not in where
    assert "registration_checked_at" not in where


async def test_pending_and_new_bd_predicates_are_disjoint(
    repository: BrokerDealerRepository,
) -> None:
    """A firm on the Pending card must NOT also appear on New BD.

    Structural proof: New BD requires ``registration_date >= cutoff`` while
    pending requires ``registration_date IS NULL``; SQL NULL never satisfies
    a range comparison, so no row can match both. Assert both fragments
    exist in their respective statements so a future edit can't quietly
    decouple them.
    """
    pending_session = _StagedSession()
    await repository.list_broker_dealers(
        pending_session, **_default_kwargs(), pending_approval=True
    )
    new_bd_session = _StagedSession()
    await repository.list_broker_dealers(
        new_bd_session, **_default_kwargs(), registered_after=date(2026, 4, 3)
    )
    assert "broker_dealers.registration_date is null" in _captured_where_sql(pending_session)
    assert (
        "broker_dealers.registration_date >= '2026-04-03'"
        in _captured_where_sql(new_bd_session)
    )


async def test_count_pending_approval_sql_shape() -> None:
    """The dashboard KPI count applies the same predicate + a 90-day proxy
    window (mirroring new_bds_90_days), and supports the unwindowed total."""

    class _CountSession:
        def __init__(self) -> None:
            self.statements: list[object] = []

        async def execute(self, statement: object) -> object:
            self.statements.append(statement)
            result = MagicMock()
            result.scalar_one.return_value = 3
            return result

    repository = BrokerDealerRepository()
    session = _CountSession()
    count = await repository.count_pending_approval(session, today=date(2026, 7, 2))
    assert count == 3
    sql = _compile_sql(session.statements[0])
    assert "broker_dealers.crd_number is not null" in sql
    assert "broker_dealers.registration_date is null" in sql
    assert "broker_dealers.registration_checked_at is not null" in sql
    assert (
        "cast(broker_dealers.created_at as date) >= '2026-04-03'" in sql
    )  # 2026-07-02 minus 90 days
    assert "formation_date" not in sql

    session = _CountSession()
    await repository.count_pending_approval(session, filed_within_days=None)
    unwindowed = _compile_sql(session.statements[0])
    assert "cast(broker_dealers.created_at as date) >=" not in unwindowed


# ── Endpoint passthrough ────────────────────────────────────────────────────


def _override_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="test-user",
        name="Test User",
        email="test-user@example.com",
        role="viewer",
        feature_permissions=["master_list"],
        session_expires_at=datetime(2099, 1, 1),
    )


async def _call_endpoint(monkeypatch, params: dict) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def _capture(_db, **kwargs):
        captured.update(kwargs)
        return BrokerDealerListResponse(
            items=[], meta=BrokerDealerListMeta(page=1, limit=25, total=0, total_pages=0)
        )

    monkeypatch.setattr(bd_endpoints.repository, "list_broker_dealers", _capture)
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/api/v1/broker-dealers", params=params)
        assert response.status_code == 200, response.text
        return captured
    finally:
        app.dependency_overrides.clear()


async def test_endpoint_defaults_to_not_pending(monkeypatch) -> None:
    captured = await _call_endpoint(monkeypatch, {})
    assert captured["pending_approval"] is False


async def test_endpoint_passes_pending_flag_with_window(monkeypatch) -> None:
    captured = await _call_endpoint(
        monkeypatch,
        {"pending_approval": "true", "registered_after": "2026-04-03"},
    )
    assert captured["pending_approval"] is True
    assert captured["registered_after"] == date(2026, 4, 3)
