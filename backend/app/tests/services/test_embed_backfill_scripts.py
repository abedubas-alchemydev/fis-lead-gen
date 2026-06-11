"""Unit tests for the Doxie embed-freshness scripts.

Covers the two repo-root scripts that keep ``chatbot_firm_embedding``
current without waiting for the populate-all pipeline:

- ``scripts/run_embed_backfill.py`` — ``--entity`` filtering and the
  exit-code contract (0 unless every selected backfill hard-fails;
  2 for config errors).
- ``scripts/standalone_extract_new_bds.py`` — the post-apply Doxie
  freshness hook: fires exactly once after rows are committed, never on
  dry runs, and an embedding blow-up never leaks into the extractor's
  exit code.

Everything external is monkeypatched (service methods, sessions, the
extractor's DB engine and FINRA probe) — no real Postgres, no Gemini.
Follows ``test_chatbot_semantic_unit.py`` for the session/service fakes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

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
    def __init__(
        self,
        *,
        rows: list[Any] | None = None,
        scalar: Any = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one(self) -> Any:
        return self._scalar


class _FakeConn:
    """Pops canned results off the engine's shared queue, in order."""

    def __init__(self, engine: "_FakeEngine") -> None:
        self._engine = engine

    async def execute(self, _stmt: Any, _params: Any = None) -> _FakeResult:
        self._engine.executed += 1
        return self._engine.results.pop(0)


class _FakeConnCtx:
    def __init__(self, engine: "_FakeEngine") -> None:
        self._engine = engine

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._engine)

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _FakeEngine:
    """Async-engine stand-in: connect()/begin() hand out conns that share
    one ordered result queue — enough to drive the extractor's main()."""

    def __init__(self, results: list[_FakeResult]) -> None:
        self.results = list(results)
        self.executed = 0
        self.disposed = False

    def connect(self) -> _FakeConnCtx:
        return _FakeConnCtx(self)

    def begin(self) -> _FakeConnCtx:
        return _FakeConnCtx(self)

    async def dispose(self) -> None:
        self.disposed = True


_PROBE_ARGS = [
    "--db-url",
    _FAKE_DB_URL,
    "--crd-start",
    "900001",
    "--probe-limit",
    "1",
]


def _hit_probe(monkeypatch) -> None:
    """Make the (single) probed CRD resolve to a fake new BD — no HTTP."""

    async def _fake_probe(_client: Any, crd: int) -> extractor.NewBd:
        return extractor.NewBd(
            crd_number=str(crd),
            name="Test Securities LLC",
            sec_file_number="8-99999",
            city="New York",
            state="NY",
            status="Active",
            registration_date=None,
            formation_date=None,
        )

    monkeypatch.setattr(extractor, "_probe_one", _fake_probe)


async def test_extractor_apply_triggers_embed_hook_exactly_once(
    monkeypatch,
) -> None:
    _hit_probe(monkeypatch)
    # Queue order: dedup SELECT (no existing rows) → INSERT RETURNING id.
    engine = _FakeEngine(
        [_FakeResult(rows=[]), _FakeResult(scalar=123)]
    )
    monkeypatch.setattr(
        extractor, "create_async_engine", lambda *_a, **_kw: engine
    )
    hook_spy = AsyncMock()
    monkeypatch.setattr(extractor, "_embed_backfill_after_apply", hook_spy)

    rc = await extractor.main([*_PROBE_ARGS, "--apply"])

    assert rc == 0
    assert hook_spy.await_count == 1
    assert engine.executed == 2  # the INSERT really ran before the hook
    assert engine.disposed


async def test_extractor_dry_run_does_not_trigger_embed_hook(
    monkeypatch,
) -> None:
    _hit_probe(monkeypatch)
    engine = _FakeEngine([_FakeResult(rows=[])])  # dedup SELECT only
    monkeypatch.setattr(
        extractor, "create_async_engine", lambda *_a, **_kw: engine
    )
    hook_spy = AsyncMock()
    monkeypatch.setattr(extractor, "_embed_backfill_after_apply", hook_spy)

    rc = await extractor.main(_PROBE_ARGS)

    assert rc == 0
    assert hook_spy.await_count == 0
    assert engine.executed == 1  # nothing written on a dry run


async def test_extractor_apply_without_inserts_skips_embed_hook(
    monkeypatch,
) -> None:
    """--apply with zero finds returns before the hook — no embed work
    on nights when FINRA registered nothing new."""

    async def _miss_probe(_client: Any, _crd: int) -> None:
        return None

    monkeypatch.setattr(extractor, "_probe_one", _miss_probe)
    engine = _FakeEngine([])
    monkeypatch.setattr(
        extractor, "create_async_engine", lambda *_a, **_kw: engine
    )
    hook_spy = AsyncMock()
    monkeypatch.setattr(extractor, "_embed_backfill_after_apply", hook_spy)

    rc = await extractor.main([*_PROBE_ARGS, "--apply"])

    assert rc == 0
    assert hook_spy.await_count == 0


async def test_extractor_embed_failure_does_not_change_exit_code(
    monkeypatch, caplog
) -> None:
    """End-to-end isolation: the real hook runs, the embedding service
    blows up, and the extractor still exits 0 with its rows written."""
    _hit_probe(monkeypatch)
    engine = _FakeEngine(
        [_FakeResult(rows=[]), _FakeResult(scalar=123)]
    )
    monkeypatch.setattr(
        extractor, "create_async_engine", lambda *_a, **_kw: engine
    )
    # Arm the hook's lazy-imported internals: key present, session fake,
    # service raising.
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTEST")
    monkeypatch.setattr(app_db_session, "SessionLocal", _FakeSessionLocal())
    monkeypatch.setattr(
        ChatbotSemanticService,
        "backfill_broker_dealers",
        AsyncMock(side_effect=RuntimeError("gemini down")),
    )

    rc = await extractor.main([*_PROBE_ARGS, "--apply"])

    assert rc == 0
    assert engine.executed == 2  # the committed insert was not undone
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
