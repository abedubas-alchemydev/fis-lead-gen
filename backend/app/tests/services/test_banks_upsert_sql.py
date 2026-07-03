"""Write-side coverage for ``BankRepository`` (``services/banks.py``).

The read side is pinned by ``test_banks_repository.py``; these tests cover
the watcher's WRITE paths, which use PostgreSQL-only constructs
(``INSERT .. ON CONFLICT``, the forward-only rank ``CASE``) that nothing
else in the default suite compiles:

1. ``upsert_fdic_institutions`` — the statement must compile under the
   PostgreSQL dialect with the ``ON CONFLICT (fdic_cert) DO UPDATE`` target,
   the forward-only charter_status CASE, and the fdic/occ source merge; the
   batch must dedupe duplicate certs (institutions-window + history-
   corroboration union) so one statement can't touch a row twice.
2. ``upsert_occ_filings`` — new-bank creation from a filing group, Receipt →
   application_received_date, last_action_date accretion, forward-only
   status on existing rows, the NULL-action_date manual dedupe, and the
   ``ON CONFLICT ON CONSTRAINT uq_bank_application_events_bank_action_date
   DO NOTHING .. RETURNING`` event insert.

No live Postgres: statements are captured via a fake session and compiled
with ``sqlalchemy.dialects.postgresql.dialect()`` — the same approach as the
SQL-shape suites, extended to the insert paths.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.models.bank import Bank
from app.services.banks import BankRepository
from app.services.fdic_bankfind import FdicInstitutionRecord
from app.services.occ_cas import OccCharterFiling, OccNationalBankDirectoryRow

_PG = postgresql.dialect()


def _pg_sql(statement: object) -> str:
    return str(statement.compile(dialect=_PG)).lower()


# ── upsert_fdic_institutions ────────────────────────────────────────────────


class _CaptureSession:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, statement: object) -> object:
        self.statements.append(statement)
        return MagicMock()


def _fdic_record(cert: str = "59381", **overrides) -> FdicInstitutionRecord:
    defaults = dict(
        cert=cert,
        name="Portrait Bank",
        fed_rssd="9999999",
        established_date=date(2026, 6, 22),
        insured_date=date(2026, 6, 22),
        city="Miami",
        state="FL",
        charter_authority="STATE",
        regulator="FDIC",
        bkclass="NM",
        active=True,
    )
    defaults.update(overrides)
    return FdicInstitutionRecord(**defaults)


async def test_fdic_upsert_compiles_on_pg_with_conflict_and_forward_only_case() -> None:
    repo = BankRepository()
    session = _CaptureSession()
    written = await repo.upsert_fdic_institutions(session, [_fdic_record()])
    assert written == 1
    assert len(session.statements) == 1
    sql = _pg_sql(session.statements[0])

    # PG upsert with the unique-index conflict target the migration created.
    assert "insert into banks" in sql
    assert "on conflict (fdic_cert) do update set" in sql
    # Forward-only lifecycle: the charter_status assignment is a rank CASE
    # comparing incoming (excluded) vs stored, not a blind overwrite.
    assert "charter_status = case when (case excluded.charter_status" in sql
    assert "else banks.charter_status end" in sql
    # Source merge CASE: an fdic write over an 'occ' row must become
    # 'fdic+occ' (the merged value rides in as a bound param).
    assert "source = case when (banks.source in" in sql
    # FDIC-owned columns must coalesce so a sparse re-list can't null data.
    assert "coalesce(excluded.established_date, banks.established_date)" in sql

    params = session.statements[0].compile(dialect=_PG).params
    # The insert VALUES carry charter_status='opened' (an FDIC-certificated
    # institution necessarily opened) and the source-merge param is 'fdic+occ'.
    assert "opened" in params.values()
    assert "fdic+occ" in params.values()


async def test_fdic_upsert_dedupes_duplicate_certs_within_batch() -> None:
    """Institutions-window + history-corroboration union can carry the same
    cert twice; one ON CONFLICT statement must not touch a row twice
    (CardinalityViolation). Last record wins."""
    repo = BankRepository()
    session = _CaptureSession()
    written = await repo.upsert_fdic_institutions(
        session,
        [
            _fdic_record(name="Portrait Bank (stale)"),
            _fdic_record(name="Portrait Bank"),
        ],
    )
    assert written == 1
    params = session.statements[0].compile(dialect=_PG).params
    names = [v for v in params.values() if isinstance(v, str) and v.startswith("Portrait")]
    assert names == ["Portrait Bank"]


async def test_fdic_upsert_chunks_large_batches_under_bind_param_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full-directory sync (~4.3k rows × ~20 cols) exceeds Postgres's
    65535 bind-param ceiling in a single INSERT — the real failure the
    laptop apply hit. The upsert must split the batch across statements with
    every distinct cert still written. Force a tiny cap so a handful of rows
    spans multiple chunks."""
    monkeypatch.setattr("app.services.banks._PG_MAX_BIND_PARAMS", 40)
    repo = BankRepository()
    session = _CaptureSession()
    certs = [str(90000 + i) for i in range(5)]
    written = await repo.upsert_fdic_institutions(
        session, [_fdic_record(cert=c) for c in certs]
    )

    assert written == 5
    # Before the fix this was always exactly one statement (→ overflow at 4.3k).
    assert len(session.statements) > 1
    expected = set(certs)
    seen: set[str] = set()
    for stmt in session.statements:
        sql = _pg_sql(stmt)
        assert "insert into banks" in sql
        assert "on conflict (fdic_cert) do update" in sql
        params = stmt.compile(dialect=_PG).params
        # Each chunk carries only its slice of rows (one fdic_cert bind per
        # row), so rows × cols stays under the bind-param cap it was split for.
        rows_in_chunk = sum(1 for k in params if k.startswith("fdic_cert"))
        assert rows_in_chunk <= 2
        seen.update(v for v in params.values() if isinstance(v, str) and v in expected)
    # No row dropped at a chunk boundary — all five certs landed.
    assert seen == expected


