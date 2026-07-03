"""API-layer guards for POST /banks/{id}/gap-fill-contacts.

Integration-marked (needs Postgres for the Bank + PipelineRun rows). The
banks analog of the advisor refresh-gate tests: unknown bank → 404, a queued
run returns 202 + a run_id and writes the PipelineRun, and a second call while
a run is in flight attaches to it (202 + status "in_flight") rather than
queueing a duplicate.

The discovery BackgroundTask is monkeypatched to a no-op so the endpoint's
queue/guard logic is exercised WITHOUT firing real (paid) provider discovery.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime

import httpx
import pytest
from sqlalchemy import delete, select

import app.api.v1.endpoints.banks as banks_endpoint
from app.db.session import SessionLocal
from app.main import app
from app.models.bank import Bank
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user
from app.services.bank_contact_gap_fill import GAP_FILL_BANK_CONTACTS_PIPELINE_NAME

pytestmark = pytest.mark.integration


def _override_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"test-user-{secrets.token_hex(6)}",
        name="Caller",
        email="bank-gapfill-test@example.com",
        role="admin",  # admin bypasses the BANKS feature gate
        session_expires_at=datetime(2099, 1, 1),
    )


@pytest.fixture(autouse=True)
def _bypass_auth_and_bg(monkeypatch: pytest.MonkeyPatch) -> object:
    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    # Patch the symbol the endpoint's wrapper actually calls, so no real
    # discovery (and no Apollo/Hunter spend) runs during the test.
    monkeypatch.setattr(banks_endpoint, "run_gap_fill_bank_contacts_background", _noop)
    app.dependency_overrides[get_current_user] = _override_user
    try:
        yield
    finally:
        app.dependency_overrides.clear()


async def _seed_bank() -> int:
    async with SessionLocal() as session:
        bank = Bank(name=f"Gap Fill Bank {secrets.token_hex(4)}", charter_status="opened")
        session.add(bank)
        await session.flush()
        bank_id = bank.id
        await session.commit()
    return bank_id


async def _cleanup(bank_ids: list[int]) -> None:
    async with SessionLocal() as session:
        for bank_id in bank_ids:
            await session.execute(
                delete(PipelineRun).where(
                    PipelineRun.pipeline_name == GAP_FILL_BANK_CONTACTS_PIPELINE_NAME,
                    PipelineRun.notes.ilike(f'%"bank_id": {bank_id}%'),
                )
            )
        if bank_ids:
            await session.execute(delete(Bank).where(Bank.id.in_(bank_ids)))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_gap_fill_unknown_bank_returns_404() -> None:
    async with _client() as client:
        response = await client.post("/api/v1/banks/999999999/gap-fill-contacts?force=true")
    assert response.status_code == 404
    assert response.json()["detail"] == "Bank not found."


async def test_gap_fill_queues_run_and_persists_pipeline_run() -> None:
    bank_id = await _seed_bank()
    try:
        async with _client() as client:
            response = await client.post(f"/api/v1/banks/{bank_id}/gap-fill-contacts?force=true")
        assert response.status_code == 202
        body = response.json()
        assert isinstance(body["run_id"], int)
        assert body["status"] == "queued"
        assert body["bank_id"] == bank_id
        assert body["reason"] is None

        async with SessionLocal() as session:
            run = (
                await session.execute(
                    select(PipelineRun).where(PipelineRun.id == body["run_id"])
                )
            ).scalar_one()
            assert run.pipeline_name == GAP_FILL_BANK_CONTACTS_PIPELINE_NAME
            assert json.loads(run.notes)["bank_id"] == bank_id
    finally:
        await _cleanup([bank_id])


async def test_gap_fill_attaches_to_in_flight_run() -> None:
    bank_id = await _seed_bank()
    try:
        # Pre-seed an in-flight run for this bank (started_at defaults to now()).
        async with SessionLocal() as session:
            run = PipelineRun(
                pipeline_name=GAP_FILL_BANK_CONTACTS_PIPELINE_NAME,
                trigger_source="manual_gap_fill:someone@example.com",
                status="running",
                total_items=1,
                notes=json.dumps({"bank_id": bank_id, "stage": "queued"}),
            )
            session.add(run)
            await session.flush()
            existing_run_id = run.id
            await session.commit()

        async with _client() as client:
            response = await client.post(f"/api/v1/banks/{bank_id}/gap-fill-contacts?force=true")
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "in_flight"
        assert body["run_id"] == existing_run_id
    finally:
        await _cleanup([bank_id])


async def test_gap_fill_ignores_prefix_colliding_bank_run() -> None:
    """Regression: a bank id must not prefix-match another bank's run.

    ``notes`` stores the id as JSON text, so a naive ``ILIKE '%"bank_id": N%'``
    also matched a *superstring* id — bank 12 would wrongly attach to bank 123's
    in-flight run and silently discover nothing. The marker must be delimited
    (the id is always followed by ',' or '}'). Here bank ``N`` must NOT attach to
    an in-flight run recorded for the digit-superstring id ``N00007``; it must
    queue its own fresh run instead.
    """
    bank_id = await _seed_bank()
    colliding_id = int(f"{bank_id}00007")  # bank_id is a strict digit-prefix of this
    try:
        async with SessionLocal() as session:
            run = PipelineRun(
                pipeline_name=GAP_FILL_BANK_CONTACTS_PIPELINE_NAME,
                trigger_source="manual_gap_fill:other@example.com",
                status="running",
                total_items=1,
                notes=json.dumps({"bank_id": colliding_id, "stage": "queued"}),
            )
            session.add(run)
            await session.flush()
            colliding_run_id = run.id
            await session.commit()

        async with _client() as client:
            response = await client.post(f"/api/v1/banks/{bank_id}/gap-fill-contacts?force=true")
        assert response.status_code == 202
        body = response.json()
        # Must QUEUE a fresh run for THIS bank — never attach to the superstring's run.
        assert body["status"] == "queued", body
        assert body["run_id"] != colliding_run_id
        assert body["bank_id"] == bank_id
    finally:
        async with SessionLocal() as session:
            await session.execute(
                delete(PipelineRun).where(PipelineRun.id == colliding_run_id)
            )
            await session.commit()
        await _cleanup([bank_id])
