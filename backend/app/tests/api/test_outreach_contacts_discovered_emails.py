"""API tests for surfacing Email-Extractor ``discovered_email`` rows on
``/outreach/contacts``.

Covers the new
``GET /outreach/contacts/firms/{entity_kind}/{entity_id}/discovered-emails``
endpoint plus the firms-list changes (decisions B-b2 + C-c1): a firm with
only discovered emails now appears in the list, the existing typed triad
(``contact_count`` / ``with_email_count`` / ``with_phone_count``) stays
typed-only, and a separate ``discovered_email_count`` is added.

Integration-marked -- the firms list UNIONs the typed contact tables with
correlated COUNT subqueries against ``discovered_email`` and the endpoint
joins through ``institutional_investors.advisor_id``, so a mock session
would only re-assert the mock. Touches a real Postgres.
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


def _make_discovered(
    run_id: int,
    email: str,
    *,
    bd_id: int | None = None,
    advisor_id: int | None = None,
    source: str = "apollo",
    enriched_name: str | None = None,
    enriched_title: str | None = None,
    enriched_phone: str | None = None,
    enriched_linkedin_url: str | None = None,
    enrichment_status: str = "not_enriched",
    confidence: float | None = None,
) -> DiscoveredEmail:
    return DiscoveredEmail(
        run_id=run_id,
        email=email,
        domain=email.split("@", 1)[-1],
        source=source,
        confidence=confidence,
        bd_id=bd_id,
        advisor_id=advisor_id,
        enriched_name=enriched_name,
        enriched_title=enriched_title,
        enriched_phone=enriched_phone,
        enriched_linkedin_url=enriched_linkedin_url,
        enrichment_status=enrichment_status,
    )


# --- new endpoint: discovered-emails by firm -----------------------------


async def test_discovered_emails_for_broker_dealer() -> None:
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

        session.add_all(
            [
                _make_discovered(
                    run.id,
                    f"ceo@{token}.com",
                    bd_id=bd.id,
                    source="hunter",
                    enriched_name="Jane CEO",
                    enriched_title="Chief Executive",
                    enriched_phone="+1-555-0101",
                    enriched_linkedin_url="https://linkedin.com/in/jane",
                    enrichment_status="enriched",
                    confidence=0.92,
                ),
                _make_discovered(
                    run.id,
                    f"cfo@{token}.com",
                    bd_id=bd.id,
                    source="apollo",
                ),
            ]
        )
        await session.commit()
        bd_id, run_id = bd.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/outreach/contacts/firms/broker_dealer/{bd_id}/discovered-emails"
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 2
        emails = {row["email"] for row in body}
        assert emails == {f"ceo@{token}.com", f"cfo@{token}.com"}

        ceo = next(r for r in body if r["email"] == f"ceo@{token}.com")
        # Pinned field shape -- every key must be present.
        assert set(ceo.keys()) == {
            "id",
            "email",
            "enriched_name",
            "enriched_title",
            "enriched_phone",
            "enriched_linkedin_url",
            "enrichment_status",
            "source",
            "confidence",
            "created_at",
        }
        assert ceo["enriched_name"] == "Jane CEO"
        assert ceo["enriched_title"] == "Chief Executive"
        assert ceo["enriched_phone"] == "+1-555-0101"
        assert ceo["enriched_linkedin_url"] == "https://linkedin.com/in/jane"
        assert ceo["enrichment_status"] == "enriched"
        assert ceo["source"] == "hunter"
        assert ceo["confidence"] == 0.92
        assert isinstance(ceo["id"], int)
        # created_at serializes as an ISO string.
        assert "T" in ceo["created_at"]

        cfo = next(r for r in body if r["email"] == f"cfo@{token}.com")
        assert cfo["enriched_name"] is None
        assert cfo["enrichment_status"] == "not_enriched"
        assert cfo["confidence"] is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [], [], [run_id])


async def test_discovered_emails_for_advisor() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(name=f"Advisor {token} Group", status="active")
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)

        run = ExtractionRun(
            domain=f"{token}.com", advisor_id=advisor.id, status="completed"
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        session.add(
            _make_discovered(
                run.id,
                f"pm@{token}.com",
                advisor_id=advisor.id,
                source="snov",
            )
        )
        await session.commit()
        advisor_id, run_id = advisor.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/outreach/contacts/firms/advisor/{advisor_id}/discovered-emails"
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body) == 1
        assert body[0]["email"] == f"pm@{token}.com"
        assert body[0]["source"] == "snov"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id], [], [run_id])


async def test_discovered_emails_investor_without_advisor_is_empty() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        # Pure-13F investor: no advisor_id, so it can never carry
        # discovered_email rows (those link via advisor_id only).
        investor = InstitutionalInvestor(name=f"Investor {token} Capital")
        session.add(investor)
        await session.commit()
        await session.refresh(investor)
        investor_id = investor.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/firms/"
                f"institutional_investor/{investor_id}/discovered-emails"
            )
        assert response.status_code == 200, response.text
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [investor_id], [])


async def test_discovered_emails_investor_via_advisor_link() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(name=f"Advisor {token} Group", status="active")
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)

        # IAPD-overlap investor: links to the advisor, so the advisor's
        # discovered emails surface under the investor too.
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
                run.id,
                f"cio@{token}.com",
                advisor_id=advisor.id,
                source="apollo",
            )
        )
        await session.commit()
        advisor_id, investor_id, run_id = advisor.id, investor.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/firms/"
                f"institutional_investor/{investor_id}/discovered-emails"
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body) == 1
        assert body[0]["email"] == f"cio@{token}.com"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id], [investor_id], [run_id])


async def test_discovered_emails_requires_feature_permission() -> None:
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
                "/api/v1/outreach/contacts/firms/broker_dealer/1/discovered-emails"
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [], [])


# --- firms list: B-b2 inclusion + C-c1 separate count --------------------


async def test_firms_list_includes_discovered_only_firm() -> None:
    """B-b2: a BD with ONLY discovered emails (no typed contacts) now
    appears in the list, with discovered_email_count > 0 and the typed
    counts all zero (C-c1)."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=f"Discovered Only BD {token}",
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

        session.add_all(
            [
                _make_discovered(run.id, f"a@{token}.com", bd_id=bd.id),
                _make_discovered(run.id, f"b@{token}.com", bd_id=bd.id),
            ]
        )
        await session.commit()
        bd_id, run_id = bd.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/firms", params={"q": token}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["entity_kind"] == "broker_dealer"
        assert item["entity_id"] == bd_id
        # Discovered-only: typed triad is all zero, discovered count is 2.
        assert item["contact_count"] == 0
        assert item["with_email_count"] == 0
        assert item["with_phone_count"] == 0
        assert item["discovered_email_count"] == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [], [], [run_id])


