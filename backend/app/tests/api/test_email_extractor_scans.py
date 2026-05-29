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
import secrets
from datetime import datetime

import httpx
import pytest

from app.db.session import SessionLocal
from app.main import app
from app.models.discovered_email import DiscoveredEmail
from app.models.extraction_run import ExtractionRun, RunStatus
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"test-user-{secrets.token_hex(6)}",
        name="Test User",
        email="email-extractor-scan-test@example.com",
        role="viewer",
        feature_permissions=["email_extractor"],
        session_expires_at=datetime(2099, 1, 1),
    )


@pytest.fixture(autouse=True)
def _bypass_auth() -> object:
    """Bypass the BetterAuth session probe for every test in this module.

    The endpoints under test depend on ``get_current_user``; the focus here
    is the scan POST/GET round-trip, not session validation. Same pattern
    as ``test_email_extractor_enrich_all.py``.
    """
    app.dependency_overrides[get_current_user] = _override_user
    try:
        yield
    finally:
        app.dependency_overrides.clear()


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


async def _seed_advisor() -> int:
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(
            name=f"Advisor Scan Test {secrets.token_hex(4)}",
            matched_source="edgar",
            status="active",
        )
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)
        return advisor.id


async def test_post_scan_with_advisor_id_round_trip() -> None:
    advisor_id = await _seed_advisor()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        post_response = await client.post(
            "/api/v1/email-extractor/scans",
            json={"domain": "advisor-example.com", "advisor_id": advisor_id},
        )
        assert post_response.status_code == 202
        scan_id = post_response.json()["id"]

        list_response = await client.get(
            "/api/v1/email-extractor/scans",
            params={"advisor_id": advisor_id, "limit": 5},
        )
        assert list_response.status_code == 200
        items = list_response.json()
        ids = {item["id"] for item in items}
        assert scan_id in ids
        for item in items:
            assert item["advisor_id"] == advisor_id


async def test_post_scan_rejects_both_bd_and_advisor_id() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/email-extractor/scans",
            json={"domain": "example.com", "bd_id": 1, "advisor_id": 1},
        )
    assert response.status_code == 422


async def test_list_scans_filter_by_advisor_id_narrows() -> None:
    advisor_a = await _seed_advisor()
    advisor_b = await _seed_advisor()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        post_a = await client.post(
            "/api/v1/email-extractor/scans",
            json={"domain": "advisor-a.example.com", "advisor_id": advisor_a},
        )
        post_b = await client.post(
            "/api/v1/email-extractor/scans",
            json={"domain": "advisor-b.example.com", "advisor_id": advisor_b},
        )
        assert post_a.status_code == 202
        assert post_b.status_code == 202
        scan_a_id = post_a.json()["id"]
        scan_b_id = post_b.json()["id"]

        only_a = await client.get(
            "/api/v1/email-extractor/scans",
            params={"advisor_id": advisor_a, "limit": 50},
        )
        assert only_a.status_code == 200
        a_ids = {item["id"] for item in only_a.json()}
        assert scan_a_id in a_ids
        assert scan_b_id not in a_ids


async def test_get_scan_orders_discovered_emails_by_id() -> None:
    """Discovered emails come back in stable ascending-id (discovery) order,
    even after a row is mutated (e.g. enriched).

    Without the relationship's ``order_by`` the collection follows Postgres
    heap order, which can bump a just-updated row to the end and make it jump
    pages in the results table. We mutate the lowest-id row to reproduce that
    case, then assert the GET still returns ascending-id order.
    """
    async with SessionLocal() as session:
        run = ExtractionRun(
            domain="order-test.example.com",
            status=RunStatus.completed.value,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        rows = [
            DiscoveredEmail(
                run_id=run_id,
                email=f"user{i}@order-test.example.com",
                domain="order-test.example.com",
                source="hunter",
            )
            for i in range(4)
        ]
        session.add_all(rows)
        await session.flush()
        seeded_ids = sorted(r.id for r in rows)
        await session.commit()

    # Rewrite the lowest-id row's tuple -- the mutation that used to push it
    # out of order on the next load.
    async with SessionLocal() as session:
        row = await session.get(DiscoveredEmail, seeded_ids[0])
        assert row is not None
        row.enrichment_status = "enriched"
        await session.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/api/v1/email-extractor/scans/{run_id}")
    assert response.status_code == 200
    returned_ids = [e["id"] for e in response.json()["discovered_emails"]]
    assert returned_ids == seeded_ids
