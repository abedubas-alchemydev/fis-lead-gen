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

import json
import secrets
from datetime import datetime

import httpx
import pytest
import respx
from sqlalchemy import delete

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.advisor_contact import AdvisorContact
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investment_advisor import InvestmentAdvisor
from app.models.investor_contact import InvestorContact
from app.models.vault_folder import VaultFolder
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

    The token is embedded in each contact's email (or name) so the
    contact-search hits all three entity kinds. Firm-name matches
    don't count for contact-search anymore -- they go through
    /outreach/firms/search instead. Two firm rows ALSO carry the
    token in their firm name so the separate firm-search tests can
    reuse this fixture.
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
            email=f"alice-{needle_token}@example.com",
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
            email=f"cathy-{needle_token}@example.com",
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


# ── /outreach/firms/search + /outreach/firms/contacts ────────────────


async def test_firms_search_returns_rows_from_all_three_kinds() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    bd_ids, advisor_ids, investor_ids, *_ = await _seed_search_fixtures(token)
    # The investor firm in the shared fixture doesn't carry the token in
    # its name -- seed a token-named investor so all three kinds match.
    async with SessionLocal() as session:
        named_investor = InstitutionalInvestor(name=f"Investor {token} Capital")
        session.add(named_investor)
        await session.commit()
        await session.refresh(named_investor)
        investor_ids.append(named_investor.id)
        named_investor_contact = InvestorContact(
            investor_id=named_investor.id,
            name="Edna Cruz",
            title="Director",
            email="edna@example.com",
        )
        session.add(named_investor_contact)
        await session.commit()

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/firms/search", params={"q": token}
            )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        kinds = {item["entity_kind"] for item in items}
        assert "broker_dealer" in kinds
        assert "advisor" in kinds
        assert "institutional_investor" in kinds
        # The "Some unrelated BD" + the bd_no_email row that has the
        # token in a NAME but its single contact lacks an email should
        # not appear (contact_count > 0 filter on email-bearing rows).
        bd_rows = [i for i in items if i["entity_kind"] == "broker_dealer"]
        for row in bd_rows:
            assert row["contact_count"] >= 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], bd_ids, advisor_ids, investor_ids)


async def test_firms_search_excludes_firms_with_no_emailable_contacts() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    bd_ids: list[int] = []
    async with SessionLocal() as session:
        empty_bd = BrokerDealer(
            name=f"Empty BD {token}",
            matched_source="edgar",
            is_deficient=False,
            status="active",
        )
        session.add(empty_bd)
        await session.commit()
        await session.refresh(empty_bd)
        bd_ids.append(empty_bd.id)
        no_email_contact = ExecutiveContact(
            bd_id=empty_bd.id,
            name="Nobody Useful",
            title="N/A",
            email=None,
        )
        session.add(no_email_contact)
        await session.commit()

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/firms/search", params={"q": token}
            )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        # Empty BD (no emailable contact) must not appear.
        assert all(it["entity_id"] != empty_bd.id for it in items)
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], bd_ids, [], [])


async def test_firm_contacts_returns_emailable_contacts() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    bd_ids, advisor_ids, investor_ids, *_ = await _seed_search_fixtures(token)
    bd_id = bd_ids[0]  # the BD whose name carries the token, with Alice
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/firms/contacts",
                params={"entity_kind": "broker_dealer", "entity_id": bd_id},
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["entity_kind"] == "broker_dealer"
        assert payload["entity_id"] == bd_id
        assert len(payload["items"]) == 1
        assert payload["items"][0]["contact_name"] == "Alice Reyes"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], bd_ids, advisor_ids, investor_ids)


async def test_firm_contacts_404_on_unknown_firm() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/firms/contacts",
                params={"entity_kind": "broker_dealer", "entity_id": 999_999_999},
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [], [], [])


# ── /outreach/favorites/search + /outreach/favorites/{id}/firms ──────


async def _seed_favorite_list(
    user_id: str, list_name: str, bd_ids_to_link: list[int]
) -> int:
    """Create a FavoriteList for the user and link the given BDs into it."""
    from app.models.favorite_list import FavoriteList, FavoriteListItem

    async with SessionLocal() as session:
        fav_list = FavoriteList(user_id=user_id, name=list_name)
        session.add(fav_list)
        await session.commit()
        await session.refresh(fav_list)
        for bd_id in bd_ids_to_link:
            session.add(
                FavoriteListItem(list_id=fav_list.id, broker_dealer_id=bd_id)
            )
        await session.commit()
        return fav_list.id


async def _cleanup_favorite(list_id: int) -> None:
    from app.models.favorite_list import FavoriteList

    async with SessionLocal() as session:
        await session.execute(
            delete(FavoriteList).where(FavoriteList.id == list_id)
        )
        await session.commit()


async def test_favorites_search_returns_owned_lists_matching_query() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    bd_ids, advisor_ids, investor_ids, *_ = await _seed_search_fixtures(token)
    list_id = await _seed_favorite_list(
        user_id, f"My {token} Targets", [bd_ids[0]]
    )
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/favorites/search", params={"q": token}
            )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert any(item["list_id"] == list_id for item in items)
        match = next(item for item in items if item["list_id"] == list_id)
        # Only the bd_ids[0] (Alice with email) counts; bd_ids[1]
        # (no-email contact) wouldn't even if it were in the list.
        assert match["firm_count"] == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup_favorite(list_id)
        await _cleanup([user_id], bd_ids, advisor_ids, investor_ids)


