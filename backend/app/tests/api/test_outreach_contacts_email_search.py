"""API tests for ``GET /outreach/contacts/email-search``.

The "email mode" of the contacts search: typing an email address returns
the matching EMAIL rows directly (flat, paginated) instead of firm rows,
drawn from both the typed contact tables and ``discovered_email``.

Integration-marked -- the endpoint joins six SELECTs (three typed contact
tables + ``discovered_email`` via ``bd_id`` / ``advisor_id`` / the
institutional-investor ``advisor_id`` overlap) and dedups in Python, so a
mock session would only re-assert the mock. Touches a real Postgres.
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
from app.models.discovered_email import DiscoveredEmail
from app.models.executive_contact import ExecutiveContact
from app.models.extraction_run import ExtractionRun
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


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _make_discovered(
    run_id: int,
    email: str,
    *,
    bd_id: int | None = None,
    advisor_id: int | None = None,
    source: str = "apollo",
    enriched_name: str | None = None,
    enriched_title: str | None = None,
) -> DiscoveredEmail:
    return DiscoveredEmail(
        run_id=run_id,
        email=email,
        domain=email.split("@", 1)[-1],
        source=source,
        bd_id=bd_id,
        advisor_id=advisor_id,
        enriched_name=enriched_name,
        enriched_title=enriched_title,
        enrichment_status="not_enriched",
    )


async def _cleanup(
    user_ids: list[str],
    bd_ids: list[int],
    advisor_ids: list[int],
    investor_ids: list[int],
    run_ids: list[int],
) -> None:
    async with SessionLocal() as session:
        # discovered_email cascades from extraction_run; delete runs first so
        # the rows go even though bd_id/advisor_id are ON DELETE SET NULL.
        if run_ids:
            await session.execute(
                delete(ExtractionRun).where(ExtractionRun.id.in_(run_ids))
            )
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


# --- typed contact match -------------------------------------------------


async def test_email_search_matches_executive_contact() -> None:
    """A typed ExecutiveContact whose email contains the needle returns a
    ``source="contact"`` row with ``contact_id`` + phone, linked to its BD."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=f"BD {token} LLP",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add(bd)
        await session.commit()
        await session.refresh(bd)

        contact = ExecutiveContact(
            bd_id=bd.id,
            name="Jordan Officer",
            title="CEO",
            email=f"jordan@{token}.com",
            phone="+1-555-0100",
        )
        session.add(contact)
        await session.commit()
        await session.refresh(contact)
        bd_id, contact_id = bd.id, contact.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/email-search",
                params={"q": f"jordan@{token}"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["page"] == 1
        assert body["limit"] == 50
        assert body["has_more"] is False
        row = body["results"][0]
        # Pinned field shape -- every key must be present.
        assert set(row.keys()) == {
            "entity_kind",
            "entity_id",
            "firm_name",
            "owner_name",
            "title",
            "email",
            "phone",
            "source",
            "contact_id",
        }
        assert row["entity_kind"] == "broker_dealer"
        assert row["entity_id"] == bd_id
        assert row["firm_name"] == f"BD {token} LLP"
        assert row["owner_name"] == "Jordan Officer"
        assert row["title"] == "CEO"
        assert row["email"] == f"jordan@{token}.com"
        assert row["phone"] == "+1-555-0100"
        assert row["source"] == "contact"
        assert row["contact_id"] == contact_id
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [], [], [])


# --- discovered email match ----------------------------------------------