async def test_fdic_upsert_empty_batch_is_a_noop() -> None:
    repo = BankRepository()
    session = _CaptureSession()
    assert await repo.upsert_fdic_institutions(session, []) == 0
    assert session.statements == []


# ── upsert_occ_filings ──────────────────────────────────────────────────────


class _FakeOccSession:
    """Routes the three statement shapes ``upsert_occ_filings`` issues:
    bank lookup by control number, NULL-date event existence check, and the
    event INSERT .. ON CONFLICT DO NOTHING RETURNING."""

    def __init__(self, existing_bank: Bank | None = None, null_date_event_exists: bool = False) -> None:
        self.statements: list[object] = []
        self.added: list[object] = []
        self._existing_bank = existing_bank
        self._null_exists = null_date_event_exists
        self._next_id = 100

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    async def execute(self, statement: object) -> object:
        self.statements.append(statement)
        sql = _pg_sql(statement)
        result = MagicMock()
        if sql.startswith("insert into bank_application_events"):
            result.fetchall.return_value = [(self._next_id,)]
        elif "from bank_application_events" in sql:
            result.first.return_value = ((1,) if self._null_exists else None)
        elif "from banks" in sql:
            result.scalar_one_or_none.return_value = self._existing_bank
        return result


def _filing(action: str, action_date: date | None, **overrides) -> OccCharterFiling:
    defaults = dict(
        control_number="2026-Charter-345612",
        bank_name="OpenReserve Bank, NA",
        action=action,
        action_date=action_date,
        filing_type="New Bank Charter",
        filing_type_id="2",
        filing_id="345612",
        filing_subtype_id="1093",
        city="Salt Lake City",
        state="UT",
    )
    defaults.update(overrides)
    return OccCharterFiling(**defaults)


async def test_occ_upsert_creates_pending_bank_and_accretes_actions() -> None:
    """A Receipt + Approved pair (given out of order) creates one bank that
    ends 'approved', with application_received_date = the Receipt date and
    last_action_date = the newest action date."""
    repo = BankRepository()
    session = _FakeOccSession()
    banks_touched, events_inserted = await repo.upsert_occ_filings(
        session,
        [
            _filing("Approved", date(2026, 6, 24)),  # deliberately first
            _filing("Receipt", date(2026, 4, 13)),
        ],
    )
    assert (banks_touched, events_inserted) == (1, 2)
    assert len(session.added) == 1
    bank = session.added[0]
    assert bank.occ_control_number == "2026-Charter-345612"
    assert bank.charter_authority == "OCC"
    assert bank.source == "occ"
    assert bank.charter_status == "approved"
    assert bank.application_received_date == date(2026, 4, 13)
    assert bank.last_action_date == date(2026, 6, 24)
    assert bank.occ_checked_at is not None

    event_inserts = [s for s in session.statements if _pg_sql(s).startswith("insert into bank_application_events")]
    assert len(event_inserts) == 2
    sql = _pg_sql(event_inserts[0])
    assert "on conflict on constraint uq_bank_application_events_bank_action_date do nothing" in sql
    assert "returning bank_application_events.id" in sql
    params = event_inserts[0].compile(dialect=_PG).params
    # Ordered oldest-first within the group: the Receipt event inserts first,
    # deep-linked to the official CAS details page.
    assert params["action"] == "Receipt"
    assert params["action_date"] == date(2026, 4, 13)
    assert params["source_url"] == (
        "https://apps.occ.gov/CAS/home/details"
        "?FilingTypeID=2&FilingID=345612&FilingSubtypeID=1093"
    )


