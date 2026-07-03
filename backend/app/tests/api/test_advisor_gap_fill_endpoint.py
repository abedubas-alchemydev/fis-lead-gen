"""API-layer in-flight guard for POST /investment-advisors/{id}/gap-fill-contacts.

Integration-marked (needs Postgres for the InvestmentAdvisor + PipelineRun rows).
Focus: the advisor gap-fill run-matching must key on the FULL advisor_id JSON
token so one advisor id never prefix-matches another (advisor 12 vs 123's run) —
the analog of the banks fix. The discovery BackgroundTask is monkeypatched to a
no-op so the endpoint's guard logic runs WITHOUT firing real (paid) discovery.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime

import httpx
import pytest
from sqlalchemy import delete

import app.api.v1.endpoints.investment_advisors as ia_endpoint
from app.db.session import SessionLocal
from app.main import app
from app.models.investment_advisor import InvestmentAdvisor
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.services.advisor_refresh_orchestrator import (
    GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME,
)
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"test-user-{secrets.token_hex(6)}",
        name="Caller",
        email="advisor-gapfill-test@example.com",
        role="admin",  # admin bypasses the INVESTMENT_ADVISORS feature gate
        session_expires_at=datetime(2099, 1, 1),
    )


@pytest.fixture(autouse=True)
def _bypass_auth_and_bg(monkeypatch: pytest.MonkeyPatch) -> object:
    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    # Patch the runner the endpoint schedules, so no real (paid) discovery fires.
    monkeypatch.setattr(ia_endpoint, "_run_gap_fill_contacts_background", _noop)
    app.dependency_overrides[get_current_user] = _override_user
    try:
        yield
    finally:
        app.dependency_overrides.clear()


async def _seed_advisor() -> int:
    async with SessionLocal() as session:
        advisor = InvestmentAdvisor(
            name=f"GapFill Advisor {secrets.token_hex(4)}", status="active"
        )
        session.add(advisor)
        await session.flush()
        advisor_id = advisor.id
        await session.commit()
    return advisor_id


async def _cleanup(advisor_ids: list[int]) -> None:
    async with SessionLocal() as session:
        for advisor_id in advisor_ids:
            await session.execute(
                delete(PipelineRun).where(
                    PipelineRun.pipeline_name == GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME,
                    PipelineRun.notes.ilike(f'%"advisor_id": {advisor_id}%'),
                )
            )
        if advisor_ids:
            await session.execute(
                delete(InvestmentAdvisor).where(InvestmentAdvisor.id.in_(advisor_ids))
            )
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_advisor_gap_fill_attaches_to_in_flight_run() -> None:
    advisor_id = await _seed_advisor()
    try:
        async with SessionLocal() as session:
            run = PipelineRun(
                pipeline_name=GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME,
                trigger_source="manual_gap_fill:someone@example.com",
                status="running",
                total_items=1,
                notes=json.dumps({"advisor_id": advisor_id, "stage": "queued"}),
            )
            session.add(run)
            await session.flush()
            existing_run_id = run.id
            await session.commit()

        async with _client() as client:
            response = await client.post(
                f"/api/v1/investment-advisors/{advisor_id}/gap-fill-contacts?force=true"
            )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "in_flight"
        assert body["run_id"] == existing_run_id
    finally:
        await _cleanup([advisor_id])


async def test_advisor_gap_fill_ignores_prefix_colliding_run() -> None:
    """Regression: advisor N must not prefix-match a superstring id's run.

    ``notes`` stores the id as JSON text, so a naive ``ILIKE '%"advisor_id": N%'``
    also matched a longer id — advisor 12 would wrongly attach to advisor 123's
    in-flight run. The marker match must be delimited (id followed by ',' or
    '}'). Here advisor ``N`` must NOT attach to a run recorded for ``N00007``.
    """
    advisor_id = await _seed_advisor()
    colliding_id = int(f"{advisor_id}00007")  # advisor_id is a strict digit-prefix
    try:
        async with SessionLocal() as session:
            run = PipelineRun(
                pipeline_name=GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME,
                trigger_source="manual_gap_fill:other@example.com",
                status="running",
                total_items=1,
                notes=json.dumps({"advisor_id": colliding_id, "stage": "queued"}),
            )
            session.add(run)
            await session.flush()
            colliding_run_id = run.id
            await session.commit()

        async with _client() as client:
            response = await client.post(
                f"/api/v1/investment-advisors/{advisor_id}/gap-fill-contacts?force=true"
            )
        assert response.status_code == 202, response.text
        body = response.json()
        # Must QUEUE a fresh run for THIS advisor, not attach to the superstring's.
        assert body["status"] == "queued", body
        assert body["run_id"] != colliding_run_id
        assert body["advisor_id"] == advisor_id
    finally:
        async with SessionLocal() as session:
            await session.execute(
                delete(PipelineRun).where(PipelineRun.id == colliding_run_id)
            )
            await session.commit()
        await _cleanup([advisor_id])
