"""Integration tests for the admin ``/api/v1/shares`` lifecycle.

Touch a real Postgres (auth mocked via ``dependency_overrides``). Exercises the
snapshot builder end-to-end: eligible-vs-skipped partitioning, position
ordering, the 80-cap, and the rotate/revoke/unrevoke/delete lifecycle with
audit rows.

Requires migration 20260708_0001 (favorite_list_item.bank_id, from the
parallel banks-in-favorites slice) to be applied — runs in the merged tree at
QA/CI, not in the isolated backend-core worktree.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import httpx
import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.main import app
from app.models.audit_log import AuditLog
from app.models.auth import AuthUser
from app.models.bank import Bank
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investment_advisor import InvestmentAdvisor
from app.models.lead_share import LeadShare
from app.models.reporting_owner import ReportingOwner
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _admin(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Admin",
        email=f"{user_id}@example.com",
        role="admin",
        feature_permissions=[],
        session_expires_at=datetime(2099, 1, 1),
    )


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _seed_full_list() -> tuple[str, int, dict]:
    """Admin + a list holding 2 BDs, 1 advisor, 1 bank (eligible) plus 1
    institutional investor and 1 reporting owner (skipped)."""
    user_id = f"admin-{secrets.token_hex(6)}"
    async with SessionLocal() as s:
        s.add(
            AuthUser(
                id=user_id,
                name="Admin",
                email=f"{user_id}@example.com",
                email_verified=True,
                role="admin",
                status="active",
            )
        )
        bd1 = BrokerDealer(name="BD One", matched_source="edgar", is_deficient=False, status="active")
        bd2 = BrokerDealer(name="BD Two", matched_source="edgar", is_deficient=False, status="active")
        adv = InvestmentAdvisor(name="Adv One")
        bank = Bank(name="Bank One", charter_status="approved", source="occ")
        inv = InstitutionalInvestor(name="II One")
        owner = ReportingOwner(cik="0000001", name="Owner One")
        s.add_all([bd1, bd2, adv, bank, inv, owner])
        await s.commit()
        for obj in (bd1, bd2, adv, bank, inv, owner):
            await s.refresh(obj)

        fl = FavoriteList(user_id=user_id, name="FIS Q3 Prospects")
        s.add(fl)
        await s.commit()
        await s.refresh(fl)

        # Insert in a known order; snapshot order is created_at desc, id desc.
        s.add_all(
            [
                FavoriteListItem(list_id=fl.id, broker_dealer_id=bd1.id),
                FavoriteListItem(list_id=fl.id, broker_dealer_id=bd2.id),
                FavoriteListItem(list_id=fl.id, advisor_id=adv.id),
                FavoriteListItem(list_id=fl.id, bank_id=bank.id),
                FavoriteListItem(list_id=fl.id, institutional_investor_id=inv.id),
                FavoriteListItem(list_id=fl.id, reporting_owner_id=owner.id),
            ]
        )
        await s.commit()
        ids = {
            "bd1": bd1.id, "bd2": bd2.id, "adv": adv.id, "bank": bank.id,
            "inv": inv.id, "owner": owner.id,
        }
        return user_id, fl.id, ids


async def _cleanup(user_id: str, ids: dict) -> None:
    async with SessionLocal() as s:
        await s.execute(delete(LeadShare).where(LeadShare.created_by == user_id))
        await s.execute(delete(FavoriteList).where(FavoriteList.user_id == user_id))
        await s.execute(delete(BrokerDealer).where(BrokerDealer.id.in_([ids["bd1"], ids["bd2"]])))
        await s.execute(delete(InvestmentAdvisor).where(InvestmentAdvisor.id == ids["adv"]))
        await s.execute(delete(Bank).where(Bank.id == ids["bank"]))
        await s.execute(delete(InstitutionalInvestor).where(InstitutionalInvestor.id == ids["inv"]))
        await s.execute(delete(ReportingOwner).where(ReportingOwner.id == ids["owner"]))
        await s.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
        await s.execute(delete(AuthUser).where(AuthUser.id == user_id))
        await s.commit()


async def test_full_admin_lifecycle() -> None:
    user_id, list_id, ids = await _seed_full_list()
    app.dependency_overrides[get_current_user] = lambda: _admin(user_id)
    try:
        async with _client() as client:
            # ── create ──
            resp = await client.post("/api/v1/shares", json={"favorite_list_id": list_id})
            assert resp.status_code == 201, resp.text
            body = resp.json()
            share_id = body["share"]["id"]
            password = body["password"]
            assert body["share"]["item_count"] == 4  # 2 BD + advisor + bank
            assert body["skipped"] == {"institutional_investors": 1, "reporting_owners": 1}
            assert len(body["warnings"]) == 2

            # ── detail: positions 1..4, three kinds, no password_hash ──
            detail = (await client.get(f"/api/v1/shares/{share_id}")).json()
            positions = [it["position"] for it in detail["items"]]
            assert positions == [1, 2, 3, 4]
            kinds = {it["kind"] for it in detail["items"]}
            assert kinds == {"broker_dealer", "advisor", "bank"}

            # ── rotate: new password, old differs ──
            rot = await client.post(f"/api/v1/shares/{share_id}/rotate-password")
            assert rot.status_code == 200
            assert rot.json()["password"] != password

            # ── revoke / unrevoke ──
            assert (await client.post(f"/api/v1/shares/{share_id}/revoke")).status_code == 200
            assert (await client.post(f"/api/v1/shares/{share_id}/revoke")).status_code == 409
            # rotate on a revoked share is blocked
            assert (await client.post(f"/api/v1/shares/{share_id}/rotate-password")).status_code == 409
            assert (await client.post(f"/api/v1/shares/{share_id}/unrevoke")).status_code == 200
            assert (await client.post(f"/api/v1/shares/{share_id}/unrevoke")).status_code == 409

            # ── delete ──
            assert (await client.delete(f"/api/v1/shares/{share_id}")).status_code == 204
            assert (await client.get(f"/api/v1/shares/{share_id}")).status_code == 404

        # audit trail written
        async with SessionLocal() as s:
            actions = set(
                (
                    await s.execute(
                        select(AuditLog.action).where(AuditLog.user_id == user_id)
                    )
                ).scalars().all()
            )
        assert {
            "lead_share_created",
            "lead_share_password_rotated",
            "lead_share_revoked",
            "lead_share_unrevoked",
            "lead_share_deleted",
        } <= actions
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_id, ids)


async def test_empty_after_skips_is_400() -> None:
    user_id = f"admin-{secrets.token_hex(6)}"
    async with SessionLocal() as s:
        s.add(
            AuthUser(
                id=user_id, name="Admin", email=f"{user_id}@example.com",
                email_verified=True, role="admin", status="active",
            )
        )
        owner = ReportingOwner(cik="0000002", name="Owner Only")
        s.add(owner)
        await s.commit()
        await s.refresh(owner)
        fl = FavoriteList(user_id=user_id, name="Insiders only")
        s.add(fl)
        await s.commit()
        await s.refresh(fl)
        s.add(FavoriteListItem(list_id=fl.id, reporting_owner_id=owner.id))
        await s.commit()
        list_id, owner_id = fl.id, owner.id
    app.dependency_overrides[get_current_user] = lambda: _admin(user_id)
    try:
        async with _client() as client:
            resp = await client.post("/api/v1/shares", json={"favorite_list_id": list_id})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "share_source_empty"
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as s:
            await s.execute(delete(FavoriteList).where(FavoriteList.user_id == user_id))
            await s.execute(delete(ReportingOwner).where(ReportingOwner.id == owner_id))
            await s.execute(delete(AuthUser).where(AuthUser.id == user_id))
            await s.commit()


async def test_over_cap_is_400() -> None:
    user_id = f"admin-{secrets.token_hex(6)}"
    async with SessionLocal() as s:
        s.add(
            AuthUser(
                id=user_id, name="Admin", email=f"{user_id}@example.com",
                email_verified=True, role="admin", status="active",
            )
        )
        bds = [
            BrokerDealer(name=f"Cap BD {i}", matched_source="edgar", is_deficient=False, status="active")
            for i in range(81)
        ]
        s.add_all(bds)
        await s.commit()
        for bd in bds:
            await s.refresh(bd)
        fl = FavoriteList(user_id=user_id, name="Over cap")
        s.add(fl)
        await s.commit()
        await s.refresh(fl)
        s.add_all([FavoriteListItem(list_id=fl.id, broker_dealer_id=bd.id) for bd in bds])
        await s.commit()
        list_id = fl.id
        bd_ids = [bd.id for bd in bds]
    app.dependency_overrides[get_current_user] = lambda: _admin(user_id)
    try:
        async with _client() as client:
            resp = await client.post("/api/v1/shares", json={"favorite_list_id": list_id})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "share_too_large"
        assert resp.json()["detail"]["eligible"] == 81
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as s:
            await s.execute(delete(FavoriteList).where(FavoriteList.user_id == user_id))
            await s.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids)))
            await s.execute(delete(AuthUser).where(AuthUser.id == user_id))
            await s.commit()