async def test_occ_upsert_never_demotes_an_opened_bank() -> None:
    """A stale window re-seeing 'Receipt' after the bank opened must record
    the event but leave charter_status alone (forward-only lifecycle)."""
    existing = Bank(
        id=7,
        occ_control_number="2026-Charter-345612",
        name="OpenReserve Bank, NA",
        charter_status="opened",
        source="fdic",
        last_action_date=date(2026, 6, 30),
    )
    repo = BankRepository()
    session = _FakeOccSession(existing_bank=existing)
    banks_touched, events_inserted = await repo.upsert_occ_filings(
        session, [_filing("Receipt", date(2026, 4, 13))]
    )
    assert (banks_touched, events_inserted) == (1, 1)
    assert existing.charter_status == "opened"  # not demoted to pending
    assert existing.last_action_date == date(2026, 6, 30)  # older action can't regress it
    assert existing.application_received_date == date(2026, 4, 13)  # gap filled
    assert existing.source == "fdic+occ"  # occ evidence arrived on an fdic row


async def test_occ_upsert_fdic_name_wins_on_reconciled_rows() -> None:
    """A nightly OCC refresh of a row that has been reconciled with FDIC
    (has fdic_cert) must NOT overwrite the FDIC-authoritative legal name
    with the CAS spelling — FDIC name wins on reconciled rows."""
    existing = Bank(
        id=7,
        occ_control_number="2026-Charter-345612",
        fdic_cert="59500",
        name="OpenReserve Bank, National Association",  # FDIC legal name
        charter_status="opened",
        source="fdic+occ",
    )
    repo = BankRepository()
    session = _FakeOccSession(existing_bank=existing)
    await repo.upsert_occ_filings(
        session, [_filing("Approved", date(2026, 6, 24), bank_name="OpenReserve Bank, NA")]
    )
    assert existing.name == "OpenReserve Bank, National Association"
    # Non-name gap fill still applies (address arrived, row had none).
    assert existing.city == "Salt Lake City"


async def test_occ_upsert_still_refreshes_name_on_unreconciled_rows() -> None:
    """Before reconciliation (no fdic_cert) CAS is the only name source,
    so the refresh must keep applying."""
    existing = Bank(
        id=7,
        occ_control_number="2026-Charter-345612",
        fdic_cert=None,
        name="Openreserve (typo'd early spelling)",
        charter_status="pending",
        source="occ",
    )
    repo = BankRepository()
    session = _FakeOccSession(existing_bank=existing)
    await repo.upsert_occ_filings(
        session, [_filing("Approved", date(2026, 6, 24), bank_name="OpenReserve Bank, NA")]
    )
    assert existing.name == "OpenReserve Bank, NA"


async def test_occ_upsert_null_action_date_dedupes_manually() -> None:
    """NULL action_date bypasses the unique constraint (NULL != NULL in PG);
    the repository must dedupe those by an explicit existence check."""
    existing = Bank(
        id=7,
        occ_control_number="2026-Charter-345612",
        name="OpenReserve Bank, NA",
        charter_status="pending",
        source="occ",
    )
    repo = BankRepository()
    session = _FakeOccSession(existing_bank=existing, null_date_event_exists=True)
    banks_touched, events_inserted = await repo.upsert_occ_filings(
        session, [_filing("Receipt", None)]
    )
    assert (banks_touched, events_inserted) == (1, 0)
    assert not any(
        _pg_sql(s).startswith("insert into bank_application_events") for s in session.statements
    )


async def test_occ_upsert_empty_batch_is_a_noop() -> None:
    repo = BankRepository()
    session = _FakeOccSession()
    assert await repo.upsert_occ_filings(session, []) == (0, 0)
    assert session.statements == []


# ── merge_occ_bank_into_fdic_row ────────────────────────────────────────────


