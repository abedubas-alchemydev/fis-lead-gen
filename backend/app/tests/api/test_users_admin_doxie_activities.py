"""API-layer tests for the chatbot (Doxie) branch of /users/{id}/activities.

Integration-marked: writes real ``chatbot_conversation`` +
``chatbot_message`` rows and reads them back through the UNION endpoint.
Auth is mocked via ``app.dependency_overrides`` (same pattern as
``test_users_admin_activities.py`` / ``test_users_admin_saved_firms.py``).

Asserts that:

* one feed row appears per *user* message (assistant turns excluded);
* the row carries the page-context path in ``target_name`` and the
  message preview in ``details``;
* ``?type=doxie`` prunes the feed to only Doxie rows, and a different
  filter (``?type=save``) excludes them.

The non-integration default suite covers the ``_row_details`` mapping for
the "doxie" event_type directly in
``test_users_admin_doxie_row_details.py`` so this DB-backed module can be
deselected without losing branch coverage.
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
from app.models.chatbot_conversation import ChatbotConversation
from app.models.chatbot_message import ChatbotMessage
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str, role: str = "admin") -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Admin Caller",
        email=f"{user_id}@example.com",
        role=role,
        session_expires_at=datetime(2099, 1, 1),
    )


async def _seed_user(role: str = "viewer", suffix: str = "") -> str:
    user_id = f"test-user-{secrets.token_hex(6)}{suffix}"
    async with SessionLocal() as session:
        session.add(
            AuthUser(
                id=user_id,
                name=f"Test {suffix or 'User'}",
                email=f"{user_id}@example.com",
                email_verified=False,
                role=role,
                status="active",
            )
        )
        await session.commit()
    return user_id


async def _seed_conversation(user_id: str, archived: bool = False) -> int:
    async with SessionLocal() as session:
        convo = ChatbotConversation(
            user_id=user_id,
            archived_at=datetime(2025, 1, 1) if archived else None,
        )
        session.add(convo)
        await session.commit()
        await session.refresh(convo)
        return convo.id


async def _seed_message(
    conversation_id: int,
    role: str,
    content: str,
    path: str | None = None,
    title: str | None = None,
) -> None:
    async with SessionLocal() as session:
        session.add(
            ChatbotMessage(
                conversation_id=conversation_id,
                role=role,
                content=content,
                page_context_path=path,
                page_context_title=title,
            )
        )
        await session.commit()


async def _cleanup(user_ids: list[str], conversation_ids: list[int]) -> None:
    async with SessionLocal() as session:
        if conversation_ids:
            await session.execute(
                delete(ChatbotMessage).where(
                    ChatbotMessage.conversation_id.in_(conversation_ids)
                )
            )
            await session.execute(
                delete(ChatbotConversation).where(
                    ChatbotConversation.id.in_(conversation_ids)
                )
            )
        if user_ids:
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_activities_includes_doxie_user_messages() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    target_id = await _seed_user(suffix="-target")
    convo_id = await _seed_conversation(target_id)

    await _seed_message(
        convo_id,
        "user",
        "Which firms cleared through Pershing last year?",
        path="/master-list",
        title="Broker Dealers",
    )
    # Assistant turn must NOT surface as its own feed row.
    await _seed_message(convo_id, "assistant", "Here are the firms…")

    app.dependency_overrides[get_current_user] = lambda: _override_user(
        admin_id, role="admin"
    )
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/users/{target_id}/activities")
        assert response.status_code == 200
        body = response.json()

        doxie_rows = [r for r in body["items"] if r["event_type"] == "doxie"]
        # Exactly one — the user message, not the assistant reply.
        assert len(doxie_rows) == 1
        row = doxie_rows[0]
        assert row["target_type"] is None
        # page_context_path rides in target_name.
        assert row["target_name"] == "/master-list"
        # details carries path + title + message preview.
        assert row["details"]["path"] == "/master-list"
        assert row["details"]["title"] == "Broker Dealers"
        assert (
            row["details"]["preview"]
            == "Which firms cleared through Pershing last year?"
        )
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id, target_id], [convo_id])


async def test_activities_doxie_includes_archived_conversations() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    target_id = await _seed_user(suffix="-target")
    archived_convo = await _seed_conversation(target_id, archived=True)

    await _seed_message(
        archived_convo,
        "user",
        "Old question from a prior chat",
        path="/dashboard",
    )

    app.dependency_overrides[get_current_user] = lambda: _override_user(
        admin_id, role="admin"
    )
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/users/{target_id}/activities", params={"type": "doxie"}
            )
        assert response.status_code == 200
        body = response.json()
        # Archived conversations still surface (archived only means "not the
        # active chat") — history must be visible to the admin.
        assert body["total"] == 1
        assert body["items"][0]["details"]["preview"] == (
            "Old question from a prior chat"
        )
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id, target_id], [archived_convo])


async def test_activities_type_doxie_filters_to_doxie_only() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    target_id = await _seed_user(suffix="-target")
    convo_id = await _seed_conversation(target_id)

    await _seed_message(convo_id, "user", "Question one", path="/master-list")
    await _seed_message(convo_id, "user", "Question two", path="/advisor-list")
    await _seed_message(convo_id, "assistant", "Some answer")

    app.dependency_overrides[get_current_user] = lambda: _override_user(
        admin_id, role="admin"
    )
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/users/{target_id}/activities", params={"type": "doxie"}
            )
        assert response.status_code == 200
        body = response.json()
        actions = {row["event_type"] for row in body["items"]}
        assert actions == {"doxie"}
        # Two user messages, assistant excluded.
        assert body["total"] == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id, target_id], [convo_id])


async def test_activities_type_save_excludes_doxie_rows() -> None:
    admin_id = await _seed_user(role="admin", suffix="-admin")
    target_id = await _seed_user(suffix="-target")
    convo_id = await _seed_conversation(target_id)

    await _seed_message(convo_id, "user", "A Doxie question", path="/master-list")

    app.dependency_overrides[get_current_user] = lambda: _override_user(
        admin_id, role="admin"
    )
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/users/{target_id}/activities", params={"type": "save"}
            )
        assert response.status_code == 200
        body = response.json()
        actions = {row["event_type"] for row in body["items"]}
        assert "doxie" not in actions
    finally:
        app.dependency_overrides.clear()
        await _cleanup([admin_id, target_id], [convo_id])
