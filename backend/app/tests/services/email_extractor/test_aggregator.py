"""Aggregator tests — DB-touching, gated as integration.

Relies on the running backend's Postgres + applied migrations. Local dev
without Docker should run these via the VPS deploy path or against a
local test Postgres.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.discovered_email import DiscoveredEmail
from app.models.email_verification import EmailVerification
from app.models.extraction_run import ExtractionRun, RunStatus
from app.services.email_extractor import aggregator, verification
from app.services.email_extractor.base import (
    DiscoveredEmailDraft,
    DiscoveryResult,
    EmailSource,
)

pytestmark = pytest.mark.integration


class _FakeProvider:
    def __init__(self, name: str, result: DiscoveryResult | Exception) -> None:
        self.name = name
        self._result = result

    async def run(self, domain: str) -> DiscoveryResult:  # noqa: ARG002
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def _new_scan(domain: str = "example.com") -> int:
    async with SessionLocal() as session:
        scan = ExtractionRun(domain=domain, status=RunStatus.queued.value)
        session.add(scan)
        await session.commit()
        await session.refresh(scan)
        return scan.id


def _stub_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(_email: str) -> tuple[bool, bool, str | None]:
        return True, True, None

    monkeypatch.setattr(verification, "check_syntax_and_mx", _ok)
    monkeypatch.setattr(aggregator, "check_syntax_and_mx", _ok)


async def test_one_provider_two_drafts_persists_two_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_verification(monkeypatch)
    run_id = await _new_scan()

    provider: EmailSource = _FakeProvider(
        "fake",
        DiscoveryResult(
            emails=[
                DiscoveredEmailDraft(email="a@example.com", source="fake", confidence=0.9),
                DiscoveredEmailDraft(email="b@example.com", source="fake", confidence=0.7),
            ]
        ),
    )

    await aggregator.run(run_id, providers=[provider])

    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, run_id)
        assert scan is not None
        assert scan.status == RunStatus.completed.value
        assert scan.success_count == 2
        assert scan.failure_count == 0

        rows = (await session.execute(select(DiscoveredEmail).where(DiscoveredEmail.run_id == run_id))).scalars().all()
        assert sorted(r.email for r in rows) == ["a@example.com", "b@example.com"]

        verifications = (
            (
                await session.execute(
                    select(EmailVerification).where(EmailVerification.discovered_email_id.in_([r.id for r in rows]))
                )
            )
            .scalars()
            .all()
        )
        assert len(verifications) == 2
        assert all(v.syntax_valid is True and v.mx_record_present is True for v in verifications)


async def test_one_failing_provider_completes_with_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_verification(monkeypatch)
    run_id = await _new_scan()

    good: EmailSource = _FakeProvider(
        "good",
        DiscoveryResult(emails=[DiscoveredEmailDraft(email="x@example.com", source="good", confidence=0.5)]),
    )
    bad: EmailSource = _FakeProvider("bad", RuntimeError("kaboom"))

    await aggregator.run(run_id, providers=[good, bad])

    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, run_id)
        assert scan is not None
        assert scan.status == RunStatus.completed.value
        assert scan.failure_count == 0
        assert scan.error_message is not None
        assert "bad" in scan.error_message and "kaboom" in scan.error_message


async def test_all_providers_raise_marks_run_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_verification(monkeypatch)
    run_id = await _new_scan()

    p1: EmailSource = _FakeProvider("p1", RuntimeError("nope"))
    p2: EmailSource = _FakeProvider("p2", RuntimeError("also nope"))

    await aggregator.run(run_id, providers=[p1, p2])

    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, run_id)
        assert scan is not None
        assert scan.status == RunStatus.failed.value
        assert scan.error_message is not None
        assert "p1" in scan.error_message and "p2" in scan.error_message


async def test_dedup_keeps_highest_confidence_across_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cross-provider dedup: lowercased email key, keep highest confidence.

    Provider A: Dup@Example.com @ 0.7, a-only@example.com @ 0.6
    Provider B: dup@example.com @ 0.9, b-only@example.com @ 0.5

    Asserts: 3 rows total; the surviving dup row has source='b' and
    confidence=0.9; both -only rows survive unchanged. Pins the contract
    documented in aggregator.py's module docstring.
    """
    _stub_verification(monkeypatch)
    run_id = await _new_scan()

    a: EmailSource = _FakeProvider(
        "a",
        DiscoveryResult(
            emails=[
                DiscoveredEmailDraft(email="Dup@Example.com", source="a", confidence=0.7),
                DiscoveredEmailDraft(email="a-only@example.com", source="a", confidence=0.6),
            ]
        ),
    )
    b: EmailSource = _FakeProvider(
        "b",
        DiscoveryResult(
            emails=[
                DiscoveredEmailDraft(email="dup@example.com", source="b", confidence=0.9),
                DiscoveredEmailDraft(email="b-only@example.com", source="b", confidence=0.5),
            ]
        ),
    )

    await aggregator.run(run_id, providers=[a, b])

    async with SessionLocal() as session:
        rows = (await session.execute(select(DiscoveredEmail).where(DiscoveredEmail.run_id == run_id))).scalars().all()

        assert len(rows) == 3, f"expected 3 deduped rows, got {[(r.email, r.source) for r in rows]}"

        by_lowered = {r.email.lower(): r for r in rows}
        assert set(by_lowered.keys()) == {
            "dup@example.com",
            "a-only@example.com",
            "b-only@example.com",
        }

        dup = by_lowered["dup@example.com"]
        assert dup.source == "b", f"higher-confidence provider should win, got source={dup.source}"
        assert dup.confidence == 0.9, f"surviving confidence should be unchanged 0.9, got {dup.confidence}"

        a_only = by_lowered["a-only@example.com"]
        assert a_only.source == "a"
        assert a_only.confidence == 0.6

        b_only = by_lowered["b-only@example.com"]
        assert b_only.source == "b"
        assert b_only.confidence == 0.5


