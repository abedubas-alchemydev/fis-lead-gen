"""Unit tests for ``scripts/refresh_registration_dates.py``.

The script re-fetches the FINRA Form BD for EXISTING broker-dealers and
UPDATEs ``registration_date`` / ``formation_date`` in place. What's worth
locking without a real Postgres:

  * **Selection ordering** — NULL-``registration_date`` firms first, then
    never-checked, then oldest-checked (the rolling cursor). Asserted by
    pinning the exact ``ORDER BY`` in the selection SQL.
  * **UPDATE-only + COALESCE** — a parse miss still stamps
    ``registration_checked_at`` (cursor advances) but must NOT null an
    existing date; a parsed date lands. Asserted by capturing the params
    bound to the UPDATE and pinning the COALESCE-guarded SQL.
  * **Never INSERTs** — no statement the script executes is an INSERT.

DB I/O is faked with a recording async-engine stand-in (the
``test_embed_backfill_scripts.py`` ``_FakeEngine`` pattern). One test
drives the real fetch path through ``respx`` to prove the FINRA JSON +
PDF wiring is exercised, not just ``_probe_dates`` patched out; the
verbatim-copied PDF-text parser is stubbed there (it shares lineage with
the extractor's and the in-tree ``brokercheck_pdf`` parser).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import respx

# The script lives at the repo root (shipped into the backend image at
# /app/scripts). pytest runs from backend/ with pythonpath=., so the repo
# root must be added before ``scripts.*`` resolves.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import refresh_registration_dates as refresh  # noqa: E402

_FAKE_DB_URL = "postgresql://unit:test@127.0.0.1:9/never_connects"


# ── recording fake async-engine ─────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    def all(self) -> list[Any]:
        return self._rows


class _RecordingConn:
    """Records every (statement, params) and returns canned SELECT rows."""

    def __init__(self, engine: "_FakeEngine") -> None:
        self._engine = engine

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        sql = str(stmt)
        self._engine.executed.append((sql, params))
        if sql.lstrip().upper().startswith("SELECT"):
            return _FakeResult(self._engine.select_rows)
        return _FakeResult([])


class _ConnCtx:
    def __init__(self, engine: "_FakeEngine") -> None:
        self._engine = engine

    async def __aenter__(self) -> _RecordingConn:
        return _RecordingConn(self._engine)

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _FakeEngine:
    """connect()/begin() hand out conns sharing one execution journal.

    ``select_rows`` is what the SELECT returns; ``executed`` captures
    every statement so tests can assert UPDATE params and prove no INSERT.
    """

    def __init__(self, select_rows: list[Any]) -> None:
        self.select_rows = select_rows
        self.executed: list[tuple[str, Any]] = []
        self.disposed = False

    def connect(self) -> _ConnCtx:
        return _ConnCtx(self)

    def begin(self) -> _ConnCtx:
        return _ConnCtx(self)

    async def dispose(self) -> None:
        self.disposed = True


def _install_engine(monkeypatch, engine: _FakeEngine) -> None:
    monkeypatch.setattr(
        refresh, "create_async_engine", lambda *_a, **_kw: engine
    )


def _no_sleep(monkeypatch) -> None:
    async def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(refresh.asyncio, "sleep", _sleep)


def _updates(engine: _FakeEngine) -> list[tuple[str, Any]]:
    return [
        (sql, params)
        for sql, params in engine.executed
        if sql.lstrip().upper().startswith("UPDATE")
    ]


# ── selection ordering / prioritization ─────────────────────────────────


def test_selection_sql_prioritizes_null_date_then_oldest_checked() -> None:
    """The rolling cursor: NULL registration_date first, then
    never-checked (NULLS FIRST), then least-recently-checked."""
    sql = " ".join(str(refresh.SELECT_TARGETS_SQL).split())

    # Only numeric CRDs are eligible (the per-CRD probe needs an int).
    assert "crd_number ~ '^[0-9]+$'" in sql
    # NULL-date firms sort ahead: ``(registration_date IS NOT NULL)`` puts
    # FALSE (0, has-no-date) before TRUE (1, has-a-date).
    order_clause = sql[sql.upper().index("ORDER BY"):]
    assert "(registration_date IS NOT NULL)" in order_clause
    # Never-refreshed firms next, then oldest, ahead of the id tiebreak.
    i_null = order_clause.index("(registration_date IS NOT NULL)")
    i_nulls_first = order_clause.upper().index("REGISTRATION_CHECKED_AT NULLS FIRST")
    i_asc = order_clause.upper().index("REGISTRATION_CHECKED_AT ASC")
    i_id = order_clause.upper().rindex("ID ASC")
    assert i_null < i_nulls_first < i_asc < i_id
    # Batch is capped.
    assert "LIMIT :limit" in sql


async def test_limit_is_passed_to_selection_query(monkeypatch) -> None:
    engine = _FakeEngine(select_rows=[])  # empty → early return after SELECT
    _install_engine(monkeypatch, engine)

    rc = await refresh.main(["--db-url", _FAKE_DB_URL, "--limit", "37"])

    assert rc == 0
    select_calls = [
        params for sql, params in engine.executed
        if sql.lstrip().upper().startswith("SELECT")
    ]
    assert select_calls == [{"limit": 37}]
    assert engine.disposed


# ── UPDATE-only + COALESCE behavior ─────────────────────────────────────


def test_update_sql_is_coalesce_guarded_and_stamps_checked_at() -> None:
    """Pin the COALESCE shape: dates only overwrite when a new value is
    present, and registration_checked_at is always stamped."""
    sql = " ".join(str(refresh.UPDATE_DATES_SQL).split())
    assert "UPDATE broker_dealers SET" in sql
    assert "registration_date = COALESCE(:new_reg, registration_date)" in sql
    assert (
        "formation_date = COALESCE(:new_formation, formation_date)" in sql
    )
    assert "registration_checked_at = now()" in sql
    assert "WHERE id = :id" in sql


async def test_apply_parsed_date_lands_in_update_params(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    engine = _FakeEngine(select_rows=[(101, "12345")])
    _install_engine(monkeypatch, engine)

    async def _hit(_client: Any, _crd: int) -> refresh.RefreshResult:
        return refresh.RefreshResult(
            registration_date=date(2026, 6, 1),
            formation_date=date(2020, 1, 15),
        )

    monkeypatch.setattr(refresh, "_probe_dates", _hit)

    rc = await refresh.main(["--db-url", _FAKE_DB_URL, "--apply"])

    assert rc == 0
    updates = _updates(engine)
    assert len(updates) == 1
    _sql, params = updates[0]
    assert params == {
        "id": 101,
        "new_reg": date(2026, 6, 1),
        "new_formation": date(2020, 1, 15),
    }


async def test_apply_parse_miss_stamps_checked_at_without_nulling_date(
    monkeypatch,
) -> None:
    """A parse miss (no PDF / no dates) must still UPDATE — so
    registration_checked_at advances — but bind new_reg/new_formation as
    None, which COALESCE keeps as the existing column value. The cursor
    moves; the date is never nulled."""
    _no_sleep(monkeypatch)
    engine = _FakeEngine(select_rows=[(202, "67890")])
    _install_engine(monkeypatch, engine)

    async def _miss(_client: Any, _crd: int) -> None:
        return None

    monkeypatch.setattr(refresh, "_probe_dates", _miss)

    rc = await refresh.main(["--db-url", _FAKE_DB_URL, "--apply"])

    assert rc == 0
    updates = _updates(engine)
    # The UPDATE still fired (cursor advances on a miss)…
    assert len(updates) == 1
    _sql, params = updates[0]
    # …but with NULL date binds, so COALESCE preserves the existing dates.
    assert params == {"id": 202, "new_reg": None, "new_formation": None}


async def test_dry_run_does_not_update(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    engine = _FakeEngine(select_rows=[(303, "11111")])
    _install_engine(monkeypatch, engine)

    async def _hit(_client: Any, _crd: int) -> refresh.RefreshResult:
        return refresh.RefreshResult(
            registration_date=date(2026, 6, 2), formation_date=None
        )

    monkeypatch.setattr(refresh, "_probe_dates", _hit)

    rc = await refresh.main(["--db-url", _FAKE_DB_URL])  # no --apply

    assert rc == 0
    assert _updates(engine) == []  # dry run writes nothing
    # Only the SELECT ran.
    assert len(engine.executed) == 1
    assert engine.executed[0][0].lstrip().upper().startswith("SELECT")


# ── never INSERTs ───────────────────────────────────────────────────────


async def test_apply_never_executes_an_insert(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    engine = _FakeEngine(
        select_rows=[(1, "100"), (2, "200"), (3, "300")]
    )
    _install_engine(monkeypatch, engine)

    async def _mixed(_client: Any, crd: int) -> refresh.RefreshResult | None:
        # One hit, one date-less hit, one outright miss — still no INSERT.
        if crd == 100:
            return refresh.RefreshResult(
                registration_date=date(2026, 6, 3), formation_date=None
            )
        if crd == 200:
            return refresh.RefreshResult(
                registration_date=None, formation_date=None
            )
        return None

    monkeypatch.setattr(refresh, "_probe_dates", _mixed)

    rc = await refresh.main(["--db-url", _FAKE_DB_URL, "--apply"])

    assert rc == 0
    assert all(
        not sql.lstrip().upper().startswith("INSERT")
        for sql, _params in engine.executed
    ), "refresh must be UPDATE-only — it executed an INSERT"
    # All three firms got an UPDATE (every touched firm stamps the cursor).
    assert len(_updates(engine)) == 3


async def test_summary_counts_hits_and_misses(monkeypatch, caplog) -> None:
    _no_sleep(monkeypatch)
    engine = _FakeEngine(select_rows=[(1, "100"), (2, "200")])
    _install_engine(monkeypatch, engine)

    async def _mixed(_client: Any, crd: int) -> refresh.RefreshResult | None:
        if crd == 100:
            return refresh.RefreshResult(
                registration_date=date(2026, 6, 3), formation_date=None
            )
        return None  # parse miss

    monkeypatch.setattr(refresh, "_probe_dates", _mixed)

    with caplog.at_level("INFO"):
        rc = await refresh.main(["--db-url", _FAKE_DB_URL, "--apply"])

    assert rc == 0
    assert (
        "scanned=2 dates_updated=1 checked_stamped=2 parse_misses=1"
        in caplog.text
    )


# ── guards ──────────────────────────────────────────────────────────────


async def test_missing_db_url_exits_two(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    rc = await refresh.main([])
    assert rc == 2


async def test_nonpositive_limit_exits_two(monkeypatch) -> None:
    rc = await refresh.main(["--db-url", _FAKE_DB_URL, "--limit", "0"])
    assert rc == 2


# ── real fetch path through respx ───────────────────────────────────────


@respx.mock
async def test_probe_dates_runs_real_finra_fetch_path(monkeypatch) -> None:
    """Exercise the copied FINRA JSON + PDF fetchers end-to-end via respx
    so the real ``_probe_dates`` wiring is covered (only the verbatim
    PDF-text parser is stubbed). A parsed date must land in the UPDATE."""
    _no_sleep(monkeypatch)

    crd = 424242
    # FINRA firm-search envelope: hits[0]._source.content is a JSON string.
    finra_content = '{"basicInformation": {"firmName": "Acme Securities LLC"}}'
    respx.get(refresh.FINRA_SEARCH_URL.format(crd=crd)).mock(
        return_value=httpx.Response(
            200,
            json={"hits": {"hits": [{"_source": {"content": finra_content}}]}},
        )
    )
    respx.get(refresh.FINRA_PDF_URL.format(crd=crd)).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.4 minimal",
        )
    )
    # The PDF-text extractor is copied verbatim from the extractor / in-tree
    # parser; stub it so we assert the fetch wiring, not pdfplumber.
    monkeypatch.setattr(
        refresh,
        "_extract_dates_from_pdf",
        lambda _b: (date(2026, 6, 10), date(2019, 3, 4)),
    )

    engine = _FakeEngine(select_rows=[(777, str(crd))])
    _install_engine(monkeypatch, engine)

    rc = await refresh.main(["--db-url", _FAKE_DB_URL, "--apply"])

    assert rc == 0
    updates = _updates(engine)
    assert len(updates) == 1
    _sql, params = updates[0]
    assert params == {
        "id": 777,
        "new_reg": date(2026, 6, 10),
        "new_formation": date(2019, 3, 4),
    }
