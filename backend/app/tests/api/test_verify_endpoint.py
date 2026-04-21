"""Endpoint tests for POST /api/v1/email-extractor/verify and
GET /api/v1/email-extractor/verify-runs/{run_id}.

POST /verify is now async: it persists a `VerificationRun` row, schedules
the SMTP probe pipeline as a `BackgroundTasks` job, and returns 202 with
`{verify_run_id, status}`. GET /verify-runs/{id} returns the run state +
latest `EmailVerification` per requested email_id in input order.

Marked `integration` because rows go through real Postgres. SMTP probes
are mocked at the runner module's `check_smtp` import site — no network.

Walltime tests for concurrency exercise `run_smtp_verification` directly
(unit-level), since `BackgroundTasks` inside `httpx.ASGITransport` doesn't
provide a deterministic await point.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy import select

from app.api.v1.endpoints import email_extractor as endpoint_module
from app.core import config
from app.db.session import SessionLocal
from app.main import app
from app.models.discovered_email import DiscoveredEmail
from app.models.email_verification import EmailVerification, SmtpStatus
from app.models.extraction_run import ExtractionRun, RunStatus
from app.models.verification_run import VerificationRun
from app.services.email_extractor import verification_runner
from app.services.email_extractor.verification_runner import run_smtp_verification

pytestmark = pytest.mark.integration


SmtpFn = Callable[[str], Awaitable[tuple[SmtpStatus, str | None]]]


async def _seed_emails(count: int, domain: str = "example.com") -> list[int]:
    """Insert one ExtractionRun + ``count`` DiscoveredEmail rows; return IDs."""
    async with SessionLocal() as session:
        run = ExtractionRun(domain=domain, status=RunStatus.completed.value)
        session.add(run)
        await session.flush()
        emails = [
            DiscoveredEmail(
                run_id=run.id,
                email=f"u{i}@{domain}",
                domain=domain,
                source="test",
                confidence=0.5,
            )
            for i in range(count)
        ]
        session.add_all(emails)
        await session.commit()
        return [e.id for e in emails]


def _patch_check_smtp(monkeypatch: pytest.MonkeyPatch, fake: SmtpFn) -> None:
    """Patch the runner's bound `check_smtp` so the probe is mocked end-to-end."""
    monkeypatch.setattr(verification_runner, "check_smtp", fake)


def _patch_runner_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op the endpoint's `run_smtp_verification` binding.

    `httpx.ASGITransport` synchronously awaits FastAPI `BackgroundTasks` before
    returning the response — it does not match the production behaviour where
    uvicorn fires BG tasks after the response is on the wire. Patching the
    endpoint module's bound name keeps POST near-instant and lets each test
    drive the runner explicitly via the `await run_smtp_verification(...)`
    helper, with deterministic ordering and no exception propagation through
    the transport layer.
    """

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(endpoint_module, "run_smtp_verification", _noop)


async def _post_verify(payload: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/api/v1/email-extractor/verify", json=payload)


async def _get_verify_run(run_id: int) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(f"/api/v1/email-extractor/verify-runs/{run_id}")


# --- POST /verify (queueing contract) --------------------------------------


async def test_verify_post_returns_queued_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    email_ids = await _seed_emails(2)
    _patch_runner_noop(monkeypatch)

    response = await _post_verify({"email_ids": email_ids})

    assert response.status_code == 202
    body = response.json()
    assert isinstance(body["verify_run_id"], int)
    assert body["status"] == RunStatus.queued.value

    async with SessionLocal() as session:
        run = await session.get(VerificationRun, body["verify_run_id"])
        assert run is not None
        assert run.email_ids == email_ids
        assert run.total_items == len(email_ids)


async def test_verify_post_completes_in_under_500ms(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST must return immediately — runner is no-op'd to isolate handler latency.

    `httpx.ASGITransport` synchronously awaits FastAPI BackgroundTasks before
    returning the response, so the only way to measure the handler's own work
    in-process is to patch the runner to a no-op. In production, uvicorn fires
    BG tasks after the response is on the wire.
    """
    email_ids = await _seed_emails(5)
    _patch_runner_noop(monkeypatch)

    start = time.perf_counter()
    response = await _post_verify({"email_ids": email_ids})
    elapsed = time.perf_counter() - start

    assert response.status_code == 202
    assert elapsed < 0.5, f"POST handler should be near-instant; took {elapsed:.3f}s"