class _FakeMergeSession:
    """The three session verbs the merge path uses: execute (the moved-events
    select), flush (the two-step unique-key handover), delete (the OCC row)."""

    def __init__(self, events: list | None = None) -> None:
        self.statements: list[object] = []
        self.deleted: list[object] = []
        self.flush_count = 0
        self._events = events or []

    async def execute(self, statement: object) -> object:
        self.statements.append(statement)
        result = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = self._events
        result.scalars.return_value = scalars
        return result

    async def flush(self) -> None:
        self.flush_count += 1

    async def delete(self, obj: object) -> None:
        self.deleted.append(obj)


def _merge_pair() -> tuple[Bank, Bank]:
    occ_bank = Bank(
        id=2,
        occ_control_number="2026-Charter-345612",
        name="OpenReserve Bank, NA",
        charter_status="approved",
        source="occ",
        application_received_date=date(2026, 4, 13),
        last_action_date=date(2026, 6, 24),
        # Institutions-API enrichment stamped by the reconcile phase just
        # before the merge — it must survive onto the FDIC row.
        lei="549300OPENRSRV00LE00",
        charter_type="National",
    )
    fdic_bank = Bank(
        id=1,
        fdic_cert="59500",
        occ_control_number=None,
        name="OpenReserve Bank, National Association",
        charter_status="opened",
        source="fdic",
    )
    return occ_bank, fdic_bank


async def test_merge_moves_key_dates_and_events_onto_the_fdic_row() -> None:
    from app.models.bank import BankApplicationEvent

    occ_bank, fdic_bank = _merge_pair()
    event = BankApplicationEvent(id=10, bank_id=2, action="Receipt", action_date=date(2026, 4, 13))
    repo = BankRepository()
    session = _FakeMergeSession(events=[event])
    survivor = await repo.merge_occ_bank_into_fdic_row(session, occ_bank, fdic_bank)
    assert survivor is fdic_bank
    assert fdic_bank.occ_control_number == "2026-Charter-345612"
    assert fdic_bank.application_received_date == date(2026, 4, 13)
    assert fdic_bank.source == "fdic+occ"
    assert fdic_bank.lei == "549300OPENRSRV00LE00"
    assert fdic_bank.charter_type == "National"
    assert event.bank_id == 1  # events follow the surviving row
    assert session.deleted == [occ_bank]
    assert session.flush_count == 2  # release-then-take unique-key handover


async def test_merge_refuses_when_target_carries_a_different_control_number() -> None:
    """Defensive guard: folding an application onto a row that already
    belongs to ANOTHER application would cross-link two distinct filings.
    The merge must log + skip, writing nothing."""
    occ_bank, fdic_bank = _merge_pair()
    fdic_bank.occ_control_number = "2025-Charter-343355"  # a different filing
    repo = BankRepository()
    session = _FakeMergeSession()
    result = await repo.merge_occ_bank_into_fdic_row(session, occ_bank, fdic_bank)
    assert result is occ_bank  # the application row survives, untouched
    assert occ_bank.occ_control_number == "2026-Charter-345612"  # key NOT released
    assert fdic_bank.occ_control_number == "2025-Charter-343355"
    assert fdic_bank.source == "fdic"
    # No session activity at all: no flush, no event move, no delete.
    assert session.flush_count == 0
    assert session.statements == []
    assert session.deleted == []


# ── upsert_occ_institutions (full-directory OCC sync) ───────────────────────


class _FakeDirectorySession:
    """Routes the query shapes ``upsert_occ_institutions`` issues:

    - ``find_by_cert``                     → banks WHERE fdic_cert = ?
    - ``find_banks_by_occ_charter_number`` → banks WHERE occ_charter_number = ?
    - ``find_fdic_candidates_by_name_state`` → banks WHERE fdic_cert IS NOT NULL
    - ``merge_occ_bank_into_fdic_row``     → bank_application_events select + delete
    - the OCC-only insert                  → INSERT INTO banks .. ON CONFLICT

    plus the ``flush`` / ``delete`` verbs the merge path uses.
    """

    def __init__(
        self,
        *,
        cert_match: Bank | None = None,
        charter_rows: list[Bank] | None = None,
        name_state_rows: list[Bank] | None = None,
        events: list | None = None,
    ) -> None:
        self.statements: list[object] = []
        self.deleted: list[object] = []
        self.flush_count = 0
        self._cert_match = cert_match
        self._charter_rows = charter_rows or []
        self._name_state_rows = name_state_rows or []
        self._events = events or []

    async def flush(self) -> None:
        self.flush_count += 1

    async def delete(self, obj: object) -> None:
        self.deleted.append(obj)

    async def execute(self, statement: object) -> object:
        self.statements.append(statement)
        sql = _pg_sql(statement)
        result = MagicMock()
        if sql.startswith("insert into banks"):
            return result
        if "from bank_application_events" in sql:
            _set_scalars(result, self._events)
        elif "occ_charter_number =" in sql:
            _set_scalars(result, self._charter_rows)
        elif "fdic_cert is not null" in sql:
            _set_scalars(result, self._name_state_rows)
        elif "fdic_cert =" in sql:
            result.scalar_one_or_none.return_value = self._cert_match
        return result

    def bank_inserts(self) -> list[object]:
        return [s for s in self.statements if _pg_sql(s).startswith("insert into banks")]


