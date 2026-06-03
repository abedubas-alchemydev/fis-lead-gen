"""API tests for ``POST /outreach/contacts/firms/{kind}/{id}/gap-fill``.

Integration-marked -- the dispatch endpoint creates a real PipelineRun
row and the test asserts on its pipeline_name + notes marker. The
background runner itself is monkey-patched to a no-op so the test
doesn't fan out to Apollo / PDL / Hunter / Snov.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete, select

from app.api.v1.endpoints import outreach as outreach_endpoint
from app.db.session import SessionLocal
from app.main import app
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investment_advisor import InvestmentAdvisor
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Test User",
        email=f"{user_id}@example.com",
        role="viewer",
        feature_permissions=["outreach_contacts"],
        session_expires_at=datetime(2099, 1, 1),
    )


async def _seed_user() -> str:
    user_id = f"test-user-{secrets.token_hex(6)}"
    async with SessionLocal() as session:
        session.add(
            AuthUser(
                id=user_id,
                name="Test User",
                email=f"{user_id}@example.com",
                email_verified=False,
                role="viewer",
                status="active",
            )
        )
        await session.commit()
    return user_id


async def _cleanup(
    user_ids: list[str],
    bd_ids: list[int],
    advisor_ids: list[int],
    investor_ids: list[int],
    run_ids: list[int],
) -> None:
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        if run_ids:
            await session.execute(
                delete(PipelineRun).where(PipelineRun.id.in_(run_ids))
            )
        if bd_ids:
            await session.execute(
                delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids))
            )
        if advisor_ids:
            await session.execute(
                delete(InvestmentAdvisor).where(InvestmentAdvisor.id.in_(advisor_ids))
            )
        if investor_ids:
            await session.execute(
                delete(InstitutionalInvestor).where(
                    InstitutionalInvestor.id.in_(investor_ids)
                )
            )
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture(autouse=True)
def _noop_background_runners(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the three background runners with no-ops so the dispatch
    test doesn't fan out to live providers."""

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        outreach_endpoint, "run_gap_fill_bd_contacts_background", _noop
    )
    monkeypatch.setattr(
        outreach_endpoint, "run_gap_fill_advisor_contacts_background", _noop
    )
    monkeypatch.setattr(
        outreach_endpoint, "run_gap_fill_ii_contacts_background", _noop
    )


async def test_dispatch_broker_dealer_creates_pipeline_run_with_bd_marker() -> None:
    user_id = await _seed_user()
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=f"BD {secrets.token_hex(4)}",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add(bd)
        await session.commit()
        await session.refresh(bd)
        bd_id = bd.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    run_id: int | None = None
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/outreach/contacts/firms/broker_dealer/{bd_id}/gap-fill"
            )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["entity_kind"] == "broker_dealer"
        assert body["entity_id"] == bd_id
        assert body["status"] == "queued"
        run_id = body["run_id"]

        async with SessionLocal() as session:
            run = (
                await session.execute(select(PipelineRun).where(PipelineRun.id == run_id))
            ).scalar_one()
            assert run.pipeline_name == "broker_dealer_gap_fill_contacts"
            notes = json.loads(run.notes)
            assert notes["bd_id"] == bd_id
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [], [], [run_id] if run_id else [])


async def test_dispatch_advisor_creates_pipeline_run_with_advisor_marker() -> None:
    user_id = await _seed_user()
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(
            name=f"Advisor {secrets.token_hex(4)}", status="active"
        )
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)
        advisor_id = advisor.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    run_id: int | None = None
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/outreach/contacts/firms/advisor/{advisor_id}/gap-fill"
            )
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        async with SessionLocal() as session:
            run = (
                await session.execute(select(PipelineRun).where(PipelineRun.id == run_id))
            ).scalar_one()
            assert run.pipeline_name == "investment_advisor_gap_fill_contacts"
            notes = json.loads(run.notes)
            assert notes["advisor_id"] == advisor_id
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id], [], [run_id] if run_id else [])


async def test_dispatch_investor_creates_pipeline_run_with_investor_marker() -> None:
    user_id = await _seed_user()
    async with SessionLocal() as session:
        investor = InstitutionalInvestor(name=f"II {secrets.token_hex(4)}")
        session.add(investor)
        await session.commit()
        await session.refresh(investor)
        investor_id = investor.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    run_id: int | None = None
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/contacts/firms/institutional_investor/"
                f"{investor_id}/gap-fill"
            )
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        async with SessionLocal() as session:
            run = (
                await session.execute(select(PipelineRun).where(PipelineRun.id == run_id))
            ).scalar_one()
            assert run.pipeline_name == "institutional_investor_gap_fill_contacts"
            notes = json.loads(run.notes)
            assert notes["investor_id"] == investor_id
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [investor_id], [run_id] if run_id else [])


async def test_dispatch_ignores_cooldown_demo_override() -> None:
    # DEMO: the 30-day gap-fill cooldown was removed so "Enrich all" is always
    # actionable for the client demo. A firm gap-filled 5 days ago must now
    # still dispatch (202 + a fresh PipelineRun) instead of the old 429.
    # Restore the 429 assertion when the cooldown gate is reinstated.
    user_id = await _seed_user()
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(
            name=f"CooldownAdvisor {secrets.token_hex(4)}",
            status="active",
            last_gap_fill_attempt_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)
        advisor_id = advisor.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    run_id: int | None = None
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/outreach/contacts/firms/advisor/{advisor_id}/gap-fill"
            )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "queued"
        run_id = body["run_id"]
        assert run_id is not None
        # A run IS created now that the cooldown no longer rejects the request.
        async with SessionLocal() as session:
            run = (
                await session.execute(
                    select(PipelineRun).where(PipelineRun.id == run_id)
                )
            ).scalar_one()
            assert run.pipeline_name == "investment_advisor_gap_fill_contacts"
    finally:
        app.dependency_overrides.clear()
        await _cleanup(
            [user_id], [], [advisor_id], [], [run_id] if run_id else []
        )


async def test_dispatch_returns_404_for_missing_firm() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/contacts/firms/broker_dealer/999999999/gap-fill"
            )
        assert response.status_code == 404, response.text
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [], [])
