"""API-layer tests for DELETE /outreach/sends/{send_id}.

Integration-marked -- touches a real Postgres. Pins the two behaviors
the soft-delete contract turns on:

* Soft delete hides the row from both read paths (list + detail) while
  leaving it on disk (``deleted_at`` stamped, row not dropped).
* Delete is owner-only: a user cannot delete another user's send, and a
  second delete of an already-deleted id is a 404 -- the same opaque
  response as GET so a leaked id can't confirm cross-user existence.

Mirrors the seed/override/cleanup style of ``test_outreach_signature.py``
and ``test_outreach_create_tab.py``. ``_cleanup`` deletes the AuthUser,
which cascades to ``outreach_sends`` via the user_id FK.
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
from app.models.outreach_send import OutreachSend
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


async def _seed_send(user_id: str) -> int:
    """Insert one adhoc-style send (no firm/contact FKs) for ``user_id``."""
    async with SessionLocal() as session:
        row = OutreachSend(
            user_id=user_id,
            subject="Test subject",
            body="Test body",
            provider="google",
            status="sent",
            recipient_email="recipient@example.com",
            recipient_name="Recipient",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def _cleanup(*user_ids: str) -> None:
    async with SessionLocal() as session:
        await session.execute(
            delete(AuthUser).where(AuthUser.id.in_(user_ids))
        )
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_delete_hides_from_list_and_detail() -> None:
    user_id = await _seed_user()
    send_id = await _seed_send(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            before = await client.get("/api/v1/outreach/sends")
            assert before.status_code == 200, before.text
            assert before.json()["total"] == 1

            deleted = await client.delete(
                f"/api/v1/outreach/sends/{send_id}"
            )
            assert deleted.status_code == 204, deleted.text

            after = await client.get("/api/v1/outreach/sends")
            assert after.json()["total"] == 0

            detail = await client.get(
                f"/api/v1/outreach/sends/{send_id}"
            )
            assert detail.status_code == 404

        # Row is tombstoned, not dropped: still on disk with deleted_at set.
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(OutreachSend).where(OutreachSend.id == send_id)
                )
            ).scalar_one()
            assert row.deleted_at is not None
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_id)


async def test_delete_is_owner_only() -> None:
    owner_id = await _seed_user()
    other_id = await _seed_user()
    send_id = await _seed_send(owner_id)
    # Act as the OTHER user -- not the sender.
    app.dependency_overrides[get_current_user] = lambda: _override_user(other_id)
    try:
        async with _client() as client:
            denied = await client.delete(
                f"/api/v1/outreach/sends/{send_id}"
            )
            assert denied.status_code == 404
    finally:
        app.dependency_overrides.clear()

    # The owner still sees the row -- the cross-user delete was a no-op.
    app.dependency_overrides[get_current_user] = lambda: _override_user(owner_id)
    try:
        async with _client() as client:
            mine = await client.get("/api/v1/outreach/sends")
            assert mine.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup(owner_id, other_id)


async def test_delete_already_deleted_returns_404() -> None:
    user_id = await _seed_user()
    send_id = await _seed_send(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            first = await client.delete(
                f"/api/v1/outreach/sends/{send_id}"
            )
            assert first.status_code == 204, first.text

            second = await client.delete(
                f"/api/v1/outreach/sends/{send_id}"
            )
            assert second.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_id)