async def test_email_search_matches_discovered_email() -> None:
    """A ``discovered_email`` whose address contains the needle returns a
    ``source="extracted"`` row with no ``contact_id`` and ``phone=None``."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=f"BD {token} LLP",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add(bd)
        await session.commit()
        await session.refresh(bd)

        run = ExtractionRun(domain=f"{token}.com", bd_id=bd.id, status="completed")
        session.add(run)
        await session.commit()
        await session.refresh(run)

        session.add(
            _make_discovered(
                run.id,
                f"cfo@{token}.com",
                bd_id=bd.id,
                source="hunter",
                enriched_name="Jane CFO",
                enriched_title="Chief Financial Officer",
            )
        )
        await session.commit()
        bd_id, run_id = bd.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/email-search",
                params={"q": f"cfo@{token}"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        row = body["results"][0]
        assert row["entity_kind"] == "broker_dealer"
        assert row["entity_id"] == bd_id
        assert row["firm_name"] == f"BD {token} LLP"
        assert row["owner_name"] == "Jane CFO"
        assert row["title"] == "Chief Financial Officer"
        assert row["email"] == f"cfo@{token}.com"
        assert row["phone"] is None
        assert row["source"] == "extracted"
        assert row["contact_id"] is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [], [], [run_id])


# --- dedup: same email in both sources -----------------------------------


async def test_email_search_dedupes_to_typed_contact() -> None:
    """When the same ``(entity_id, email)`` exists as both a typed contact
    and a discovered email, exactly one row comes back and it's the typed
    contact (``source="contact"``, carries ``contact_id``)."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    shared_email = f"shared@{token}.com"
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=f"BD {token} LLP",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add(bd)
        await session.commit()
        await session.refresh(bd)

        contact = ExecutiveContact(
            bd_id=bd.id,
            name="Shared Person",
            title="CEO",
            email=shared_email,
            phone="+1-555-0123",
        )
        session.add(contact)

        run = ExtractionRun(domain=f"{token}.com", bd_id=bd.id, status="completed")
        session.add(run)
        await session.commit()
        await session.refresh(contact)
        await session.refresh(run)

        # Same address, same firm -- the discovered row must be deduped out.
        session.add(_make_discovered(run.id, shared_email, bd_id=bd.id))
        await session.commit()
        bd_id, contact_id, run_id = bd.id, contact.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/email-search",
                params={"q": f"shared@{token}"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        row = body["results"][0]
        assert row["source"] == "contact"
        assert row["contact_id"] == contact_id
        assert row["phone"] == "+1-555-0123"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [], [], [run_id])


async def test_email_search_same_email_different_firms_kept_separate() -> None:
    """The dedup key includes the firm, so the same address at two distinct
    firms yields two rows (one per firm)."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    shared_email = f"info@{token}.com"
    async with SessionLocal() as session:
        bd_a = BrokerDealer(
            name=f"Alpha {token} LLP",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        bd_b = BrokerDealer(
            name=f"Beta {token} LLP",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add_all([bd_a, bd_b])
        await session.commit()
        await session.refresh(bd_a)
        await session.refresh(bd_b)

        session.add_all(
            [
                ExecutiveContact(
                    bd_id=bd_a.id, name="A Person", title="CEO", email=shared_email
                ),
                ExecutiveContact(
                    bd_id=bd_b.id, name="B Person", title="CEO", email=shared_email
                ),
            ]
        )
        await session.commit()
        bd_ids = [bd_a.id, bd_b.id]

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/email-search",
                params={"q": f"info@{token}"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        firm_ids = {row["entity_id"] for row in body["results"]}
        assert firm_ids == set(bd_ids)
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], bd_ids, [], [], [])


# --- pagination ----------------------------------------------------------


async def test_email_search_pagination_has_more() -> None:
    """With more matches than ``limit``, the first page reports
    ``has_more=True`` and returns exactly ``limit`` rows; the second page
    returns the remainder with ``has_more=False``."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=f"BD {token} LLP",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add(bd)
        await session.commit()
        await session.refresh(bd)

        # Three contacts whose emails all match the needle.
        session.add_all(
            [
                ExecutiveContact(
                    bd_id=bd.id,
                    name=f"Person {i}",
                    title="Staff",
                    email=f"person{i}@{token}.com",
                )
                for i in range(3)
            ]
        )
        await session.commit()
        bd_id = bd.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            page1 = await client.get(
                "/api/v1/outreach/contacts/email-search",
                params={"q": token, "page": 1, "limit": 2},
            )
            page2 = await client.get(
                "/api/v1/outreach/contacts/email-search",
                params={"q": token, "page": 2, "limit": 2},
            )
        assert page1.status_code == 200, page1.text
        b1 = page1.json()
        assert b1["total"] == 3
        assert b1["page"] == 1
        assert b1["limit"] == 2
        assert len(b1["results"]) == 2
        assert b1["has_more"] is True

        assert page2.status_code == 200, page2.text
        b2 = page2.json()
        assert b2["total"] == 3
        assert b2["page"] == 2
        assert len(b2["results"]) == 1
        assert b2["has_more"] is False

        # No row repeats across the two pages.
        emails = {r["email"] for r in b1["results"]} | {
            r["email"] for r in b2["results"]
        }
        assert len(emails) == 3
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [], [], [])