# --- GET /verify-runs/{id} after run completion ----------------------------


async def test_verify_run_get_returns_results_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email_ids = await _seed_emails(3)

    async def fake(_email: str) -> tuple[SmtpStatus, str | None]:
        return SmtpStatus.deliverable, None

    _patch_check_smtp(monkeypatch, fake)
    _patch_runner_noop(monkeypatch)

    post_response = await _post_verify({"email_ids": email_ids})
    assert post_response.status_code == 202
    verify_run_id = post_response.json()["verify_run_id"]

    # The endpoint's BG task is no-op'd; run the real runner explicitly so
    # assertions don't race the ASGI response cycle.
    await run_smtp_verification(verify_run_id=verify_run_id, email_ids=email_ids)

    response = await _get_verify_run(verify_run_id)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == verify_run_id
    assert body["status"] == RunStatus.completed.value
    assert body["completed_at"] is not None
    assert body["processed_items"] == 3
    assert body["success_count"] == 3
    assert body["failure_count"] == 0
    assert len(body["results"]) == 3
    for item in body["results"]:
        assert item["smtp_status"] == "deliverable"
        assert item["smtp_message"] is None
        assert item["email"] is not None
        assert item["checked_at"] is not None


async def test_verify_run_preserves_input_order(monkeypatch: pytest.MonkeyPatch) -> None:
    email_ids = await _seed_emails(3)
    reordered = [email_ids[2], email_ids[0], email_ids[1]]

    async def fake(_email: str) -> tuple[SmtpStatus, str | None]:
        return SmtpStatus.deliverable, None

    _patch_check_smtp(monkeypatch, fake)
    _patch_runner_noop(monkeypatch)

    post_response = await _post_verify({"email_ids": reordered})
    verify_run_id = post_response.json()["verify_run_id"]
    await run_smtp_verification(verify_run_id=verify_run_id, email_ids=reordered)

    response = await _get_verify_run(verify_run_id)
    assert response.status_code == 200
    returned_ids = [item["email_id"] for item in response.json()["results"]]
    assert returned_ids == reordered


async def test_verify_run_inconclusive_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    email_ids = await _seed_emails(1)

    async def fake(_email: str) -> tuple[SmtpStatus, str | None]:
        return SmtpStatus.inconclusive, None

    _patch_check_smtp(monkeypatch, fake)
    _patch_runner_noop(monkeypatch)

    post_response = await _post_verify({"email_ids": email_ids})
    verify_run_id = post_response.json()["verify_run_id"]
    await run_smtp_verification(verify_run_id=verify_run_id, email_ids=email_ids)

    response = await _get_verify_run(verify_run_id)
    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["smtp_status"] == "inconclusive"
    # Inconclusive counts as success in the run-level counters (PR #13 contract).
    assert body["success_count"] == 1


async def test_verify_run_404_on_unknown_id() -> None:
    response = await _get_verify_run(999_999_999)
    assert response.status_code == 404
    assert response.json()["detail"] == "verify run not found"


async def test_verify_run_failed_status_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    email_ids = await _seed_emails(2)

    async def fake_boom(_email: str) -> tuple[SmtpStatus, str | None]:
        raise RuntimeError("simulated probe failure")

    _patch_check_smtp(monkeypatch, fake_boom)
    _patch_runner_noop(monkeypatch)

    post_response = await _post_verify({"email_ids": email_ids})
    verify_run_id = post_response.json()["verify_run_id"]

    with pytest.raises(RuntimeError, match="simulated probe failure"):
        await run_smtp_verification(verify_run_id=verify_run_id, email_ids=email_ids)

    response = await _get_verify_run(verify_run_id)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == RunStatus.failed.value
    assert body["error_message"] is not None
    assert "simulated probe failure" in body["error_message"]
    assert body["completed_at"] is not None