def _set_scalars(result: MagicMock, rows: list) -> None:
    scalars = MagicMock()
    scalars.all.return_value = rows
    result.scalars.return_value = scalars


def _directory_row(**overrides) -> OccNationalBankDirectoryRow:
    defaults = dict(
        charter_number="25200",
        name="Protego Trust Bank, National Association",
        city="Seattle",
        state="WA",
        fdic_cert=None,
        fed_rssd="8888888",
        lei="5493001PROTEGO00LE00",
        charter_type="TrustCo-National",
    )
    defaults.update(overrides)
    return OccNationalBankDirectoryRow(**defaults)


async def test_occ_institutions_enriches_fdic_match_without_overwriting_non_nulls() -> None:
    """An insured national bank already inserted by the FDIC upsert is
    matched by cert and enriched in place: NULL gaps fill, an existing
    non-null (charter_type) is preserved, and charter_status never flips."""
    existing = Bank(
        id=1,
        fdic_cert="59512",
        occ_charter_number=None,
        fed_rssd=None,
        lei=None,
        charter_type="National",  # already set — must NOT be overwritten
        charter_status="opened",
        source="fdic",
        name="Anchorage Digital Bank, National Association",
    )
    repo = BankRepository()
    session = _FakeDirectorySession(cert_match=existing, charter_rows=[])
    row = _directory_row(
        charter_number="24316",
        fdic_cert="59512",
        name="Anchorage Digital Bank, N.A.",
        state="SD",
        charter_type="TrustCo-National",
    )
    applied = await repo.upsert_occ_institutions(session, [row])

    assert applied == 1
    assert session.bank_inserts() == []  # matched -> enrich, no INSERT
    assert existing.occ_charter_number == "24316"  # stamped into the NULL gap
    assert existing.fed_rssd == "8888888"
    assert existing.lei == "5493001PROTEGO00LE00"
    assert existing.charter_type == "National"  # existing non-null preserved
    assert existing.charter_status == "opened"  # lifecycle untouched
    assert existing.occ_checked_at is not None


async def test_occ_institutions_inserts_new_occ_only_uninsured_trust() -> None:
    """An OCC-only uninsured trust (FDIC cert 0 -> NULL) that matches nothing
    is INSERTed as a source='occ' row via ON CONFLICT (occ_charter_number)."""
    repo = BankRepository()
    session = _FakeDirectorySession(charter_rows=[], name_state_rows=[])
    applied = await repo.upsert_occ_institutions(session, [_directory_row()])

    assert applied == 1
    inserts = session.bank_inserts()
    assert len(inserts) == 1
    sql = _pg_sql(inserts[0])
    assert "insert into banks" in sql
    # The conflict target the migration's partial unique index provides.
    assert "on conflict (occ_charter_number)" in sql
    assert "where occ_charter_number is not null" in sql
    assert "do update set" in sql
    # Additive fill on conflict: existing value wins (opposite of the
    # FDIC-authoritative upsert).
    assert "coalesce(banks.lei, excluded.lei)" in sql
    params = inserts[0].compile(dialect=_PG).params
    assert params["charter_authority"] == "OCC"
    assert params["charter_status"] == "opened"
    assert params["source"] == "occ"
    assert params["occ_charter_number"] == "25200"
    assert params["fdic_cert"] is None  # uninsured — no FDIC record


