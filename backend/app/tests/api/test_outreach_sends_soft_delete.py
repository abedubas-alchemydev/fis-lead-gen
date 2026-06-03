"""API-layer tests for soft-deleting outreach sends.

Integration-marked: writes real rows into ``outreach_sends`` and exercises
``GET/DELETE /api/v1/outreach/sends`` through the ASGI app. Auth is mocked via
``app.dependency_overrides`` (same pattern as ``test_users_admin_activities.py``).

Seeded rows are ad-hoc (all firm/contact FKs NULL) so they only need
``recipient_email`` to satisfy ``ck_outreach_sends_adhoc_has_recipient`` — no
BrokerDealer/contact fixtures required. The caller is seeded as ``admin`` purely
to bypass the ``SENT_OUTREACH`` feature gate (``ensure_feature`` early-returns
for admins); the delete itself is owner-only and enforced by a
``user_id == current_user.id`` filter, NOT by role — which is exactly what
``test_delete_other_users_send_404`` pins down.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

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


def _override_user(user_id: str, role: str = "admin") -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Caller",
        email=f"{user_id}@example.com",
        role=role,
        session_expires_at=datetime(2099, 1, 1),
    )


async def _seed_user(role: str = "admin", suffix: str = "") -> str:
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


async def _seed_send(
    user_id: str, *, status: str = "sent", archived: bool = False
) -> int:
    async with SessionLocal() as session:
        send = OutreachSend(
            user_id=user_id,
            subject="Soft-delete test subject",
            body="Soft-delete test body.",
            provider="google",
            status=status,
            recipient_email="recipient@example.com",
            recipient_name="Test Recipient",
            archived_at=datetime.now(timezone.utc) if archived else None,
        )
        session.add(send)
        await session.flush()  # assigns the autoincrement PK
        send_id = send.id
        await session.commit()
    return send_id


async def _get_send(send_id: int) -> OutreachSend | None:
    async with SessionLocal() as session:
        return (
            await session.execute(
                select(OutreachSend).where(OutreachSend.id == send_id)
            )
        ).scalar_one_or_none()


async def _cleanup(user_ids: list[str]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(
                delete(OutreachSend).where(OutreachSend.user_id.in_(user_ids))
            )
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_archived_send_excluded_from_list_and_total() -> None:
    user_id = await _seed_user(suffix="-owner")
    live_1 = await _seed_send(user_id)
    live_2 = await _seed_send(user_id)
    archived = await _seed_send(user_id, archived=True)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get("/api/v1/outreach/sends")
        assert response.status_code == 200
        body = response.json()
        ids = {row["id"] for row in body["items"]}
        assert ids == {live_1, live_2}
        assert archived not in ids
        # total comes from a count subquery over the same filtered select,
        # so the archived row must not inflate it either.
        assert body["total"] == 2
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id])


async def test_detail_404_for_archived_send() -> None:
    user_id = await _seed_user(suffix="-owner")
    archived = await _seed_send(user_id, archived=True)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(f"/api/v1/outreach/sends/{archived}")
        assert response.status_code == 404
        assert response.json()["detail"] == "outreach_send_not_found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id])


async def test_delete_soft_archives_and_hides() -> None:
    user_id = await _seed_user(suffix="-owner")
    send_id = await _seed_send(user_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            delete_resp = await client.delete(f"/api/v1/outreach/sends/{send_id}")
            assert delete_resp.status_code == 204
            list_resp = await client.get("/api/v1/outreach/sends")
            detail_resp = await client.get(f"/api/v1/outreach/sends/{send_id}")

        assert send_id not in {row["id"] for row in list_resp.json()["items"]}
        assert detail_resp.status_code == 404

        # Soft, not hard: the row is still in the DB with archived_at set.
        row = await _get_send(send_id)
        assert row is not None
        assert row.archived_at is not None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id])


async def test_delete_other_users_send_404() -> None:
    caller_id = await _seed_user(suffix="-caller")
    other_id = await _seed_user(suffix="-other")
    send_id = await _seed_send(other_id)

    # Caller is an admin, but delete is owner-only: another user's row is a 404.
    app.dependency_overrides[get_current_user] = lambda: _override_user(caller_id)
    try:
        async with _client() as client:
            response = await client.delete(f"/api/v1/outreach/sends/{send_id}")
        assert response.status_code == 404

        row = await _get_send(send_id)
        assert row is not None
        assert row.archived_at is None  # untouched
    finally:
        app.dependency_overrides.clear()
        await _cleanup([caller_id, other_id])


async def test_delete_is_idempotent() -> None:
    user_id = await _seed_user(suffix="-owner")
    send_id = await _seed_send(user_id)

    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            first = await client.delete(f"/api/v1/outreach/sends/{send_id}")
        assert first.status_code == 204
        first_row = await _get_send(send_id)
        assert first_row is not None
        stamp = first_row.archived_at
        assert stamp is not None

        async with _client() as client:
            second = await client.delete(f"/api/v1/outreach/sends/{send_id}")
        assert second.status_code == 204

        # The guard skips re-stamping, so archived_at is unchanged.
        second_row = await _get_send(send_id)
        assert second_row is not None
        assert second_row.archived_at == stamp
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id])