async def test_favorites_search_excludes_empty_lists() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    # List name matches but it has zero firms with emailable contacts.
    list_id = await _seed_favorite_list(user_id, f"Empty {token} List", [])
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/outreach/favorites/search", params={"q": token}
            )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert all(item["list_id"] != list_id for item in items)
    finally:
        app.dependency_overrides.clear()
        await _cleanup_favorite(list_id)
        await _cleanup([user_id], [], [], [])


async def test_favorite_firms_returns_emailable_contact_firms() -> None:
    user_id = await _seed_user()
    token = secrets.token_hex(4)
    bd_ids, advisor_ids, investor_ids, *_ = await _seed_search_fixtures(token)
    # Link both BDs (one with emailable contact, one without).
    list_id = await _seed_favorite_list(
        user_id, f"Mixed {token} List", bd_ids
    )
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/outreach/favorites/{list_id}/firms"
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["list_id"] == list_id
        # Only the BD with an emailable contact should appear.
        assert len(payload["items"]) == 1
        assert payload["items"][0]["entity_kind"] == "broker_dealer"
        assert payload["items"][0]["entity_id"] == bd_ids[0]
    finally:
        app.dependency_overrides.clear()
        await _cleanup_favorite(list_id)
        await _cleanup([user_id], bd_ids, advisor_ids, investor_ids)


async def test_favorite_firms_404_on_other_users_list() -> None:
    owner_id = await _seed_user()
    other_id = await _seed_user()
    list_id = await _seed_favorite_list(owner_id, "Owner's List", [])
    app.dependency_overrides[get_current_user] = lambda: _override_user(other_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/outreach/favorites/{list_id}/firms"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup_favorite(list_id)
        await _cleanup([owner_id, other_id], [], [], [])


# ── POST /outreach/adhoc-draft ───────────────────────────────────────────

_VALID_GEMINI_KEY = "AIzaSy" + "a" * 33
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash:generateContent"
)


async def _seed_vault_folder(user_id: str, name: str, description: str) -> int:
    async with SessionLocal() as session:
        folder = VaultFolder(user_id=user_id, name=name, description=description)
        session.add(folder)
        await session.commit()
        await session.refresh(folder)
        return folder.id


async def _cleanup_folder(folder_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(
            delete(VaultFolder).where(VaultFolder.id == folder_id)
        )
        await session.commit()


def _stub_gemini_response(*, subject: str, body: str) -> httpx.Response:
    structured = json.dumps({"subject": subject, "body": body})
    return httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": structured}]}}]},
    )


@pytest.fixture
def _patch_outreach_retrieve_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub RAG so the test doesn't depend on pgvector + OpenAI embeddings.

    The endpoint already tolerates retrieve_chunks failures, but mocking
    keeps the test deterministic instead of relying on the swallow-error
    path.
    """

    async def _empty_retrieve(*, folder_id: int, query: str, db) -> list:  # noqa: ANN001
        return []

    monkeypatch.setattr(
        "app.api.v1.endpoints.outreach.retrieve_chunks", _empty_retrieve
    )


@respx.mock
async def test_adhoc_draft_returns_subject_and_body(
    monkeypatch: pytest.MonkeyPatch, _patch_outreach_retrieve_chunks
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", _VALID_GEMINI_KEY)
    monkeypatch.setattr(
        settings,
        "gemini_api_base",
        "https://generativelanguage.googleapis.com/v1beta",
    )
    respx.post(_GEMINI_URL).mock(
        return_value=_stub_gemini_response(
            subject="Quick intro on stock-loan rebates",
            body="Hi Sarah,\n\nValue para.\n\n- Sender",
        )
    )

    user_id = await _seed_user()
    folder_id = await _seed_vault_folder(
        user_id,
        "Stock Loan",
        "We offer competitive rebates on hard-to-borrow names.",
    )
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/adhoc-draft",
                json={
                    "folder_id": folder_id,
                    "recipient_email": "sarah@example.com",
                    "recipient_name": "Sarah",
                },
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["subject"] == "Quick intro on stock-loan rebates"
        assert payload["body"].startswith("Hi Sarah")
    finally:
        app.dependency_overrides.clear()
        await _cleanup_folder(folder_id)
        await _cleanup([user_id], [], [], [])


async def test_adhoc_draft_404_on_folder_owned_by_other_user(
    _patch_outreach_retrieve_chunks,
) -> None:
    owner_id = await _seed_user()
    caller_id = await _seed_user()
    folder_id = await _seed_vault_folder(owner_id, "Owner Service", "Owned by owner.")
    app.dependency_overrides[get_current_user] = lambda: _override_user(caller_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/adhoc-draft",
                json={
                    "folder_id": folder_id,
                    "recipient_email": "sarah@example.com",
                },
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "outreach_inputs_not_found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup_folder(folder_id)
        await _cleanup([owner_id, caller_id], [], [], [])


async def test_adhoc_draft_503_when_gemini_key_missing(
    monkeypatch: pytest.MonkeyPatch, _patch_outreach_retrieve_chunks
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", None)

    user_id = await _seed_user()
    folder_id = await _seed_vault_folder(user_id, "Stock Loan", "Service desc.")
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/adhoc-draft",
                json={
                    "folder_id": folder_id,
                    "recipient_email": "sarah@example.com",
                },
            )
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()
        await _cleanup_folder(folder_id)
        await _cleanup([user_id], [], [], [])


async def test_adhoc_draft_422_on_invalid_recipient_email() -> None:
    user_id = await _seed_user()
    folder_id = await _seed_vault_folder(user_id, "Stock Loan", "Service desc.")
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/adhoc-draft",
                json={
                    "folder_id": folder_id,
                    "recipient_email": "not-an-email",
                },
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await _cleanup_folder(folder_id)
        await _cleanup([user_id], [], [], [])
