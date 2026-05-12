"""API-layer tests for /vault/folders/{id}/files.

Integration-marked. GCS and the async processing orchestrator are
monkeypatched out — the BE-side tests are about ownership/auth/cap
enforcement and DB row state, not provider plumbing. The actual GCS
upload + extraction + embedding paths are smoke-tested on staging
post-deploy.
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
from app.models.vault_folder import VaultFolder
from app.models.vault_folder_file import VaultFolderFile
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Test User",
        email=f"{user_id}@example.com",
        role="viewer",
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


async def _seed_folder(user_id: str, name: str = "Custody") -> int:
    async with SessionLocal() as session:
        folder = VaultFolder(user_id=user_id, name=name)
        session.add(folder)
        await session.commit()
        await session.refresh(folder)
        return folder.id


async def _cleanup(user_ids: list[str]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            # vault_folder_file cascades from vault_folder.
            await session.execute(
                delete(VaultFolder).where(VaultFolder.user_id.in_(user_ids))
            )
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
            await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def patch_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub GCS + the async orchestrator so tests stay DB-only."""

    async def _fake_upload(
        *, user_id: str, folder_id: int, original_filename: str, content: bytes,
        content_type: str
    ) -> str:
        return f"vault/{user_id}/{folder_id}/test-{secrets.token_hex(4)}.bin"

    async def _fake_delete(object_name: str) -> None:
        return None

    async def _fake_signed_url(object_name: str, *, ttl_seconds: int = 300) -> str:
        return f"https://example.invalid/signed/{object_name}"

    async def _fake_process(file_id: int) -> None:
        # Don't run extraction/embedding in tests — caller asserts on
        # the row state immediately after upload.
        return None

    # Patch where the endpoint imports them, not where they're defined.
    monkeypatch.setattr(
        "app.api.v1.endpoints.vault_files.upload_file", _fake_upload
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.vault_files.delete_object", _fake_delete
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.vault_files.signed_download_url", _fake_signed_url
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.vault_files.process_uploaded_file", _fake_process
    )


# ── 401 paths ───────────────────────────────────────────────────────────────


async def test_upload_401_without_session() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/vault/folders/1/files",
            files={"file": ("x.txt", b"hi", "text/plain")},
        )
    assert response.status_code == 401


async def test_list_files_401_without_session() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/vault/folders/1/files")
    assert response.status_code == 401


# ── Happy path ──────────────────────────────────────────────────────────────


async def test_upload_creates_row_and_kicks_processing(patch_storage) -> None:
    user_id = await _seed_user()
    folder_id = await _seed_folder(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/vault/folders/{folder_id}/files",
                files={"file": ("notes.txt", b"hello world", "text/plain")},
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["folder_id"] == folder_id
        assert body["original_filename"] == "notes.txt"
        assert body["mime_type"] == "text/plain"
        assert body["size_bytes"] == 11  # len(b"hello world")
        assert body["processing_status"] == "extracting"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_id])


async def test_list_returns_only_callers_files(patch_storage) -> None:
    owner = await _seed_user()
    other = await _seed_user()
    owner_folder = await _seed_folder(owner, "Custody")
    other_folder = await _seed_folder(other, "Stock Loan")

    app.dependency_overrides[get_current_user] = lambda: _override_user(owner)
    try:
        async with _client() as client:
            await client.post(
                f"/api/v1/vault/folders/{owner_folder}/files",
                files={"file": ("a.txt", b"a", "text/plain")},
            )
        # Other user uploads to their own folder.
        app.dependency_overrides[get_current_user] = lambda: _override_user(other)
        async with _client() as client:
            await client.post(
                f"/api/v1/vault/folders/{other_folder}/files",
                files={"file": ("b.txt", b"b", "text/plain")},
            )
        # Back to owner: should see only their one file.
        app.dependency_overrides[get_current_user] = lambda: _override_user(owner)
        async with _client() as client:
            response = await client.get(f"/api/v1/vault/folders/{owner_folder}/files")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["original_filename"] == "a.txt"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([owner, other])


async def test_list_files_for_other_users_folder_returns_404(patch_storage) -> None:
    owner = await _seed_user()
    intruder = await _seed_user()
    folder_id = await _seed_folder(owner)

    app.dependency_overrides[get_current_user] = lambda: _override_user(intruder)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/vault/folders/{folder_id}/files"
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "vault_folder_not_found"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([owner, intruder])


async def test_delete_file_removes_row(patch_storage) -> None:
    user_id = await _seed_user()
    folder_id = await _seed_folder(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            create = await client.post(
                f"/api/v1/vault/folders/{folder_id}/files",
                files={"file": ("a.txt", b"a", "text/plain")},
            )
        file_id = create.json()["id"]

        async with _client() as client:
            response = await client.delete(
                f"/api/v1/vault/folders/{folder_id}/files/{file_id}"
            )
        assert response.status_code == 204

        async with _client() as client:
            after = await client.get(
                f"/api/v1/vault/folders/{folder_id}/files"
            )
        assert after.status_code == 200
        assert after.json() == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_id])


async def test_retry_only_allowed_for_failed_files(patch_storage) -> None:
    user_id = await _seed_user()
    folder_id = await _seed_folder(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            create = await client.post(
                f"/api/v1/vault/folders/{folder_id}/files",
                files={"file": ("a.txt", b"a", "text/plain")},
            )
        file_id = create.json()["id"]

        # Fresh row is in "extracting" state — retry should 400.
        async with _client() as client:
            response = await client.post(
                f"/api/v1/vault/folders/{folder_id}/files/{file_id}/retry"
            )
        assert response.status_code == 400
        assert "extracting" in response.json()["detail"]

        # Flip to failed via direct DB write, retry should succeed.
        async with SessionLocal() as session:
            await session.execute(
                VaultFolderFile.__table__.update()
                .where(VaultFolderFile.id == file_id)
                .values(processing_status="failed", processing_error="boom")
            )
            await session.commit()

        async with _client() as client:
            response = await client.post(
                f"/api/v1/vault/folders/{folder_id}/files/{file_id}/retry"
            )
        assert response.status_code == 200
        assert response.json()["processing_status"] == "extracting"
        assert response.json()["processing_error"] is None
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_id])


# ── Cap enforcement ─────────────────────────────────────────────────────────


async def test_upload_rejects_unsupported_mime_with_415(patch_storage) -> None:
    user_id = await _seed_user()
    folder_id = await _seed_folder(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/vault/folders/{folder_id}/files",
                files={"file": ("evil.exe", b"MZ...", "application/x-msdownload")},
            )
        assert response.status_code == 415
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_id])


async def test_upload_rejects_empty_file_with_400(patch_storage) -> None:
    user_id = await _seed_user()
    folder_id = await _seed_folder(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/vault/folders/{folder_id}/files",
                files={"file": ("empty.txt", b"", "text/plain")},
            )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_id])


async def test_upload_rejects_oversize_with_413(
    patch_storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cap is 10MB — patch it down for fast asserts."""
    monkeypatch.setattr(
        "app.api.v1.endpoints.vault_files.MAX_FILE_SIZE_BYTES", 1024
    )
    user_id = await _seed_user()
    folder_id = await _seed_folder(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/vault/folders/{folder_id}/files",
                files={"file": ("big.txt", b"x" * 4096, "text/plain")},
            )
        assert response.status_code == 413
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _cleanup([user_id])
