"""End-to-end POST -> GET round-trip against a real Postgres.

Marked as `integration` because it requires DATABASE_URL to point at a
reachable Postgres with the migration applied. Skipped by default; run with
`pytest -m integration` against the Docker stack (or staging).

Decision driver: Docker is not installed locally. This test will be exercised
in the dedicated VPS-deploy follow-up prompt. The non-integration test suite
(http transport + respx) covers everything that does not require a real DB.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.main import app

pytestmark = pytest.mark.integration


async def test_post_then_get_scan_round_trip() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        post_response = await client.post(
            "/api/v1/email-extractor/scans",
            json={"domain": "example.com"},
        )
        assert post_response.status_code == 202
        body = post_response.json()
        assert isinstance(body["id"], int)
        assert body["domain"] == "example.com"
        assert body["status"] in {"queued", "running"}

        run_id = body["id"]

        get_response = await client.get(f"/api/v1/email-extractor/scans/{run_id}")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == run_id

        # Wait past the stub aggregator's sleep, then confirm the row reaches `completed`.
        await asyncio.sleep(2.5)

        final_response = await client.get(f"/api/v1/email-extractor/scans/{run_id}")
        assert final_response.status_code == 200
        final_body = final_response.json()
        assert final_body["status"] == "completed"
        assert final_body["completed_at"] is not None
        assert final_body["discovered_emails"] == []


async def test_get_scan_unknown_returns_404() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/email-extractor/scans/999999999")
    assert response.status_code == 404
    assert response.json()["detail"] == "scan not found"
