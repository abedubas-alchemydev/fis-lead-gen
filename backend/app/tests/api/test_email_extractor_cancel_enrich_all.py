"""API-layer tests for POST /email-extractor/scans/{run_id}/enrich-all/cancel.

Integration-marked: needs a real Postgres because the cancel endpoint
mutates ``extraction_run.enrich_cancelled_at`` and we assert against the
persisted value. Auth is bypassed via ``app.dependency_overrides`` since
the focus is the endpoint's idempotency contract, not the BetterAuth
session probe.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import delete

from app.db.session import SessionLocal
from app.main import app
from app.models.discovered_email import DiscoveredEmail
from app.models.extraction_run import ExtractionRun, RunStatus
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"test-user-{secrets.token_hex(6)}",
        name="Test User",
        email="cancel-enrich-all-test@example.com",
        role="viewer",
        feature_permissions=["email_extractor"],
        session_expires_at=datetime(2099, 1, 1),
    )


async def _seed_scan() -> int:
    async with SessionLocal() as session:
        scan = ExtractionRun(domain="example.com", status=RunStatus.running.value)
        session.add(scan)
        await session.commit()
        await session.refresh(scan)
        return scan.id


async def _cleanup_scan(scan_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(DiscoveredEmail).where(DiscoveredEmail.run_id == scan_id))
        await session.execute(delete(ExtractionRun).where(ExtractionRun.id == scan_id))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_cancel_404_when_scan_missing() -> None:
    app.dependency_overrides[get_current_user] = _override_user
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/email-extractor/scans/99999999/enrich-all/cancel"
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "scan not found"
    finally:
        app.dependency_overrides.clear()


async def test_cancel_stamps_timestamp_when_unstamped() -> None:
    """First cancel on a running scan persists ``enrich_cancelled_at``."""
    app.dependency_overrides[get_current_user] = _override_user
    scan_id = await _seed_scan()
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/email-extractor/scans/{scan_id}/enrich-all/cancel"
            )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == scan_id
        assert body["enrich_cancelled_at"] is not None

        async with SessionLocal() as session:
            scan = await session.get(ExtractionRun, scan_id)
            assert scan is not None
            assert scan.enrich_cancelled_at is not None
    finally:
        app.dependency_overrides.clear()
        await _cleanup_scan(scan_id)


async def test_cancel_is_idempotent_when_already_stamped() -> None:
    """Re-cancelling preserves the original timestamp.

    The user-visible guarantee is that "Stop" is safe to retry without
    silently re-stamping the row mid-cleanup, which would skew any later
    analytics on time-to-cancel.
    """
    app.dependency_overrides[get_current_user] = _override_user
    scan_id = await _seed_scan()
    original = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, scan_id)
        assert scan is not None
        scan.enrich_cancelled_at = original
        await session.commit()

    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/email-extractor/scans/{scan_id}/enrich-all/cancel"
            )
        assert response.status_code == 200

        async with SessionLocal() as session:
            refreshed = await session.get(ExtractionRun, scan_id)
            assert refreshed is not None
            assert refreshed.enrich_cancelled_at == original
    finally:
        app.dependency_overrides.clear()
        await _cleanup_scan(scan_id)
