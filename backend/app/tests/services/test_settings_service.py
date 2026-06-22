"""Unit tests for scoring-weight persistence.

Two layers are covered, deliberately kept separate so no test name
over-claims:

1. ``SettingsService.get_scoring_settings`` / ``update_scoring_settings``
   (this module's ``app.services.settings``) — the *persistence* behavior:
   read-or-create the singleton ``default`` row, then field-set +
   commit + refresh on update. The service does an **unconditional**
   field-set; it has no sum-to-100 guard of its own. DB is a hand-rolled
   ``_StubSession`` in the style of ``test_audit.py`` / the staged-session
   mocks elsewhere in this suite — no Postgres.

2. The sum-to-100 validation RULE, which lives one layer up in the
   endpoint ``app/api/v1/endpoints/settings.py`` (``PUT /settings/scoring``,
   the ``if total != 100`` guard, ~line 77). Those tests drive the real
   endpoint coroutine through the ASGI app with ``app.dependency_overrides``
   and ``patch.object`` so the validation branch is exercised without a DB
   — mirroring ``test_settings_competitors.py``.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.v1.endpoints.settings import repository, settings_service
from app.db.session import get_db_session
from app.main import app
from app.models.scoring_setting import ScoringSetting
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user
from app.services.settings import SettingsService


# ---------------------------------------------------------------------------
# Service layer — stubbed AsyncSession
# ---------------------------------------------------------------------------


class _StubSession:
    """Minimal AsyncSession stand-in for SettingsService.

    ``execute`` returns a pre-staged result object exposing
    ``scalar_one_or_none`` (the only result accessor the service uses for
    scoring settings). ``add`` / ``commit`` / ``refresh`` are counted so the
    upsert path can be asserted without a real DB.
    """

    def __init__(self, scalar_results: list[object | None]) -> None:
        # One entry consumed per execute() call, in order.
        self._scalar_results = list(scalar_results)
        self.added: list[object] = []
        self.commits = 0
        self.refreshes = 0
        self.executes = 0

    async def execute(self, _statement: object) -> MagicMock:
        self.executes += 1
        result = MagicMock()
        value = self._scalar_results.pop(0) if self._scalar_results else None
        result.scalar_one_or_none = MagicMock(return_value=value)
        return result

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _obj: object) -> None:
        self.refreshes += 1


async def test_get_scoring_settings_returns_existing_row_without_writing() -> None:
    existing = ScoringSetting(
        settings_key="default",
        net_capital_growth_weight=40,
        clearing_arrangement_weight=30,
        financial_health_weight=20,
        registration_recency_weight=10,
    )
    db = _StubSession(scalar_results=[existing])

    setting = await SettingsService().get_scoring_settings(db)

    assert setting is existing
    # Existing-row branch must not insert or commit.
    assert db.added == []
    assert db.commits == 0
    assert db.refreshes == 0


async def test_get_scoring_settings_creates_default_when_missing() -> None:
    db = _StubSession(scalar_results=[None])

    setting = await SettingsService().get_scoring_settings(db)

    assert isinstance(setting, ScoringSetting)
    assert setting.settings_key == "default"
    # Newly-created row is added once and persisted.
    assert db.added == [setting]
    assert db.commits == 1
    assert db.refreshes == 1
    # Model defaults (35/30/20/15) apply once the row is flushed; in this
    # unit context we assert the column defaults declared on the model.
    assert ScoringSetting.net_capital_growth_weight.default.arg == 35
    assert ScoringSetting.clearing_arrangement_weight.default.arg == 30
    assert ScoringSetting.financial_health_weight.default.arg == 20
    assert ScoringSetting.registration_recency_weight.default.arg == 15


async def test_update_scoring_settings_mutates_existing_row() -> None:
    existing = ScoringSetting(
        settings_key="default",
        net_capital_growth_weight=35,
        clearing_arrangement_weight=30,
        financial_health_weight=20,
        registration_recency_weight=15,
    )
    db = _StubSession(scalar_results=[existing])

    updated = await SettingsService().update_scoring_settings(
        db,
        net_capital_growth_weight=50,
        clearing_arrangement_weight=25,
        financial_health_weight=15,
        registration_recency_weight=10,
    )

    assert updated is existing
    assert updated.net_capital_growth_weight == 50
    assert updated.clearing_arrangement_weight == 25
    assert updated.financial_health_weight == 15
    assert updated.registration_recency_weight == 10
    # update reuses get_scoring_settings (existing branch, no add) then
    # commits + refreshes exactly once.
    assert db.added == []
    assert db.commits == 1
    assert db.refreshes == 1


async def test_update_scoring_settings_creates_then_updates_when_missing() -> None:
    # No existing row -> get_scoring_settings inserts + commits + refreshes
    # once, then update_scoring_settings commits + refreshes a second time.
    db = _StubSession(scalar_results=[None])

    updated = await SettingsService().update_scoring_settings(
        db,
        net_capital_growth_weight=25,
        clearing_arrangement_weight=25,
        financial_health_weight=25,
        registration_recency_weight=25,
    )

    assert isinstance(updated, ScoringSetting)
    assert updated.net_capital_growth_weight == 25
    assert updated.registration_recency_weight == 25
    assert db.added == [updated]
    # One commit/refresh in the create path, one in the update path.
    assert db.commits == 2
    assert db.refreshes == 2


async def test_update_scoring_settings_does_not_validate_sum() -> None:
    """Layering note: the service persists whatever weights it is given.

    The sum-to-100 rule is enforced by the endpoint, NOT the service. This
    test pins that the service itself happily writes a non-100 sum, so the
    endpoint tests below are the real owner of the validation contract.
    """
    existing = ScoringSetting(settings_key="default")
    db = _StubSession(scalar_results=[existing])

    updated = await SettingsService().update_scoring_settings(
        db,
        net_capital_growth_weight=99,
        clearing_arrangement_weight=99,
        financial_health_weight=99,
        registration_recency_weight=99,
    )

    assert updated.net_capital_growth_weight == 99
    assert db.commits == 1


# ---------------------------------------------------------------------------
# Endpoint layer — sum-to-100 validation rule
# (app/api/v1/endpoints/settings.py :: update_scoring_settings, ~L77)
# ---------------------------------------------------------------------------


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="test-admin",
        name="Test Admin",
        email="admin@example.com",
        role="admin",
        session_expires_at=datetime(2099, 1, 1),
    )


class _CommitStub:
    """DB session stand-in for the endpoint: only ``commit`` is reached
    (persistence + scoring recompute are patched out)."""

    async def commit(self) -> None:
        return None


async def _fake_db_session():
    yield _CommitStub()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def admin_with_stubbed_persistence():
    """Admin caller + DB/service/repository stubbed so the endpoint runs to
    the validation branch (and, on the happy path, to a stubbed persist)
    without any Postgres or scoring recompute."""
    app.dependency_overrides[get_current_user] = lambda: _admin()
    app.dependency_overrides[get_db_session] = _fake_db_session

    persisted = ScoringSetting(
        id=1,
        settings_key="default",
        net_capital_growth_weight=40,
        clearing_arrangement_weight=30,
        financial_health_weight=20,
        registration_recency_weight=10,
    )

    with patch.object(
        settings_service,
        "update_scoring_settings",
        new=AsyncMock(return_value=persisted),
    ) as update_mock, patch.object(
        repository, "refresh_lead_scores", new=AsyncMock(return_value=None)
    ):
        try:
            yield update_mock
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session, None)


async def test_update_scoring_endpoint_rejects_non_100_sum(
    admin_with_stubbed_persistence,
) -> None:
    update_mock = admin_with_stubbed_persistence
    async with _client() as client:
        response = await client.put(
            "/api/v1/settings/scoring",
            json={
                "net_capital_growth_weight": 50,
                "clearing_arrangement_weight": 30,
                "financial_health_weight": 20,
                "registration_recency_weight": 10,  # sums to 110
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Scoring weights must total 100."
    # Validation short-circuits before the persistence call.
    update_mock.assert_not_awaited()


async def test_update_scoring_endpoint_accepts_sum_of_100(
    admin_with_stubbed_persistence,
) -> None:
    update_mock = admin_with_stubbed_persistence
    async with _client() as client:
        response = await client.put(
            "/api/v1/settings/scoring",
            json={
                "net_capital_growth_weight": 40,
                "clearing_arrangement_weight": 30,
                "financial_health_weight": 20,
                "registration_recency_weight": 10,  # sums to 100
            },
        )

    assert response.status_code == 200
    update_mock.assert_awaited_once()


async def test_update_scoring_endpoint_blocks_non_admin() -> None:
    """Regression guard: the weight write stays admin-only."""
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="viewer",
        name="Viewer",
        email="viewer@example.com",
        role="viewer",
        session_expires_at=datetime(2099, 1, 1),
    )
    app.dependency_overrides[get_db_session] = _fake_db_session
    try:
        async with _client() as client:
            response = await client.put(
                "/api/v1/settings/scoring",
                json={
                    "net_capital_growth_weight": 40,
                    "clearing_arrangement_weight": 30,
                    "financial_health_weight": 20,
                    "registration_recency_weight": 10,
                },
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