# --- advisor + investor coverage -----------------------------------------


async def test_email_search_matches_advisor_contact() -> None:
    """An ``AdvisorContact`` email match links to its advisor firm."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(name=f"Advisor {token} Group", status="active")
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)

        contact = AdvisorContact(
            advisor_id=advisor.id,
            name="Morgan PM",
            title="Portfolio Manager",
            email=f"morgan@{token}.com",
        )
        session.add(contact)
        await session.commit()
        await session.refresh(contact)
        advisor_id, contact_id = advisor.id, contact.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/email-search",
                params={"q": f"morgan@{token}"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        row = body["results"][0]
        assert row["entity_kind"] == "advisor"
        assert row["entity_id"] == advisor_id
        assert row["firm_name"] == f"Advisor {token} Group"
        assert row["source"] == "contact"
        assert row["contact_id"] == contact_id
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id], [], [])


async def test_email_search_matches_investor_contact() -> None:
    """An ``InvestorContact`` email match links to its institutional-investor
    firm via the investor's own id (not the advisor overlap)."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        investor = InstitutionalInvestor(name=f"Investor {token} Capital")
        session.add(investor)
        await session.commit()
        await session.refresh(investor)

        contact = InvestorContact(
            investor_id=investor.id,
            name="Riley CIO",
            title="Chief Investment Officer",
            email=f"riley@{token}.com",
        )
        session.add(contact)
        await session.commit()
        await session.refresh(contact)
        investor_id, contact_id = investor.id, contact.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/email-search",
                params={"q": f"riley@{token}"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        row = body["results"][0]
        assert row["entity_kind"] == "institutional_investor"
        assert row["entity_id"] == investor_id
        assert row["firm_name"] == f"Investor {token} Capital"
        assert row["source"] == "contact"
        assert row["contact_id"] == contact_id
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [investor_id], [])


async def test_email_search_investor_discovered_via_advisor_link() -> None:
    """An IAPD-overlap investor surfaces a discovered email through its linked
    advisor's ``advisor_id`` -- mirroring the firms-list investor branch. The
    advisor firm (which owns the same discovered email) also surfaces, so both
    the advisor and the overlap investor appear, one row each."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(name=f"Advisor {token} Group", status="active")
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)

        investor = InstitutionalInvestor(
            name=f"Investor {token} Capital", advisor_id=advisor.id
        )
        session.add(investor)
        await session.commit()
        await session.refresh(investor)

        run = ExtractionRun(
            domain=f"{token}.com", advisor_id=advisor.id, status="completed"
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        session.add(
            _make_discovered(
                run.id, f"cio@{token}.com", advisor_id=advisor.id, source="apollo"
            )
        )
        await session.commit()
        advisor_id, investor_id, run_id = advisor.id, investor.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/email-search",
                params={"q": f"cio@{token}"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        # The same discovered email surfaces under BOTH the advisor (direct
        # advisor_id link) and the overlap investor (advisor_id overlap).
        by_kind = {row["entity_kind"]: row for row in body["results"]}
        assert "advisor" in by_kind
        assert "institutional_investor" in by_kind
        assert by_kind["advisor"]["entity_id"] == advisor_id
        assert by_kind["institutional_investor"]["entity_id"] == investor_id
        assert by_kind["institutional_investor"]["source"] == "extracted"
        assert by_kind["institutional_investor"]["email"] == f"cio@{token}.com"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id], [investor_id], [run_id])


# --- blank query + auth --------------------------------------------------


async def test_email_search_blank_query_returns_empty_page() -> None:
    """A whitespace-only ``q`` short-circuits to an empty page (no 422),
    with pagination metadata intact."""
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/email-search",
                params={"q": "   ", "page": 1, "limit": 50},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["results"] == []
        assert body["total"] == 0
        assert body["page"] == 1
        assert body["limit"] == 50
        assert body["has_more"] is False
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [], [])


async def test_email_search_requires_feature_permission() -> None:
    """A user without OUTREACH_CONTACTS is rejected with 403, same gate as
    the firms-list endpoint."""
    user_id = await _seed_user()

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
                "/api/v1/outreach/contacts/email-search",
                params={"q": "anything@example.com"},
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [], [])