async def test_firms_list_typed_counts_unchanged_with_discovered() -> None:
    """C-c1: for a firm that has BOTH typed contacts and discovered
    emails, the typed triad stays typed-only and discovered_email_count
    is reported separately (no double-count)."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=f"Mixed BD {token}",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add(bd)
        await session.commit()
        await session.refresh(bd)

        # Two typed contacts: one email-only, one phone-only.
        session.add_all(
            [
                ExecutiveContact(
                    bd_id=bd.id,
                    name="Typed Email Only",
                    title="CFO",
                    email=f"typed-email@{token}.com",
                    phone=None,
                ),
                ExecutiveContact(
                    bd_id=bd.id,
                    name="Typed Phone Only",
                    title="COO",
                    email=None,
                    phone="+1-555-0900",
                ),
            ]
        )

        run = ExtractionRun(domain=f"{token}.com", bd_id=bd.id, status="completed")
        session.add(run)
        await session.commit()
        await session.refresh(run)

        # Three discovered emails on the same firm.
        session.add_all(
            [
                _make_discovered(run.id, f"d1@{token}.com", bd_id=bd.id),
                _make_discovered(run.id, f"d2@{token}.com", bd_id=bd.id),
                _make_discovered(run.id, f"d3@{token}.com", bd_id=bd.id),
            ]
        )
        await session.commit()
        bd_id, run_id = bd.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/firms", params={"q": token}
            )
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        # Typed triad: counts the 2 typed contacts only, NOT the 3
        # discovered emails.
        assert item["contact_count"] == 2
        assert item["with_email_count"] == 1
        assert item["with_phone_count"] == 1
        # Discovered count is separate.
        assert item["discovered_email_count"] == 3
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [], [], [run_id])


async def test_firms_list_discovered_only_firm_honors_entity_kind_filter() -> None:
    """A discovered-only firm still has its kind, so the entity_kind
    filter buckets it correctly: it shows under its own kind and is
    excluded by a different kind's filter."""
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(
            name=f"Discovered Only Advisor {token}", status="active"
        )
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)

        run = ExtractionRun(
            domain=f"{token}.com", advisor_id=advisor.id, status="completed"
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        session.add(_make_discovered(run.id, f"x@{token}.com", advisor_id=advisor.id))
        await session.commit()
        advisor_id, run_id = advisor.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            # Filter to advisor -> the discovered-only advisor shows.
            advisor_resp = await client.get(
                "/api/v1/outreach/contacts/firms",
                params={"q": token, "entity_kind": "advisor"},
            )
            # Filter to broker_dealer -> it must NOT show.
            bd_resp = await client.get(
                "/api/v1/outreach/contacts/firms",
                params={"q": token, "entity_kind": "broker_dealer"},
            )
        assert advisor_resp.status_code == 200, advisor_resp.text
        advisor_items = advisor_resp.json()["items"]
        assert {i["entity_kind"] for i in advisor_items} == {"advisor"}
        assert any(i["entity_id"] == advisor_id for i in advisor_items)

        assert bd_resp.status_code == 200, bd_resp.text
        assert bd_resp.json()["items"] == []
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id], [], [run_id])


# --- firms list: search needle matches discovered email / person --------


