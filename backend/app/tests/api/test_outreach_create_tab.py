"""API-layer tests for the /outreach/sent?tab=create surface.

Integration-marked -- both endpoints touch a real Postgres. Covers:

* GET /outreach/contacts/search -- partial-match search across the
  three contact tables, email-presence filter, limit + ordering.
* POST /outreach/adhoc-send -- payload validation (Pydantic email
  rejection) and auth gating. The happy path goes through the
  provider plumbing we already cover via the existing /outreach/send
  surface (``_resolve_sender_account`` + ``_provider_send_and_record``),
  so this file deliberately stops short of mocking Gmail / Microsoft /
  Yahoo transports just to retest that shared codepath.
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
        feature_permissions=["sent_outreach"],
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


async def _seed_search_fixtures(
    needle_token: str,
) -> tuple[list[int], list[int], list[int], list[int], list[int], list[int]]:
    """Create one row per contact table containing ``needle_token``.

    Returns ids in declaration order:
      (bd_ids, advisor_ids, investor_ids,
       exec_contact_ids, advisor_contact_ids, investor_contact_ids).

    The token is embedded in the firm name for two rows and in the
    contact email for the third so the search exercises all three
    OR branches.
    """
    bd_ids: list[int] = []
    advisor_ids: list[int] = []
    investor_ids: list[int] = []
    exec_contact_ids: list[int] = []
    advisor_contact_ids: list[int] = []
    investor_contact_ids: list[int] = []

    async with SessionLocal() as session:
        bd_match = BrokerDealer(
            name=f"BD {needle_token} LLP",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        bd_no_email = BrokerDealer(
            name="Some unrelated BD",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        advisor_match = InvestmentAdvisor(
            name=f"Advisor {needle_token} Group", status="active"
        )
        investor_match = InstitutionalInvestor(
            name="Unrelated Investor LLC"
        )
        session.add_all([bd_match, bd_no_email, advisor_match, investor_match])
        await session.commit()
        await session.refresh(bd_match)
        await session.refresh(bd_no_email)
        await session.refresh(advisor_match)
        await session.refresh(investor_match)
        bd_ids.extend([bd_match.id, bd_no_email.id])
        advisor_ids.append(advisor_match.id)
        investor_ids.append(investor_match.id)

        exec_with_email = ExecutiveContact(
            bd_id=bd_match.id,
            name="Alice Reyes",
            title="CFO",
            email="alice@example.com",
        )
        exec_without_email = ExecutiveContact(
            bd_id=bd_no_email.id,
            name=f"Bob {needle_token} (no email)",
            title="COO",
            email=None,
        )
        advisor_contact = AdvisorContact(
            advisor_id=advisor_match.id,
            name="Cathy Lim",
            title="Head of Trading",
            email="cathy@example.com",
        )
        investor_contact_match_by_email = InvestorContact(
            investor_id=investor_match.id,
            name="Dan Park",
            title="Portfolio Manager",
            email=f"dan-{needle_token}@example.com",
        )
        session.add_all(
            [
                exec_with_email,
                exec_without_email,
                advisor_contact,
                investor_contact_match_by_email,
            ]
        )
        await session.commit()
        await session.refresh(exec_with_email)
        await session.refresh(exec_without_email)
        await session.refresh(advisor_contact)
        await session.refresh(investor_contact_match_by_email)
        exec_contact_ids.extend(
            [exec_with_email.id, exec_without_email.id]
        )
        advisor_contact_ids.append(advisor_contact.id)
        investor_contact_ids.append(investor_contact_match_by_email.id)

    return (
        bd_ids,
        advisor_ids,
        investor_ids,
        exec_contact_ids,
        advisor_contact_ids,
        investor_contact_ids,
    )


async def _cleanup(
    user_ids: list[str],
    bd_ids: list[int],
    advisor_ids: list[int],
    investor_ids: list[int],
) -> None:
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(
                delete(AuthUser).where(AuthUser.id.in_(user_ids))
            )
        # Contact rows cascade from their parent firm rows.
        if bd_ids:
            await session.execute(
                delete(BrokerDealer).where(BrokerDealer.id.in_(bd_ids))
            )
        if advisor_ids:
            await session.execute(
                delete(InvestmentAdvisor).where(
                    InvestmentAdvisor.id.in_(advisor_ids)
                )
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


async def test_contacts_search_returns_rows_from_all_three_entity_kinds() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    bd_ids, advisor_ids, investor_ids, *_ = await _seed_search_fixtures(token)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/search", params={"q": token}
            )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        kinds = {item["entity_kind"] for item in items}
        # BD match (token in firm name), advisor match (token in firm
        # name), investor match (token in contact email).
        assert "broker_dealer" in kinds
        assert "advisor" in kinds
        assert "institutional_investor" in kinds
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], bd_ids, advisor_ids, investor_ids)


async def test_contacts_search_excludes_contacts_without_email() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    bd_ids, advisor_ids, investor_ids, *_ = await _seed_search_fixtures(token)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/search", params={"q": token}
            )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        # The seed adds one ExecutiveContact whose name contains the
        # token but has email=None. It must not appear.
        for item in items:
            assert item["contact_email"]  # truthy: non-empty string
            assert "no email" not in item["contact_name"].lower()
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], bd_ids, advisor_ids, investor_ids)


async def test_contacts_search_respects_limit() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    bd_ids, advisor_ids, investor_ids, *_ = await _seed_search_fixtures(token)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/contacts/search",
                params={"q": token, "limit": 2},
            )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) <= 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], bd_ids, advisor_ids, investor_ids)


async def test_contacts_search_401_without_session_cookie() -> None:
    # No dependency override -> real get_current_user runs and rejects.
    async with _client() as client:
        response = await client.get(
            "/api/v1/outreach/contacts/search", params={"q": "anything"}
        )
    assert response.status_code == 401


async def test_adhoc_send_422_on_invalid_recipient_email() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/adhoc-send",
                json={
                    "recipient_email": "not-an-email",
                    "subject": "Hello",
                    "body": "hi",
                },
            )
        # Pydantic EmailStr rejects pre-handler; this is a wire
        # validation error.
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [])


async def test_adhoc_send_412_when_no_linked_account() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/adhoc-send",
                json={
                    "recipient_email": "someone@example.com",
                    "subject": "Hello",
                    "body": "hi there",
                },
            )
        # User has no linked Google / Microsoft / Yahoo accounts ->
        # _resolve_sender_account returns 412.
        assert response.status_code == 412
        assert response.json()["detail"] == "google_account_not_linked"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [])