async def test_dedup_tie_keeps_one_row_with_undefined_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confidence-tie behaviour: exactly one row survives; winner is non-deterministic.

    The aggregator's tie-break is `draft.confidence > existing.confidence` (strict >),
    so the first-seen draft wins. "First-seen" is governed by task completion order
    inside `anyio.create_task_group`, which is not contractually deterministic.
    Asserts only the count and that source ∈ {a, b} — see
    reports/aggregator-dedup-audit-2026-04-20.md for the full reasoning.
    """
    _stub_verification(monkeypatch)
    run_id = await _new_scan()

    a: EmailSource = _FakeProvider(
        "a",
        DiscoveryResult(emails=[DiscoveredEmailDraft(email="tied@example.com", source="a", confidence=0.8)]),
    )
    b: EmailSource = _FakeProvider(
        "b",
        DiscoveryResult(emails=[DiscoveredEmailDraft(email="tied@example.com", source="b", confidence=0.8)]),
    )

    await aggregator.run(run_id, providers=[a, b])

    async with SessionLocal() as session:
        rows = (await session.execute(select(DiscoveredEmail).where(DiscoveredEmail.run_id == run_id))).scalars().all()

        assert len(rows) == 1, f"expected exactly 1 deduped row, got {[(r.email, r.source) for r in rows]}"
        assert rows[0].email.lower() == "tied@example.com"
        assert rows[0].source in {"a", "b"}
        assert rows[0].confidence == 0.8


async def test_dedup_handles_none_confidence_against_float(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for the 2026-04-20 southloop.vc crash.

    theHarvester always emits confidence=None; site_crawler always emits a float.
    Before the fix, the comparison `None > 0.5` raised TypeError, killing the
    entire run mid-fan-out and leaving status='running' forever. After the fix,
    the float draft wins (None is treated as -inf) and the run completes.
    """
    _stub_verification(monkeypatch)
    run_id = await _new_scan()

    a: EmailSource = _FakeProvider(
        "a",
        DiscoveryResult(emails=[DiscoveredEmailDraft(email="dup@example.com", source="a", confidence=None)]),
    )
    b: EmailSource = _FakeProvider(
        "b",
        DiscoveryResult(emails=[DiscoveredEmailDraft(email="dup@example.com", source="b", confidence=0.5)]),
    )

    await aggregator.run(run_id, providers=[a, b])

    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, run_id)
        assert scan is not None
        assert scan.status == RunStatus.completed.value

        rows = (await session.execute(select(DiscoveredEmail).where(DiscoveredEmail.run_id == run_id))).scalars().all()
        assert len(rows) == 1
        assert rows[0].source == "b"
        assert rows[0].confidence == 0.5


