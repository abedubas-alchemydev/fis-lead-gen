"""API tests: clearing-membership surfacing on the profile endpoints.

Hits a real Postgres. Verifies the BD + IA profile responses carry full
membership provenance (active + needs_review) under ``clearing_memberships``
and that the firm detail's ``member_agencies`` lists only ACTIVE agencies.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import httpx
import pytest
from sqlalchemy import delete

from app.db.session import SessionLocal
from app.main import app
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.clearing_agency_membership import ClearingAgencyMembership
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Test User",
        email=f"{user_id}@example.com",
        role="viewer",
        feature_permissions=["master_list", "investment_advisors"],
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


def _membership(*, bd_id=None, advisor_id=None, agency, status="active", member_number=None):
    return ClearingAgencyMembership(
        broker_dealer_id=bd_id,
        advisor_id=advisor_id,
        agency=agency,
        member_number=member_number,
        member_name_raw="Seed Firm",
        source_file="occ_members.csv",
        source_version="abc123",
        match_method="exact_normalized",
        match_confidence=100.0,
        status=status,
    )


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_bd_profile_returns_memberships_and_active_agencies() -> None:
    user_id = await _seed_user()
    async with SessionLocal() as session:
        bd = BrokerDealer(name="Membership Test BD", matched_source="edgar", status="active")
        session.add(bd)
        await session.commit()
        await session.refresh(bd)
        bd_id = bd.id
        session.add_all([
            _membership(bd_id=bd_id, agency="OCC", member_number="OCC-1"),
            _membership(bd_id=bd_id, agency="DTC"),
            _membership(bd_id=bd_id, agency="NSCC", status="needs_review"),
        ])
        await session.commit()

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            resp = await client.get(f"/api/v1/broker-dealers/{bd_id}/profile")
        assert resp.status_code == 200
        body = resp.json()
        # Active-only set on the detail object.
        assert body["broker_dealer"]["member_agencies"] == ["DTC", "OCC"]
        # Full provenance (active + needs_review), ordered by agency.
        memberships = body["clearing_memberships"]
        assert [m["agency"] for m in memberships] == ["DTC", "NSCC", "OCC"]
        occ = next(m for m in memberships if m["agency"] == "OCC")
        assert occ["member_number"] == "OCC-1"
        assert occ["match_method"] == "exact_normalized"
        assert occ["status"] == "active"
        assert next(m for m in memberships if m["agency"] == "NSCC")["status"] == "needs_review"
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id == bd_id))
            await session.execute(delete(AuthUser).where(AuthUser.id == user_id))
            await session.commit()


async def test_ia_profile_returns_memberships() -> None:
    user_id = await _seed_user()
    async with SessionLocal() as session:
        ia = InvestmentAdvisor(name="Membership Test IA", matched_source="iapd", status="active")
        session.add(ia)
        await session.commit()
        await session.refresh(ia)
        advisor_id = ia.id
        session.add(_membership(advisor_id=advisor_id, agency="OCC"))
        await session.commit()

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            resp = await client.get(f"/api/v1/investment-advisors/{advisor_id}/profile")
        assert resp.status_code == 200
        body = resp.json()
        assert body["advisor"]["member_agencies"] == ["OCC"]
        assert [m["agency"] for m in body["clearing_memberships"]] == ["OCC"]
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(delete(InvestmentAdvisor).where(InvestmentAdvisor.id == advisor_id))
            await session.execute(delete(AuthUser).where(AuthUser.id == user_id))
            await session.commit()