# --- Edge cases ------------------------------------------------------------


async def test_verify_unknown_id_does_not_write_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email_ids = await _seed_emails(1)
    unknown_id = 99_999_998
    payload_ids = [email_ids[0], unknown_id]

    async def fake(_email: str) -> tuple[SmtpStatus, str | None]:
        return SmtpStatus.deliverable, None

    _patch_check_smtp(monkeypatch, fake)
    _patch_runner_noop(monkeypatch)

    post_response = await _post_verify({"email_ids": payload_ids})
    assert post_response.status_code == 202
    verify_run_id = post_response.json()["verify_run_id"]
    await run_smtp_verification(verify_run_id=verify_run_id, email_ids=payload_ids)

    response = await _get_verify_run(verify_run_id)
    body = response.json()
    returned_ids = [item["email_id"] for item in body["results"]]
    assert returned_ids == [email_ids[0]]
    assert body["results"][0]["smtp_status"] == "deliverable"

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(EmailVerification).where(EmailVerification.discovered_email_id == unknown_id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


async def test_verify_empty_list_rejected() -> None:
    response = await _post_verify({"email_ids": []})
    assert response.status_code == 422


async def test_verify_duplicate_ids_rejected() -> None:
    response = await _post_verify({"email_ids": [1, 1, 2]})
    assert response.status_code == 422


async def test_verify_batch_size_over_cap_413() -> None:
    response = await _post_verify({"email_ids": list(range(1, 27))})
    assert response.status_code == 413
    body = response.json()
    assert "26" in body["detail"]
    assert "25" in body["detail"]


async def test_verify_all_unknown_ids_returns_404() -> None:
    response = await _post_verify({"email_ids": [999_999_998, 999_999_999]})
    assert response.status_code == 404
    assert response.json()["detail"] == "no matching email_ids"


# --- Concurrency walltime (exercises run_smtp_verification directly) -------


async def test_verify_concurrency_parallelism_walltime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrency=5 with 5 IDs at 0.5s/probe → walltime <1.5s (proves parallelism;
    serial would be ~2.5s)."""
    email_ids = await _seed_emails(5)
    monkeypatch.setattr(config.settings, "smtp_verify_concurrency", 5)

    async def fake_slow(_email: str) -> tuple[SmtpStatus, str | None]:
        await asyncio.sleep(0.5)
        return SmtpStatus.deliverable, None

    _patch_check_smtp(monkeypatch, fake_slow)

    async with SessionLocal() as session:
        run = VerificationRun(
            email_ids=email_ids,
            status=RunStatus.queued.value,
            total_items=len(email_ids),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        verify_run_id = run.id

    start = time.perf_counter()
    await run_smtp_verification(verify_run_id=verify_run_id, email_ids=email_ids)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.5, f"expected <1.5s for 5 parallel probes at 0.5s each, got {elapsed:.2f}s"

    async with SessionLocal() as session:
        run = await session.get(VerificationRun, verify_run_id)
        assert run is not None
        assert run.status == RunStatus.completed.value
        assert run.processed_items == 5


async def test_verify_concurrency_one_still_serializes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrency=1 with 3 IDs at 0.5s/probe → walltime ≥1.4s (proves serialization
    holds at the conservative default)."""
    email_ids = await _seed_emails(3)
    monkeypatch.setattr(config.settings, "smtp_verify_concurrency", 1)

    async def fake_slow(_email: str) -> tuple[SmtpStatus, str | None]:
        await asyncio.sleep(0.5)
        return SmtpStatus.deliverable, None

    _patch_check_smtp(monkeypatch, fake_slow)

    async with SessionLocal() as session:
        run = VerificationRun(
            email_ids=email_ids,
            status=RunStatus.queued.value,
            total_items=len(email_ids),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        verify_run_id = run.id

    start = time.perf_counter()
    await run_smtp_verification(verify_run_id=verify_run_id, email_ids=email_ids)
    elapsed = time.perf_counter() - start

    assert elapsed >= 1.4, f"expected ≥1.4s for 3 serial probes at 0.5s each, got {elapsed:.2f}s"
