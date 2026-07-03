"""Bank-scoped Email Extractor scan round-trips.

Integration-marked (requires DATABASE_URL → reachable Postgres), mirroring
``test_email_extractor_scans.py``'s advisor coverage: a scan launched with a
``bank_id`` persists + filters on it, the tri-way FK mutex is enforced, and the
bank website → registrable-domain normalization runs on the created scan.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import httpx
import pytest
from sqlalchemy import delete

from app.db.session import SessionLocal
from app.main import app
from app.models.bank import Bank
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"test-user-{secrets.token_hex(6)}",
        name="Test User",
        email="bank-scan-test@example.com",
        role="viewer",
        feature_permissions=["email_extractor"],
        session_expires_at=datetime(2099, 1, 1),
    )


@pytest.fixture(autouse=True)
def _bypass_auth() -> object:
    app.dependency_overrides[get_current_user] = _override_user
    try:
        yield
    finally:
        app.dependency_overrides.clear()


async def _seed_bank() -> int:
    async with SessionLocal() as session:
        bank = Bank(name=f"Bank Scan Test {secrets.token_hex(4)}", charter_status="opened")
        session.add(bank)
        await session.flush()
        bank_id = bank.id
        await session.commit()
    return bank_id


async def _cleanup(bank_ids: list[int]) -> None:
    async with SessionLocal() as session:
        # ON DELETE SET NULL on extraction_run/discovered_email.bank_id, so
        # dropping the bank leaves the scan rows behind with bank_id nulled;
        # that's fine for the test DB.
        if bank_ids:
            await session.execute(delete(Bank).where(Bank.id.in_(bank_ids)))
        await session.commit()


async def test_post_scan_with_bank_id_round_trip() -> None:
    bank_id = await _seed_bank()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            post_response = await client.post(
                "/api/v1/email-extractor/scans",
                json={"domain": "bank-example.com", "bank_id": bank_id},
            )
            assert post_response.status_code == 202
            scan_id = post_response.json()["id"]

            list_response = await client.get(
                "/api/v1/email-extractor/scans",
                params={"bank_id": bank_id, "limit": 5},
            )
            assert list_response.status_code == 200
            items = list_response.json()
            assert scan_id in {item["id"] for item in items}
            for item in items:
                assert item["bank_id"] == bank_id
    finally:
        await _cleanup([bank_id])


async def test_post_scan_normalizes_bank_website_to_registrable_domain() -> None:
    bank_id = await _seed_bank()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            post_response = await client.post(
                "/api/v1/email-extractor/scans",
                json={"domain": "https://www.exchangebank.com/personal", "bank_id": bank_id},
            )
            assert post_response.status_code == 202
            # The bank branch normalizes the raw website to the apex.
            assert post_response.json()["domain"] == "exchangebank.com"
    finally:
        await _cleanup([bank_id])


async def test_post_scan_rejects_bd_and_bank_id() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/email-extractor/scans",
            json={"domain": "example.com", "bd_id": 1, "bank_id": 2},
        )
    assert response.status_code == 422
