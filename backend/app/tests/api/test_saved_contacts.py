"""API-layer tests for /saved-contacts (Save Contact feature).

Integration-marked -- touches a real Postgres so the FK + UNIQUE constraints
and the ON CONFLICT idempotency path actually exercise. Auth is mocked via
``app.dependency_overrides`` (same pattern as ``test_favorite_lists.py``); the
401 cases run the real ``get_current_user`` to prove it rejects pre-DB.
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
from app.models.discovered_email import DiscoveredEmail
from app.models.extraction_run import ExtractionRun
from app.models.saved_contact import SavedContact
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str) -> AuthenticatedUser:
    """An entitled viewer -- has the ``email_extractor`` feature the whole
    saved-contacts surface is gated behind."""
    return AuthenticatedUser(
        id=user_id,
        name="Test User",
        email=f"{user_id}@example.com",
        role="viewer",
        feature_permissions=["email_extractor"],
        session_expires_at=datetime(2099, 1, 1),
    )


def _override_admin(user_id: str) -> AuthenticatedUser:
    """An admin with NO explicit feature grants -- proves the gate's
    admin-bypass in ``ensure_feature``."""
    return AuthenticatedUser(
        id=user_id,
        name="Admin User",
        email=f"{user_id}@example.com",
        role="admin",
        session_expires_at=datetime(2099, 1, 1),
    )


def _override_ungated(user_id: str) -> AuthenticatedUser:
    """A viewer WITHOUT ``email_extractor`` -- has an unrelated feature only,
    so the gate must reject it with 403."""
    return AuthenticatedUser(
        id=user_id,
        name="Ungated Viewer",
        email=f"{user_id}@example.com",
        role="viewer",
        feature_permissions=["master_list"],
        session_expires_at=datetime(2099, 1, 1),
    )


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


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


async def _seed_discovered_email(
    *,
    email: str = "jane.doe@acme.com",
    name: str | None = "Jane Doe",
    title: str | None = "Head of Trading",
    company: str | None = "Acme Securities",
    phone: str | None = "+1-212-555-0100",
    linkedin_url: str | None = "https://www.linkedin.com/in/janedoe",
) -> tuple[int, int]:
    """Seed an extraction_run + one discovered_email. Returns (run_id, email_id)."""
    async with SessionLocal() as session:
        run = ExtractionRun(domain="acme.com", status="completed")
        session.add(run)
        await session.commit()
        await session.refresh(run)

        discovered = DiscoveredEmail(
            run_id=run.id,
            email=email,
            domain="acme.com",
            source="hunter",
            enriched_name=name,
            enriched_title=title,
            enriched_company=company,
            enriched_phone=phone,
            enriched_linkedin_url=linkedin_url,
        )
        session.add(discovered)
        await session.commit()
        await session.refresh(discovered)
        return run.id, discovered.id


async def _cleanup(user_ids: list[str], run_ids: list[int]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            # No FK from saved_contact -> discovered_email (by design), so
            # saved rows must be deleted explicitly; user CASCADE also covers
            # them, but be explicit for isolation.
            await session.execute(
                delete(SavedContact).where(SavedContact.user_id.in_(user_ids))
            )
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        if run_ids:
            # ExtractionRun CASCADE -> discovered_email rows.
            await session.execute(
                delete(ExtractionRun).where(ExtractionRun.id.in_(run_ids))
            )
        await session.commit()


# ── Auth gate ──────────────────────────────────────────────────────────────


async def test_list_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/saved-contacts")
    assert response.status_code == 401


async def test_create_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/saved-contacts",
            json={"source": "discovered_email", "contact_id": 1},
        )
    assert response.status_code == 401


async def test_delete_401_without_session_cookie() -> None:
    async with _client() as client:
        response = await client.delete("/api/v1/saved-contacts/1")
    assert response.status_code == 401


# ── Feature gate (email_extractor) ───────────────────────────────────────────


async def test_gate_403_without_email_extractor_feature() -> None:
    """A viewer lacking the ``email_extractor`` permission is rejected across
    the whole surface. The gate is a dependency that runs before any handler,
    so no row PII ever reaches the response body."""
    user_id = f"test-viewer-{secrets.token_hex(6)}"
    app.dependency_overrides[get_current_user] = lambda: _override_ungated(user_id)
    try:
        async with _client() as client:
            get_resp = await client.get("/api/v1/saved-contacts")
            post_resp = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": 1},
            )
            delete_resp = await client.delete("/api/v1/saved-contacts/1")
        assert get_resp.status_code == 403
        assert post_resp.status_code == 403
        assert delete_resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


async def test_admin_without_feature_permission_can_save_and_list() -> None:
    """An admin bypasses the gate (``ensure_feature`` short-circuits on role)
    and can save + list normally, even with no explicit feature grants."""
    user_id = await _seed_user()
    run_id, email_id = await _seed_discovered_email()
    app.dependency_overrides[get_current_user] = lambda: _override_admin(user_id)
    try:
        async with _client() as client:
            saved = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_id},
            )
            listing = await client.get("/api/v1/saved-contacts")
        assert saved.status_code == 200
        assert saved.json()["contact_id"] == email_id
        assert listing.status_code == 200
        assert [row["contact_id"] for row in listing.json()] == [email_id]
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [run_id])


async def test_entitled_viewer_can_save() -> None:
    """A viewer WITH the ``email_extractor`` permission passes the gate -- the
    positive counterpart to the 403 case above."""
    user_id = await _seed_user()
    run_id, email_id = await _seed_discovered_email()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            saved = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_id},
            )
        assert saved.status_code == 200
        assert saved.json()["contact_id"] == email_id
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [run_id])


# ── POST (save) ──────────────────────────────────────────────────────────────


async def test_save_snapshots_discovered_email_fields() -> None:
    user_id = await _seed_user()
    run_id, email_id = await _seed_discovered_email()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_id},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "discovered_email"
        assert body["contact_id"] == email_id
        assert body["name"] == "Jane Doe"
        assert body["title"] == "Head of Trading"
        assert body["email"] == "jane.doe@acme.com"
        assert body["company"] == "Acme Securities"
        assert body["phone"] == "+1-212-555-0100"
        assert body["linkedin_url"] == "https://www.linkedin.com/in/janedoe"
        assert isinstance(body["id"], int)
        assert body["created_at"]  # ISO string present
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [run_id])


async def test_save_is_idempotent() -> None:
    user_id = await _seed_user()
    run_id, email_id = await _seed_discovered_email()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            first = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_id},
            )
            second = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_id},
            )
        assert first.status_code == 200
        assert second.status_code == 200
        # Same row returned, not a duplicate.
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["created_at"] == first.json()["created_at"]

        async with SessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(SavedContact).where(
                            SavedContact.user_id == user_id,
                            SavedContact.contact_id == email_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [run_id])


async def test_save_404_for_unknown_contact() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": 99999999},
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "Contact not found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_save_400_for_unknown_source() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "bogus_source", "contact_id": 1},
            )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_saved_contact_survives_scan_deletion() -> None:
    """The whole point: deleting the source scan must not vanish the save."""
    user_id = await _seed_user()
    run_id, email_id = await _seed_discovered_email()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            saved = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_id},
            )
        assert saved.status_code == 200

        # Cascade-delete the originating run (and its discovered_email).
        async with SessionLocal() as session:
            await session.execute(
                delete(ExtractionRun).where(ExtractionRun.id == run_id)
            )
            await session.commit()
            gone = (
                await session.execute(
                    select(DiscoveredEmail).where(DiscoveredEmail.id == email_id)
                )
            ).scalar_one_or_none()
            assert gone is None  # source row really is gone

        async with _client() as client:
            listing = await client.get("/api/v1/saved-contacts")
        assert listing.status_code == 200
        body = listing.json()
        assert len(body) == 1
        assert body[0]["contact_id"] == email_id
        assert body[0]["name"] == "Jane Doe"  # snapshot intact
        assert body[0]["email"] == "jane.doe@acme.com"
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [run_id])


# ── GET (list) ───────────────────────────────────────────────────────────────


async def test_list_returns_newest_first_and_is_user_scoped() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    run_a, email_a = await _seed_discovered_email(email="a@acme.com", name="A")
    run_b, email_b = await _seed_discovered_email(email="b@acme.com", name="B")

    app.dependency_overrides[get_current_user] = lambda: _override_user(owner)
    try:
        async with _client() as client:
            await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_a},
            )
            await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_b},
            )
            listing = await client.get("/api/v1/saved-contacts")
        assert listing.status_code == 200
        body = listing.json()
        assert [row["contact_id"] for row in body] == [email_b, email_a]

        # Intruder sees none of the owner's saves.
        app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
        async with _client() as client:
            intruder_listing = await client.get("/api/v1/saved-contacts")
        assert intruder_listing.status_code == 200
        assert intruder_listing.json() == []
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [run_a, run_b])


async def test_list_source_filter() -> None:
    user_id = await _seed_user()
    run_id, email_id = await _seed_discovered_email()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_id},
            )
            match = await client.get(
                "/api/v1/saved-contacts?source=discovered_email"
            )
            no_match = await client.get("/api/v1/saved-contacts?source=other")
        assert match.status_code == 200
        assert len(match.json()) == 1
        assert no_match.status_code == 200
        assert no_match.json() == []
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [run_id])


# ── DELETE ───────────────────────────────────────────────────────────────────


async def test_delete_removes_own_row() -> None:
    user_id = await _seed_user()
    run_id, email_id = await _seed_discovered_email()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            saved = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_id},
            )
            saved_id = saved.json()["id"]
            deleted = await client.delete(f"/api/v1/saved-contacts/{saved_id}")
        assert deleted.status_code == 204

        async with SessionLocal() as session:
            remaining = (
                await session.execute(
                    select(SavedContact).where(SavedContact.id == saved_id)
                )
            ).scalar_one_or_none()
            assert remaining is None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [run_id])


async def test_delete_404_when_absent() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.delete("/api/v1/saved-contacts/99999999")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_delete_404_for_foreign_row() -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    run_id, email_id = await _seed_discovered_email()

    app.dependency_overrides[get_current_user] = lambda: _override_user(owner)
    try:
        async with _client() as client:
            saved = await client.post(
                "/api/v1/saved-contacts",
                json={"source": "discovered_email", "contact_id": email_id},
            )
            saved_id = saved.json()["id"]

        # Intruder cannot delete the owner's saved contact.
        app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
        async with _client() as client:
            response = await client.delete(f"/api/v1/saved-contacts/{saved_id}")
        assert response.status_code == 404

        # Owner's row is untouched.
        async with SessionLocal() as session:
            still_there = (
                await session.execute(
                    select(SavedContact).where(SavedContact.id == saved_id)
                )
            ).scalar_one_or_none()
            assert still_there is not None
    finally:
        app.dependency_overrides.clear()
        await _cleanup([owner, intruder], [run_id])
