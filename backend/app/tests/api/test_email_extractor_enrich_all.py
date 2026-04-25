"""API-layer tests for POST /email-extractor/scans/{run_id}/enrich-all.

Integration-marked: seeds an ``ExtractionRun`` plus discovered_email rows in
a real Postgres so the count queries actually execute. Auth is bypassed via
``app.dependency_overrides[get_current_user]`` since the focus here is the
endpoint's branching, not the BetterAuth session probe.

The 202 path verifies the response counts and that the background task is
enqueued without actually running it (we patch ``run_bulk_enrichment`` to a
no-op so the test stays fast and deterministic).
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import delete

from app.api.v1.endpoints import email_extractor as endpoint_module
from app.core.config import settings
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
        email="enrich-all-test@example.com",
        role="viewer",
        session_expires_at=datetime(2099, 1, 1),
    )


async def _seed_scan_with_emails(unenriched: int, already_enriched: int) -> int:
    """Create an ExtractionRun plus discovered_email rows; returns scan_id."""
    async with SessionLocal() as session:
        scan = ExtractionRun(domain="example.com", status=RunStatus.completed.value)
        session.add(scan)
        await session.commit()
        await session.refresh(scan)

        for index in range(unenriched):
            session.add(
                DiscoveredEmail(
                    run_id=scan.id,
                    email=f"unenriched-{index}-{secrets.token_hex(4)}@example.com",
                    domain="example.com",
                    source="hunter",
                    enrichment_status="not_enriched",
                )
            )
        for index in range(already_enriched):
            session.add(
                DiscoveredEmail(
                    run_id=scan.id,
                    email=f"enriched-{index}-{secrets.token_hex(4)}@example.com",
                    domain="example.com",
                    source="hunter",
                    enrichment_status="enriched",
                )
            )
        await session.commit()
        return scan.id


async def _cleanup_scan(scan_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(DiscoveredEmail).where(DiscoveredEmail.run_id == scan_id))
        await session.execute(delete(ExtractionRun).where(ExtractionRun.id == scan_id))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_enrich_all_404_when_scan_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-key", raising=False)
    app.dependency_overrides[get_current_user] = _override_user
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/email-extractor/scans/99999999/enrich-all"
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "scan not found"
    finally:
        app.dependency_overrides.clear()


async def test_enrich_all_503_when_apollo_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "", raising=False)
    app.dependency_overrides[get_current_user] = _override_user
    scan_id = await _seed_scan_with_emails(unenriched=2, already_enriched=0)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/email-extractor/scans/{scan_id}/enrich-all"
            )
        assert response.status_code == 503
        assert "Apollo" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        await _cleanup_scan(scan_id)


async def test_enrich_all_202_returns_correct_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-key", raising=False)

    enqueued_calls: list[tuple[Any, ...]] = []

    async def _capture(*args: Any) -> None:
        enqueued_calls.append(args)

    monkeypatch.setattr(endpoint_module, "run_bulk_enrichment", _capture)

    app.dependency_overrides[get_current_user] = _override_user
    scan_id = await _seed_scan_with_emails(unenriched=3, already_enriched=2)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/email-extractor/scans/{scan_id}/enrich-all"
            )

        assert response.status_code == 202
        body = response.json()
        assert body["scan_id"] == scan_id
        assert body["candidates_total"] == 5
        assert body["candidates_skipped_already_enriched"] == 2
        assert body["candidates_queued"] == 3
        assert body["status"] == "queued"

        # Background task is enqueued exactly once with the run_id.
        assert len(enqueued_calls) == 1
        assert enqueued_calls[0] == (scan_id,)
    finally:
        app.dependency_overrides.clear()
        await _cleanup_scan(scan_id)


async def test_enrich_all_202_when_no_unenriched_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Already-enriched-only scan still returns 202 with queued=0 -- the
    frontend can show 'all caught up' and skip polling.
    """
    monkeypatch.setattr(settings, "apollo_api_key", "test-key", raising=False)
    monkeypatch.setattr(endpoint_module, "run_bulk_enrichment", AsyncMock())

    app.dependency_overrides[get_current_user] = _override_user
    scan_id = await _seed_scan_with_emails(unenriched=0, already_enriched=4)
    try:
        async with _client() as client:
            response = await client.post(
                f"/api/v1/email-extractor/scans/{scan_id}/enrich-all"
            )
        assert response.status_code == 202
        body = response.json()
        assert body["candidates_total"] == 4
        assert body["candidates_skipped_already_enriched"] == 4
        assert body["candidates_queued"] == 0
    finally:
        app.dependency_overrides.clear()
        await _cleanup_scan(scan_id)
