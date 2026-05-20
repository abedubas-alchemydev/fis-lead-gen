"""API-layer tests for GET /investment-advisors/{id}/13f/latest.

Verifies the 302-to-SEC redirect path and the 404 branches: missing
advisor, files_13f=False, missing CIK, and EDGAR returning no 13F-HR
filings. EdgarService.list_all_filings_for_cik is monkeypatched so
tests don't hit SEC.

Integration-marked: uses real Postgres via SessionLocal to seed
advisor + auth rows, same pattern as test_broker_dealers.py.
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
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.services import edgar as edgar_module
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Test User",
        email=f"{user_id}@example.com",
        role="viewer",
        feature_permissions=["investment_advisors"],
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


async def _seed_advisor(
    *,
    name: str = "Test Advisor",
    cik: str | None = "0001234567",
    files_13f: bool = True,
) -> int:
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(
            name=name,
            cik=cik,
            files_13f=files_13f,
            matched_source="iapd",
            status="active",
        )
        session.add(advisor)
        await session.commit()
        await session.refresh(advisor)
        return advisor.id


async def _cleanup(user_ids: list[str], advisor_ids: list[int]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        if advisor_ids:
            await session.execute(
                delete(InvestmentAdvisor).where(InvestmentAdvisor.id.in_(advisor_ids))
            )
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _patch_edgar(
    monkeypatch: pytest.MonkeyPatch, filings: list[dict[str, object]]
) -> None:
    async def _fake_list(self, cik: str) -> list[dict[str, object]]:
        return filings

    monkeypatch.setattr(
        edgar_module.EdgarService, "list_all_filings_for_cik", _fake_list
    )


async def test_latest_13f_401_without_session_cookie() -> None:
    """No dependency override -> real get_current_user runs and rejects."""

    advisor_id = await _seed_advisor()
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/investment-advisors/{advisor_id}/13f/latest"
            )
        assert response.status_code == 401
    finally:
        await _cleanup([], [advisor_id])


async def test_latest_13f_404_when_advisor_missing() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/investment-advisors/99999999/13f/latest"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [])


async def test_latest_13f_404_when_files_13f_false() -> None:
    user_id = await _seed_user()
    advisor_id = await _seed_advisor(files_13f=False)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/investment-advisors/{advisor_id}/13f/latest"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [advisor_id])


async def test_latest_13f_404_when_cik_null() -> None:
    user_id = await _seed_user()
    advisor_id = await _seed_advisor(cik=None, files_13f=True)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/investment-advisors/{advisor_id}/13f/latest"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [advisor_id])


async def test_latest_13f_404_when_edgar_returns_no_13f_hr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EDGAR has filings but none are 13F-HR -> 404."""

    user_id = await _seed_user()
    advisor_id = await _seed_advisor()
    _patch_edgar(
        monkeypatch,
        [
            {
                "form": "10-K",
                "accession_number": "0001234567-25-000099",
                "primary_document": "form10k.htm",
            }
        ],
    )
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/investment-advisors/{advisor_id}/13f/latest"
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [advisor_id])


async def test_latest_13f_redirects_to_latest_filing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: 302 with Location pointing to the primary document."""

    user_id = await _seed_user()
    advisor_id = await _seed_advisor(cik="0001234567")
    _patch_edgar(
        monkeypatch,
        [
            {
                "form": "13F-HR",
                "accession_number": "0001234567-25-000123",
                "primary_document": "form13fhr.xml",
            },
            {
                "form": "13F-HR",
                "accession_number": "0001234567-25-000050",
                "primary_document": "form13fhr_older.xml",
            },
        ],
    )
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.get(
                f"/api/v1/investment-advisors/{advisor_id}/13f/latest",
                follow_redirects=False,
            )
        assert response.status_code == 302
        # CIK is stripped of leading zeros and accession of dashes per
        # SEC's URL convention (see build_edgar_filing_url docstring).
        assert (
            response.headers["location"]
            == "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000123/form13fhr.xml"
        )
    finally:
        app.dependency_overrides.clear()
        await _cleanup([user_id], [advisor_id])