async def test_occ_institutions_enriches_existing_occ_only_row_on_rerun() -> None:
    """A second directory sync matches the row it inserted before (by OCC
    charter number) and enriches it in place — idempotent, no new INSERT."""
    existing = Bank(
        id=5,
        occ_charter_number="25200",
        fdic_cert=None,
        fed_rssd=None,
        lei=None,
        charter_type=None,
        charter_status="opened",
        source="occ",
        name="Protego Trust Bank, National Association",
    )
    repo = BankRepository()
    session = _FakeDirectorySession(charter_rows=[existing])
    applied = await repo.upsert_occ_institutions(session, [_directory_row()])

    assert applied == 1
    assert session.bank_inserts() == []
    assert existing.lei == "5493001PROTEGO00LE00"
    assert existing.fed_rssd == "8888888"
    assert existing.charter_type == "TrustCo-National"
    assert existing.occ_charter_number == "25200"  # unchanged


async def test_occ_institutions_matches_by_name_and_state_fallback() -> None:
    """No cert / no charter match -> the conservative FDIC name+state
    fallback links the directory row and stamps its charter number."""
    fdic_row = Bank(
        id=9,
        fdic_cert="60001",
        occ_charter_number=None,
        fed_rssd=None,
        lei=None,
        charter_type=None,
        charter_status="opened",
        source="fdic",
        state="WY",
        name="Custodia Bank, National Association",
    )
    repo = BankRepository()
    # find_fdic_candidates_by_name_state applies a normalized-name filter on
    # top of what the session returns, so the names must normalize-match.
    session = _FakeDirectorySession(charter_rows=[], name_state_rows=[fdic_row])
    row = _directory_row(
        charter_number="26000",
        fdic_cert=None,
        name="Custodia Bank",
        state="WY",
    )
    applied = await repo.upsert_occ_institutions(session, [row])

    assert applied == 1
    assert session.bank_inserts() == []
    assert fdic_row.occ_charter_number == "26000"
    assert fdic_row.lei == "5493001PROTEGO00LE00"
    assert fdic_row.charter_status == "opened"


async def test_occ_institutions_folds_cert_less_duplicate_into_fdic_row() -> None:
    """Directory row carries cert X and charter C; the FDIC row (cert X) has
    no charter, but a SEPARATE cert-less row already holds C. The cert-less
    row is merged into the FDIC row so stamping C can't trip the unique
    index — reusing merge_occ_bank_into_fdic_row."""
    fdic_row = Bank(
        id=1,
        fdic_cert="59512",
        occ_charter_number=None,
        occ_control_number=None,
        charter_status="opened",
        source="fdic",
        name="Anchorage Digital Bank, National Association",
    )
    occ_only = Bank(
        id=2,
        fdic_cert=None,
        occ_charter_number="24316",
        occ_control_number="2026-Charter-24316",
        lei="5493001ANCHOR000LE00",
        charter_type="TrustCo-National",
        charter_status="opened",
        source="occ",
        name="Anchorage Digital Bank, N.A.",
    )
    repo = BankRepository()
    session = _FakeDirectorySession(cert_match=fdic_row, charter_rows=[occ_only], events=[])
    row = _directory_row(charter_number="24316", fdic_cert="59512", name="Anchorage Digital Bank, N.A.")
    applied = await repo.upsert_occ_institutions(session, [row])

    assert applied == 1
    assert session.bank_inserts() == []
    assert session.deleted == [occ_only]  # the duplicate was folded + removed
    assert fdic_row.occ_charter_number == "24316"  # charter moved onto the survivor
    assert fdic_row.occ_control_number == "2026-Charter-24316"
    assert fdic_row.lei == "5493001ANCHOR000LE00"
    assert fdic_row.source == "fdic+occ"


async def test_occ_institutions_skips_ambiguous_charter_number() -> None:
    """Two existing rows sharing a charter number (a pre-migration dupe) is a
    data problem: skip, never guess which to enrich, and never INSERT."""
    dup_a = Bank(id=3, occ_charter_number="27000", fdic_cert=None, source="occ", name="Ambiguous A")
    dup_b = Bank(id=4, occ_charter_number="27000", fdic_cert=None, source="occ", name="Ambiguous B")
    repo = BankRepository()
    session = _FakeDirectorySession(charter_rows=[dup_a, dup_b])
    row = _directory_row(charter_number="27000", fdic_cert=None)
    applied = await repo.upsert_occ_institutions(session, [row])

    assert applied == 0
    assert session.bank_inserts() == []


async def test_occ_institutions_empty_batch_is_a_noop() -> None:
    repo = BankRepository()
    session = _FakeDirectorySession()
    assert await repo.upsert_occ_institutions(session, []) == 0
    assert session.statements == []
