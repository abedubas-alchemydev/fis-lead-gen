"""API tests for ``GET /outreach/contacts/firms``.

Integration-marked -- the endpoint UNIONs three contact tables, and the
correlated COUNT subqueries are the whole point of the test, so a mock
session would just be re-asserting our mock. Touches a real Postgres.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import httpx
import pytest
from sqlalchemy import delete

from app.db.session import SessionLocal
from app.main import app
from app.models.advisor_contact import AdvisorContact
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investment_advisor import InvestmentAdvisor
from app.models.investor_contact import InvestorContact
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


async def _seed_one_firm_per_kind(
    token: str,
) -> tuple[int, int, int, list[int], list[int], list[int]]:
    """Seed one firm per entity_kind, each with two contacts:
    one email-only, one phone-only. The firm name carries ``token`` so
    the list query's ``q`` filter can pick them out reliably.
    """
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=f"BD {token} LLP",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        advisor = InvestmentAdvisor(name=f"Advisor {token} Group", status="active")
        investor = InstitutionalInvestor(name=f"Investor {token} Capital")
        session.add_all([bd, advisor, investor])
        await session.commit()
        await session.refresh(bd)
        await session.refresh(advisor)
        await session.refresh(investor)

        bd_contacts = [
            ExecutiveContact(
                bd_id=bd.id,
                name="BD Email Only",
                title="CFO",
                email="bd-email@example.com",
                phone=None,
            ),
            ExecutiveContact(
                bd_id=bd.id,
                name="BD Phone Only",
                title="COO",
                email=None,
                phone="+1-555-0100",
            ),
        ]
        advisor_contacts = [
            AdvisorContact(
                advisor_id=advisor.id,
                name="Advisor Email Only",
                title="MD",
                email="advisor-email@example.com",
                phone=None,
            ),
            AdvisorContact(
                advisor_id=advisor.id,
                name="Advisor Phone Only",
                title="PM",
                email=None,
                phone="+1-555-0200",
            ),
        ]
        investor_contacts = [
            InvestorContact(
                investor_id=investor.id,
                name="Investor Email Only",
                title="CIO",
                email="investor-email@example.com",
                phone=None,
            ),
            InvestorContact(
                investor_id=investor.id,
                name="Investor Phone Only",
                title="Analyst",
                email=None,
                phone="+1-555-0300",
            ),
        ]
        session.add_all(bd_contacts + advisor_contacts + investor_contacts)
        await session.commit()
        return (
            bd.id,
            advisor.id,
            investor.id,
            [c.id for c in bd_contacts],
            [c.id for c in advisor_contacts],
            [c.id for c in investor_contacts],
        )


async def _cleanup(
    user_ids: list[str],
    bd_ids: list[int],
    advisor_ids: list[int],
    investor_ids: list[int],
) -> None:
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
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


async def test_firms_list_unions_three_entity_kinds_with_correct_counts() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    bd_id, advisor_id, investor_id, *_ = await _seed_one_firm_per_kind(token)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/firms", params={"q": token}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 3
        items = body["items"]
        assert {item["entity_kind"] for item in items} == {
            "broker_dealer",
            "advisor",
            "institutional_investor",
        }
        for item in items:
            # Two contacts per firm: one email-only + one phone-only.
            assert item["contact_count"] == 2
            assert item["with_email_count"] == 1
            assert item["with_phone_count"] == 1
            assert item["gap_fill_in_progress"] is False
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [advisor_id], [investor_id])


async def test_firms_list_excludes_firms_with_zero_contacts() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    # Seed a BD with no contacts and another with one contact.
    async with SessionLocal() as session:
        bd_empty = BrokerDealer(
            name=f"Empty BD {token}",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        bd_filled = BrokerDealer(
            name=f"Filled BD {token}",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add_all([bd_empty, bd_filled])
        await session.commit()
        await session.refresh(bd_empty)
        await session.refresh(bd_filled)
        session.add(
            ExecutiveContact(
                bd_id=bd_filled.id,
                name="Only Contact",
                title="CEO",
                email="only@example.com",
            )
        )
        await session.commit()
        bd_ids = [bd_empty.id, bd_filled.id]

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/firms", params={"q": token}
            )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        names = {item["name"] for item in items}
        # Only the filled BD makes it in -- the empty one is filtered out.
        assert any("Filled BD" in n for n in names)
        assert not any("Empty BD" in n for n in names)
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], bd_ids, [], [])


async def test_firms_list_respects_entity_kind_filter() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    bd_id, advisor_id, investor_id, *_ = await _seed_one_firm_per_kind(token)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/firms",
                params={"q": token, "entity_kind": "advisor"},
            )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert {item["entity_kind"] for item in items} == {"advisor"}
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [advisor_id], [investor_id])


async def test_firms_list_requires_feature_permission() -> None:
    user_id = await _seed_user()
    # Override with a user that has SENT_OUTREACH but NOT OUTREACH_CONTACTS.
    def override() -> AuthenticatedUser:
        return AuthenticatedUser(
            id=user_id,
            name="Test User",
            email=f"{user_id}@example.com",
            role="viewer",
            feature_permissions=["sent_outreach"],
            session_expires_at=datetime(2099, 1, 1),
        )

    app.dependency_overrides[get_current_user] = override
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/firms", params={"q": "anything"}
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [])
