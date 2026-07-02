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

from sqlalchemy.dialects import postgresql

from app.models.bank import Bank
from app.services.banks import BankRepository
from app.services.fdic_bankfind import FdicInstitutionRecord
from app.services.occ_cas import OccCharterFiling

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
