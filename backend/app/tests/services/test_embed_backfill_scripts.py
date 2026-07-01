"""Unit tests for the Doxie embed-freshness scripts.

Covers the two repo-root scripts that keep ``chatbot_firm_embedding``
current without waiting for the populate-all pipeline:

- ``scripts/run_embed_backfill.py`` — ``--entity`` filtering and the
  exit-code contract (0 unless every selected backfill hard-fails;
  2 for config errors).
- ``scripts/standalone_extract_new_bds.py`` — the post-apply Doxie
  freshness hook: fires exactly once after the net-new rows are upserted,
  never on dry runs, and an embedding blow-up never leaks into the
  extractor's exit code.

Everything external is monkeypatched (service methods, sessions, the
extractor's enumerate-and-diff pipeline) — no real Postgres, no FINRA,
no Gemini.
Follows ``test_chatbot_semantic_unit.py`` for the session/service fakes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# The scripts live at the repo root (shipped into the backend image at
# /app/scripts). pytest runs from backend/ with pythonpath=., so the repo
# root must be added before ``scripts.*`` resolves.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import run_embed_backfill as runner  # noqa: E402
from scripts import standalone_extract_new_bds as extractor  # noqa: E402

import app.db.session as app_db_session  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services.chatbot_semantic import (  # noqa: E402
    BackfillResult,
    ChatbotSemanticService,
)
from app.services.broker_dealers import BrokerDealerRepository  # noqa: E402
from app.services.data_merge import BrokerDealerMergeService  # noqa: E402
from app.services.edgar import EdgarService  # noqa: E402
from app.services.finra import FinraService  # noqa: E402
from app.services.service_models import FinraBrokerDealerRecord  # noqa: E402

# Engine creation is lazy in SQLAlchemy — nothing ever connects to this.
_FAKE_DB_URL = "postgresql://unit:test@127.0.0.1:9/never_connects"


class _FakeSessionLocal:
    """Counting async-sessionmaker stand-in (test_chatbot_semantic_unit
    pattern) — proves short-circuit paths never opened a session."""

    def __init__(self) -> None:
        self.opened = 0

    def __call__(self) -> "_FakeSessionLocal":
        self.opened += 1
        return self

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


def _patch_service(
    monkeypatch,
    *,
    bd: Any = None,
    ia: Any = None,
) -> tuple[AsyncMock, AsyncMock, list[str]]:
    """Replace both backfill methods on the service class.

    ``bd`` / ``ia`` are either a BackfillResult to return or an Exception
    to raise. Returns the two mocks plus a call-order journal.
    """
    calls: list[str] = []

    def _side_effect(name: str, outcome: Any):
        async def _run(_db: Any):
            calls.append(name)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return _run

    bd_mock = AsyncMock(
        side_effect=_side_effect("bd", bd or BackfillResult(1, 0, 0))
    )
    ia_mock = AsyncMock(
        side_effect=_side_effect("ia", ia or BackfillResult(1, 0, 0))
    )
    monkeypatch.setattr(
        ChatbotSemanticService, "backfill_broker_dealers", bd_mock
    )
    monkeypatch.setattr(
        ChatbotSemanticService, "backfill_investment_advisors", ia_mock
    )
    return bd_mock, ia_mock, calls


# ── run_embed_backfill.py — entity filtering ────────────────────────────


async def test_runner_default_runs_bd_then_ia(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTEST")
    bd_mock, ia_mock, calls = _patch_service(
        monkeypatch, bd=BackfillResult(3, 1, 0), ia=BackfillResult(2, 0, 0)
    )

    rc = await runner.main(["--db-url", _FAKE_DB_URL])

    assert rc == 0
    assert bd_mock.await_count == 1
    assert ia_mock.await_count == 1
    assert calls == ["bd", "ia"]  # BDs first, then IAs.


async def test_runner_entity_broker_dealer_only(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTEST")
    bd_mock, ia_mock, _ = _patch_service(monkeypatch)

    rc = await runner.main(
        ["--db-url", _FAKE_DB_URL, "--entity", "broker_dealer"]
    )

    assert rc == 0
    assert bd_mock.await_count == 1
    assert ia_mock.await_count == 0


async def test_runner_entity_investment_advisor_only(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTEST")
    bd_mock, ia_mock, _ = _patch_service(monkeypatch)

    rc = await runner.main(
        ["--db-url", _FAKE_DB_URL, "--entity", "investment_advisor"]
    )

    assert rc == 0
    assert bd_mock.await_count == 0
    assert ia_mock.await_count == 1


# ── run_embed_backfill.py — exit codes ──────────────────────────────────


async def test_runner_partial_hard_failure_exits_zero(monkeypatch) -> None:
    """One entity hard-failing is logged but exits 0 — the next run
    self-heals via hash-skip. The other entity must still run."""
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTEST")
    _, ia_mock, _ = _patch_service(
        monkeypatch, bd=RuntimeError("gemini down")
    )

    rc = await runner.main(["--db-url", _FAKE_DB_URL])

    assert rc == 0
    assert ia_mock.await_count == 1  # BD's failure didn't block IA.


async def test_runner_all_entities_hard_failing_exits_nonzero(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTEST")
    _patch_service(
        monkeypatch,
        bd=RuntimeError("gemini down"),
        ia=RuntimeError("gemini down"),
    )

    rc = await runner.main(["--db-url", _FAKE_DB_URL])

    assert rc == 1


async def test_runner_single_selected_entity_failing_exits_nonzero(
    monkeypatch,
) -> None:
    """With --entity narrowing to one backfill, that backfill failing
    means *every* selected backfill failed — nonzero."""
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTEST")
    _patch_service(monkeypatch, bd=RuntimeError("gemini down"))

    rc = await runner.main(
        ["--db-url", _FAKE_DB_URL, "--entity", "broker_dealer"]
    )

    assert rc == 1


async def test_runner_row_level_failures_keep_exit_zero(monkeypatch) -> None:
    """BackfillResult.failed > 0 is a partial failure (logged), not a
    hard failure — exit stays 0 per the self-healing contract."""
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTEST")
    _patch_service(
        monkeypatch,
        bd=BackfillResult(0, 10, 7),
        ia=BackfillResult(0, 3, 2),
    )

    rc = await runner.main(["--db-url", _FAKE_DB_URL])

    assert rc == 0


async def test_runner_missing_db_url_exits_two(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    bd_mock, ia_mock, _ = _patch_service(monkeypatch)

    rc = await runner.main([])

    assert rc == 2
    assert bd_mock.await_count == 0
    assert ia_mock.await_count == 0


async def test_runner_missing_gemini_key_exits_two(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", None)
    bd_mock, ia_mock, _ = _patch_service(monkeypatch)

    rc = await runner.main(["--db-url", _FAKE_DB_URL])

    assert rc == 2
    assert bd_mock.await_count == 0
    assert ia_mock.await_count == 0


# ── standalone_extract_new_bds.py — post-apply embed hook ───────────────


class _FakeResult:
    """Stands in for the result of the existing-CRD SELECT."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    """async-session stand-in: every execute() returns the canned existing-CRD
    rows. main() runs one SELECT through a session before the write path, and
    the write path itself (upsert_many) is mocked."""

    def __init__(self, existing_rows: list[Any]) -> None:
        self._existing_rows = existing_rows
        self.executed = 0

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def execute(self, *_a: Any, **_kw: Any) -> _FakeResult:
        self.executed += 1
        return _FakeResult(self._existing_rows)


class _FakeSessionMaker:
    """Stand-in for ``async_sessionmaker(engine)``: each call opens a fresh
    fake session over the same canned existing-CRD rows."""

    def __init__(self, existing_rows: list[Any]) -> None:
        self._existing_rows = existing_rows
        self.opened = 0

    def __call__(self) -> _FakeSession:
        self.opened += 1
        return _FakeSession(self._existing_rows)


class _FakeEngine:
    """Async-engine stand-in — main() only needs create + dispose; the real
    work goes through the session-maker and the mocked services."""

    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _FakeReport:
    """Minimal MergeQAReport stand-in — main() only calls summary_lines()."""

    def summary_lines(self) -> list[str]:
        return ["(merge QA report stubbed for unit test)"]


_EXTRACT_ARGS = ["--db-url", _FAKE_DB_URL]


def _finra_record(crd: str) -> FinraBrokerDealerRecord:
    """A minimal enumerated active-BD record — the shape
    ``FinraService.fetch_broker_dealers`` returns. Only the CRD matters to the
    diff; the rest satisfies the dry-run log line."""
    return FinraBrokerDealerRecord(
        crd_number=crd,
        name="Test Securities LLC",
        sec_file_number="8-99999",
        registration_status="Active",
        branch_count=1,
        address_city="New York",
        address_state="NY",
        business_type=None,
    )


def _patch_pipeline(
    monkeypatch,
    *,
    enumerated: list[FinraBrokerDealerRecord],
    existing_rows: list[Any],
    merged: list[Any],
) -> tuple[_FakeEngine, AsyncMock, AsyncMock]:
    """Wire the extractor's enumerate → diff → enrich → EDGAR → merge → upsert
    path with fakes (no HTTP, no Postgres). Returns the fake engine plus the
    enrich and upsert mocks so callers can assert what the write path saw. The
    embed hook is left for each test to handle (spy vs. real)."""
    engine = _FakeEngine()
    session_maker = _FakeSessionMaker(existing_rows)
    monkeypatch.setattr(extractor, "create_async_engine", lambda *_a, **_kw: engine)
    monkeypatch.setattr(
        extractor, "async_sessionmaker", lambda _engine, **_kw: session_maker
    )

    monkeypatch.setattr(
        FinraService,
        "fetch_broker_dealers",
        AsyncMock(return_value=enumerated),
    )
    enrich_mock = AsyncMock(side_effect=lambda recs, **_kw: recs)
    monkeypatch.setattr(FinraService, "enrich_with_detail", enrich_mock)
    monkeypatch.setattr(
        EdgarService,
        "fetch_records_for_sec_numbers",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        BrokerDealerMergeService,
        "merge",
        MagicMock(return_value=(merged, _FakeReport())),
    )
    upsert_mock = AsyncMock(return_value=len(merged))
    monkeypatch.setattr(BrokerDealerRepository, "upsert_many", upsert_mock)
    return engine, enrich_mock, upsert_mock


async def test_extractor_apply_triggers_embed_hook_exactly_once(
    monkeypatch,
) -> None:
    # One enumerated active BD, nothing in the DB → it's net-new → the apply
    # path enriches, merges, upserts, then fires the hook exactly once.
    engine, _enrich, upsert_mock = _patch_pipeline(
        monkeypatch,
        enumerated=[_finra_record("900001")],
        existing_rows=[],
        merged=[object()],
    )
    hook_spy = AsyncMock()
    monkeypatch.setattr(extractor, "_embed_backfill_after_apply", hook_spy)

    rc = await extractor.main([*_EXTRACT_ARGS, "--apply"])

    assert rc == 0
    assert hook_spy.await_count == 1
    assert upsert_mock.await_count == 1  # the upsert really ran before the hook
    assert engine.disposed


async def test_extractor_apply_commits_each_chunk_but_embeds_once(
    monkeypatch,
) -> None:
    """Durable progress: net-new firms spanning multiple chunks trigger one
    ``upsert_many`` (commit) per chunk, but the embed hook still fires exactly
    once — after the loop — so the "apply → embed once" contract holds even as
    each chunk is committed independently."""
    monkeypatch.setattr(extractor, "_NEW_BD_CHUNK_SIZE", 2)
    _engine, enrich_mock, upsert_mock = _patch_pipeline(
        monkeypatch,
        enumerated=[
            _finra_record("900001"),
            _finra_record("900002"),
            _finra_record("900003"),
        ],
        existing_rows=[],
        merged=[object()],
    )
    hook_spy = AsyncMock()
    monkeypatch.setattr(extractor, "_embed_backfill_after_apply", hook_spy)

    rc = await extractor.main([*_EXTRACT_ARGS, "--apply"])

    assert rc == 0
    # 3 firms / chunk size 2 → 2 chunks → a commit + an enrichment per chunk.
    assert upsert_mock.await_count == 2
    assert enrich_mock.await_count == 2
    # ...but the embed hook still fires exactly once, after the loop.
    assert hook_spy.await_count == 1


async def test_extractor_dry_run_does_not_trigger_embed_hook(
    monkeypatch,
) -> None:
    _engine, enrich_mock, upsert_mock = _patch_pipeline(
        monkeypatch,
        enumerated=[_finra_record("900001")],
        existing_rows=[],
        merged=[object()],
    )
    hook_spy = AsyncMock()
    monkeypatch.setattr(extractor, "_embed_backfill_after_apply", hook_spy)

    rc = await extractor.main(_EXTRACT_ARGS)  # no --apply

    assert rc == 0
    assert hook_spy.await_count == 0
    assert enrich_mock.await_count == 0  # dry run stops at the diff
    assert upsert_mock.await_count == 0  # nothing written on a dry run


async def test_extractor_apply_without_inserts_skips_embed_hook(
    monkeypatch,
) -> None:
    """--apply on a night when every active BD is already in the DB yields zero
    net-new and returns before the hook — no embed work when FINRA registered
    nothing new."""
    _engine, enrich_mock, upsert_mock = _patch_pipeline(
        monkeypatch,
        enumerated=[_finra_record("900001")],
        existing_rows=[("900001",)],  # the only enumerated firm already exists
        merged=[object()],
    )
    hook_spy = AsyncMock()
    monkeypatch.setattr(extractor, "_embed_backfill_after_apply", hook_spy)

    rc = await extractor.main([*_EXTRACT_ARGS, "--apply"])

    assert rc == 0
    assert hook_spy.await_count == 0
    assert enrich_mock.await_count == 0  # zero net-new → no enrich/merge/upsert
    assert upsert_mock.await_count == 0


async def test_extractor_embed_failure_does_not_change_exit_code(
    monkeypatch, caplog
) -> None:
    """End-to-end isolation: the real hook runs, the embedding service
    blows up, and the extractor still exits 0 with its rows upserted."""
    _engine, _enrich, upsert_mock = _patch_pipeline(
        monkeypatch,
        enumerated=[_finra_record("900001")],
        existing_rows=[],
        merged=[object()],
    )
    # Arm the real hook's lazy-imported internals: key present, session fake,
    # service raising.
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTEST")
    monkeypatch.setattr(app_db_session, "SessionLocal", _FakeSessionLocal())
    monkeypatch.setattr(
        ChatbotSemanticService,
        "backfill_broker_dealers",
        AsyncMock(side_effect=RuntimeError("gemini down")),
    )

    rc = await extractor.main([*_EXTRACT_ARGS, "--apply"])

    assert rc == 0
    assert upsert_mock.await_count == 1  # the committed upsert was not undone
    assert "doxie embed backfill after extract failed" in caplog.text


async def test_embed_hook_skips_without_gemini_key(monkeypatch) -> None:
    """No Gemini key → log-and-return before any session is opened."""
    fake_sessions = _FakeSessionLocal()
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(app_db_session, "SessionLocal", fake_sessions)

    await extractor._embed_backfill_after_apply()

    assert fake_sessions.opened == 0


async def test_embed_hook_logs_recognizable_counts_line(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTEST")
    monkeypatch.setattr(app_db_session, "SessionLocal", _FakeSessionLocal())
    _patch_service(
        monkeypatch,
        bd=BackfillResult(4, 10, 0),
        ia=BackfillResult(2, 3, 1),
    )

    with caplog.at_level("INFO"):
        await extractor._embed_backfill_after_apply()

    assert (
        "doxie embed backfill after extract: bd_embedded=4 bd_skipped=10 "
        "bd_failed=0 ia_embedded=2 ia_skipped=3 ia_failed=1"
    ) in caplog.text