async def test_dedup_handles_none_confidence_both_sides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two providers both emitting confidence=None for the same email must not crash.

    Tie-break remains first-seen (same as the PR #16 tie case). The surviving
    row's confidence is None — we intentionally don't coerce to 0 on the way
    into the DB because the DiscoveredEmail column permits None.
    """
    _stub_verification(monkeypatch)
    run_id = await _new_scan()

    a: EmailSource = _FakeProvider(
        "a",
        DiscoveryResult(emails=[DiscoveredEmailDraft(email="dup@example.com", source="a", confidence=None)]),
    )
    b: EmailSource = _FakeProvider(
        "b",
        DiscoveryResult(emails=[DiscoveredEmailDraft(email="dup@example.com", source="b", confidence=None)]),
    )

    await aggregator.run(run_id, providers=[a, b])

    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, run_id)
        assert scan is not None
        assert scan.status == RunStatus.completed.value

        rows = (await session.execute(select(DiscoveredEmail).where(DiscoveredEmail.run_id == run_id))).scalars().all()
        assert len(rows) == 1
        assert rows[0].source in {"a", "b"}
        assert rows[0].confidence is None


async def test_aggregator_marks_row_failed_on_unhandled_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any uncaught exception in the aggregator body must mark the run failed.

    Before the crash-safety wrapper, a crash in _fan_out / _persist_drafts
    would propagate out of aggregator.run (a FastAPI BackgroundTask), be
    silently logged by starlette, and leave status='running' forever. The
    wrapper now writes a terminal `failed` row with a populated error_message
    so the frontend stops polling. The original exception is re-raised for
    log visibility.
    """
    _stub_verification(monkeypatch)
    run_id = await _new_scan()

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(aggregator, "_fan_out", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        await aggregator.run(run_id, providers=[_FakeProvider("x", DiscoveryResult())])

    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, run_id)
        assert scan is not None
        assert scan.status == RunStatus.failed.value
        assert scan.completed_at is not None
        assert scan.error_message is not None
        assert "aggregator crash:" in scan.error_message
        assert "boom" in scan.error_message


def test_default_providers_includes_all_four_sources() -> None:
    """Regression: every provider class shipped under services/email_extractor/
    must be wired into the production fan-out. Set-equality (not subset) so
    accidental additions / removals also fail and force an explicit test
    update — the safeguard against the "wired but not mounted" bug pattern
    that would have hidden Snov from production scans had aggregator.py's
    PR #10 edit been omitted (caught by the 1500 VPS smoke as a stale image).
    """
    names = {type(p).__name__ for p in aggregator.default_providers()}
    assert names == {"SiteCrawler", "Hunter", "TheHarvester", "Snov"}


async def test_provider_error_gets_single_provider_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR 0002: provider emits bare error; aggregator wraps with exactly one '<name>: ' prefix."""
    _stub_verification(monkeypatch)
    run_id = await _new_scan()

    provider: EmailSource = _FakeProvider(
        "fake",
        DiscoveryResult(emails=[], errors=["boom"]),
    )

    await aggregator.run(run_id, providers=[provider])

    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, run_id)
        assert scan is not None
        assert scan.error_message is not None
        assert "fake: boom" in scan.error_message
        assert "fake: fake:" not in scan.error_message


async def test_multiple_providers_get_independent_prefixes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each provider's errors are prefixed with its own name, exactly once."""
    _stub_verification(monkeypatch)
    run_id = await _new_scan()

    providers: list[EmailSource] = [
        _FakeProvider("alpha", DiscoveryResult(emails=[], errors=["one"])),
        _FakeProvider("beta", DiscoveryResult(emails=[], errors=["two"])),
    ]

    await aggregator.run(run_id, providers=providers)

    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, run_id)
        assert scan is not None
        assert scan.error_message is not None
        assert "alpha: one" in scan.error_message
        assert "beta: two" in scan.error_message
        assert "alpha: alpha:" not in scan.error_message
        assert "beta: beta:" not in scan.error_message
        assert "alpha: beta:" not in scan.error_message
        assert "beta: alpha:" not in scan.error_message
