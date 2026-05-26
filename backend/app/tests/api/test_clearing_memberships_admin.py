"""API tests for the admin clearing-membership review queue.

Hits a real Postgres. Verifies the admin-gated GET surfaces needs_review
rows with firm name + side, approve flips status=active and stamps
match_method='manual' AND cascades-reject sibling candidates for the
same directory entry, reject flips to rejected, and non-admins get 403.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import httpx
import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.main import app
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.clearing_agency_membership import ClearingAgencyMembership
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str, role: str = "admin") -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Test",
        email=f"{user_id}@example.com",
        role=role,
        feature_permissions=["master_list", "investment_advisors"],
        session_expires_at=datetime(2099, 1, 1),
    )


async def _seed_user(role: str = "admin") -> str:
    user_id = f"test-user-{secrets.token_hex(6)}"
    async with SessionLocal() as session:
        session.add(
            AuthUser(
                id=user_id,
                name="Test",
                email=f"{user_id}@example.com",
                email_verified=False,
                role=role,
                status="active",
            )
        )
        await session.commit()
    return user_id


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _membership(*, bd_id, agency, member_name_raw, status="needs_review"):
    return ClearingAgencyMembership(
        broker_dealer_id=bd_id,
        agency=agency,
        member_name_raw=member_name_raw,
        source_file="dtc_participants.csv",
        source_version="abc",
        match_method="exact_normalized",
        match_confidence=60.0,
        status=status,
    )


async def test_review_queue_returns_needs_review_with_firm_name() -> None:
    admin_id = await _seed_user(role="admin")
    bd_ids: list[int] = []
    async with SessionLocal() as session:
        bd_a = BrokerDealer(name="Morgan Stanley & Co. LLC", matched_source="edgar", status="active")
        bd_b = BrokerDealer(name="Morgan Stanley", matched_source="edgar", status="active")
        bd_active = BrokerDealer(name="Goldman Sachs & Co. LLC", matched_source="edgar", status="active")
        session.add_all([bd_a, bd_b, bd_active])
        await session.commit()
        for bd in (bd_a, bd_b, bd_active):
            await session.refresh(bd)
            bd_ids.append(bd.id)
        session.add_all([
            _membership(bd_id=bd_a.id, agency="DTC", member_name_raw="Morgan Stanley & Co. LLC"),
            _membership(bd_id=bd_b.id, agency="DTC", member_name_raw="Morgan Stanley & Co. LLC"),
            # An ACTIVE row must NOT appear in the review queue.
            _membership(bd_id=bd_active.id, agency="OCC", member_name_raw="Goldman Sachs & Co. LLC", status="active"),
        ])
        await session.commit()

    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id)
    try:
        async with _client() as client:
            resp = await client.get("/api/v1/clearing-memberships/review")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2  # only the two needs_review rows
        firms = sorted(r["firm_name"] for r in body["items"])
        assert firms == ["Morgan Stanley", "Morgan Stanley & Co. LLC"]
        assert all(r["firm_side"] == "broker_dealer" for r in body["items"])
        assert all(r["agency"] == "DTC" for r in body["items"])
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids)))
            await session.execute(delete(AuthUser).where(AuthUser.id == admin_id))
            await session.commit()


async def test_approve_flips_status_and_cascades_reject_to_siblings() -> None:
    admin_id = await _seed_user(role="admin")
    bd_ids: list[int] = []
    async with SessionLocal() as session:
        bd_a = BrokerDealer(name="Morgan Stanley & Co. LLC", matched_source="edgar", status="active")
        bd_b = BrokerDealer(name="Morgan Stanley", matched_source="edgar", status="active")
        bd_other = BrokerDealer(name="Unrelated Other Firm", matched_source="edgar", status="active")
        session.add_all([bd_a, bd_b, bd_other])
        await session.commit()
        for bd in (bd_a, bd_b, bd_other):
            await session.refresh(bd)
            bd_ids.append(bd.id)
        ma = _membership(bd_id=bd_a.id, agency="DTC", member_name_raw="Morgan Stanley & Co. LLC")
        mb = _membership(bd_id=bd_b.id, agency="DTC", member_name_raw="Morgan Stanley & Co. LLC")
        # A different directory entry — must NOT be affected by the cascade.
        m_other = _membership(bd_id=bd_other.id, agency="DTC", member_name_raw="Other Firm LLC")
        session.add_all([ma, mb, m_other])
        await session.commit()
        await session.refresh(ma); await session.refresh(mb); await session.refresh(m_other)
        ma_id, mb_id, mo_id = ma.id, mb.id, m_other.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id)
    try:
        async with _client() as client:
            resp = await client.post(f"/api/v1/clearing-memberships/{ma_id}/approve")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "active"
        assert body["match_method"] == "manual"

        async with SessionLocal() as session:
            sibling = (await session.execute(
                select(ClearingAgencyMembership).where(ClearingAgencyMembership.id == mb_id)
            )).scalar_one()
            assert sibling.status == "rejected"
            other = (await session.execute(
                select(ClearingAgencyMembership).where(ClearingAgencyMembership.id == mo_id)
            )).scalar_one()
            assert other.status == "needs_review"  # untouched
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids)))
            await session.execute(delete(AuthUser).where(AuthUser.id == admin_id))
            await session.commit()


async def test_reject_flips_status_only() -> None:
    admin_id = await _seed_user(role="admin")
    bd_ids: list[int] = []
    async with SessionLocal() as session:
        bd = BrokerDealer(name="Rejecting BD", matched_source="edgar", status="active")
        session.add(bd)
        await session.commit()
        await session.refresh(bd)
        bd_ids.append(bd.id)
        m = _membership(bd_id=bd.id, agency="NSCC", member_name_raw="Some Firm")
        session.add(m)
        await session.commit()
        await session.refresh(m)
        m_id = m.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id)
    try:
        async with _client() as client:
            resp = await client.post(f"/api/v1/clearing-memberships/{m_id}/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids)))
            await session.execute(delete(AuthUser).where(AuthUser.id == admin_id))
            await session.commit()


async def test_non_admin_gets_403_on_review_and_approve() -> None:
    user_id = await _seed_user(role="viewer")
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id, role="viewer")
    try:
        async with _client() as client:
            resp = await client.get("/api/v1/clearing-memberships/review")
            assert resp.status_code == 403
            resp = await client.post("/api/v1/clearing-memberships/1/approve")
            assert resp.status_code == 403
            resp = await client.post("/api/v1/clearing-memberships/1/reject")
            assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(delete(AuthUser).where(AuthUser.id == user_id))
            await session.commit()


async def test_approve_404_when_membership_missing() -> None:
    admin_id = await _seed_user(role="admin")
    app.dependency_overrides[get_current_user] = lambda: _override_user(admin_id)
    try:
        async with _client() as client:
            resp = await client.post("/api/v1/clearing-memberships/99999999/approve")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(delete(AuthUser).where(AuthUser.id == admin_id))
            await session.commit()
