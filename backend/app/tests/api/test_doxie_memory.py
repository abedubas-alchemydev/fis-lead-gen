"""API-layer tests for /doxie/memories (Doxie's private per-user memory).

Integration-marked -- the endpoints touch a real Postgres. Auth is mocked via
``app.dependency_overrides`` so we don't hand-craft a BetterAuth session
cookie; the 401-without-cookie case runs the real ``get_current_user`` to
prove it rejects pre-DB.

The load-bearing guarantees here are privacy ones: a GET returns ONLY the
caller's rows, and a DELETE of a memory the caller doesn't own returns
**404, not 403** (so a memory id can't be probed for existence), including
the cross-user negative (user A cannot see or delete user B's memories).
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
from app.models.chatbot_user_memory import ChatbotUserMemory
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Test User",
        email=f"{user_id}@example.com",
        role="viewer",
        feature_permissions=[],
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


async def _seed_memory(user_id: str, content: str) -> int:
    async with SessionLocal() as session:
        memory = ChatbotUserMemory(user_id=user_id, content=content)
        session.add(memory)
        await session.commit()
        await session.refresh(memory)
        return memory.id


async def _cleanup(user_ids: list[str]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            # CASCADE on user_id drops the memories, but delete explicitly so
            # the test is robust even if FKs are ever relaxed.
            await session.execute(
                delete(ChatbotUserMemory).where(
                    ChatbotUserMemory.user_id.in_(user_ids)
                )
            )
            await session.execute(
                delete(AuthUser).where(AuthUser.id.in_(user_ids))
            )
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_get_memories_401_without_session_cookie() -> None:
    # No dependency override -> real get_current_user runs and rejects.
    async with _client() as client:
        response = await client.get("/api/v1/doxie/memories")
    assert response.status_code == 401


async def test_get_returns_only_caller_rows() -> None:
    user_id = await _seed_user()
    await _seed_memory(user_id, "Focuses on self-clearing firms in TX")
    await _seed_memory(user_id, "Standard fee is 25bps")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get("/api/v1/doxie/memories")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        contents = {item["content"] for item in body["items"]}
        assert contents == {
            "Focuses on self-clearing firms in TX",
            "Standard fee is 25bps",
        }
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id])


async def test_delete_own_memory_succeeds() -> None:
    user_id = await _seed_user()
    memory_id = await _seed_memory(user_id, "Prefers formal outreach")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete(
                f"/api/v1/doxie/memories/{memory_id}"
            )
        assert response.status_code == 204

        async with _client() as client:
            after = await client.get("/api/v1/doxie/memories")
        assert after.json()["total"] == 0
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id])


async def test_delete_unknown_id_returns_404() -> None:
    user_id = await _seed_user()

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete("/api/v1/doxie/memories/999999999")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id])


async def test_memories_are_user_scoped_and_not_cross_deletable() -> None:
    """Cross-user isolation: user A cannot see or delete user B's memory.

    The DELETE returns 404 (not 403) on the other user's id so the endpoint
    never confirms that someone else's memory exists.
    """
    user_a = await _seed_user()
    user_b = await _seed_user()
    b_memory_id = await _seed_memory(user_b, "B's private fact")

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_a)
    try:
        # A sees none of B's memories.
        async with _client() as client:
            a_list = await client.get("/api/v1/doxie/memories")
        assert a_list.status_code == 200
        assert a_list.json() == {"items": [], "total": 0}

        # A cannot delete B's memory -> 404, not 403.
        async with _client() as client:
            a_delete = await client.delete(
                f"/api/v1/doxie/memories/{b_memory_id}"
            )
        assert a_delete.status_code == 404

        # B's memory is untouched.
        app.dependency_overrides[get_current_user] = lambda: _override_user(user_b)
        async with _client() as client:
            b_list = await client.get("/api/v1/doxie/memories")
        assert b_list.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_a, user_b])