async def test_firms_list_search_matches_discovered_email_address() -> None:
    """Typing a discovered email address into the search box returns the
    firm that owns it, even when the needle is NOT a substring of the firm
    name -- proving the needle now hits DiscoveredEmail.email, not just the
    entity name."""
    user_id = await _seed_user()
    # Disjoint tokens: the firm name carries ``name_token``; the search
    # needle (``email_token``) appears only in the discovered email, so a
    # match can only come from the discovered-email branch.
    name_token = secrets.token_hex(4)
    email_token = secrets.token_hex(4)
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=f"BD {name_token} LLP",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add(bd)
        await session.commit()
        await session.refresh(bd)

        run = ExtractionRun(domain=f"{name_token}.com", bd_id=bd.id, status="completed")
        session.add(run)
        await session.commit()
        await session.refresh(run)

        session.add(
            _make_discovered(
                run.id,
                f"jane.doe@{email_token}.com",
                bd_id=bd.id,
                source="hunter",
            )
        )
        await session.commit()
        bd_id, run_id = bd.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            # Search by the email's local part -- only in the discovered row.
            response = await client.get(
                "/api/v1/outreach/contacts/firms", params={"q": email_token}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["entity_kind"] == "broker_dealer"
        assert item["entity_id"] == bd_id
        assert item["discovered_email_count"] == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [], [], [run_id])


async def test_firms_list_search_matches_discovered_enriched_name() -> None:
    """Searching by the enriched person name on a discovered email returns
    the firm -- the second searchable column (DiscoveredEmail.enriched_name)
    is wired up too."""
    user_id = await _seed_user()
    name_token = secrets.token_hex(4)
    person_token = secrets.token_hex(4)
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(name=f"Advisor {name_token} Group", status="active")
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)

        run = ExtractionRun(
            domain=f"{name_token}.com", advisor_id=advisor.id, status="completed"
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        session.add(
            _make_discovered(
                run.id,
                f"pm@{name_token}.com",
                advisor_id=advisor.id,
                source="snov",
                # Person name carries the disjoint needle; the email address
                # does not, so a match here proves enriched_name is searched.
                enriched_name=f"Portfolio Manager {person_token}",
            )
        )
        await session.commit()
        advisor_id, run_id = advisor.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/firms", params={"q": person_token}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["entity_kind"] == "advisor"
        assert item["entity_id"] == advisor_id
        assert item["discovered_email_count"] == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id], [], [run_id])


async def test_firms_list_search_discovered_email_via_investor_advisor_link() -> None:
    """An IAPD-overlap investor surfaces when the search needle matches a
    discovered email on its linked advisor -- mirroring the overlap
    advisor_id join used for the count subquery."""
    user_id = await _seed_user()
    name_token = secrets.token_hex(4)
    email_token = secrets.token_hex(4)
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(name=f"Advisor {name_token} Group", status="active")
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)

        # Overlap investor: shares the advisor's discovered emails.
        investor = InstitutionalInvestor(
            name=f"Investor {name_token} Capital", advisor_id=advisor.id
        )
        session.add(investor)
        await session.commit()
        await session.refresh(investor)

        run = ExtractionRun(
            domain=f"{name_token}.com", advisor_id=advisor.id, status="completed"
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        session.add(
            _make_discovered(
                run.id,
                f"cio@{email_token}.com",
                advisor_id=advisor.id,
                source="apollo",
            )
        )
        await session.commit()
        advisor_id, investor_id, run_id = advisor.id, investor.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/firms",
                params={"q": email_token, "entity_kind": "institutional_investor"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        # Only the overlap investor (the advisor itself is filtered out by
        # entity_kind), surfaced purely through the discovered-email match.
        assert any(
            i["entity_kind"] == "institutional_investor"
            and i["entity_id"] == investor_id
            for i in body["items"]
        )
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [advisor_id], [investor_id], [run_id])


async def test_firms_list_search_no_discovered_match_excludes_firm() -> None:
    """A discovered-only firm whose email/name does not contain the needle
    is excluded -- the broadened filter doesn't over-match (the empty-search
    inclusion still requires the needle to land somewhere)."""
    user_id = await _seed_user()
    name_token = secrets.token_hex(4)
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=f"BD {name_token} LLP",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add(bd)
        await session.commit()
        await session.refresh(bd)

        run = ExtractionRun(domain=f"{name_token}.com", bd_id=bd.id, status="completed")
        session.add(run)
        await session.commit()
        await session.refresh(run)

        session.add(_make_discovered(run.id, f"x@{name_token}.com", bd_id=bd.id))
        await session.commit()
        bd_id, run_id = bd.id, run.id

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            # A needle that matches neither the firm name nor any discovered
            # email/name on it.
            response = await client.get(
                "/api/v1/outreach/contacts/firms",
                params={"q": f"nomatch-{secrets.token_hex(4)}"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert all(i["entity_id"] != bd_id for i in body["items"])
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [bd_id], [], [], [run_id])
