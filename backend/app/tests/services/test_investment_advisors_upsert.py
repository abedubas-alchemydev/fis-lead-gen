"""SQL-shape tests for the bulk advisor upsert.

Migration 0035 promoted the ``crd_number`` index to a partial unique
index, which lets ``InvestmentAdvisorRepository.upsert_many`` /
``upsert_by_crd`` collapse what used to be ~17k individual round trips
(one SELECT, then per-row INSERT/UPDATE) into a single
``INSERT ... ON CONFLICT (crd_number) DO UPDATE`` per 500-row batch.

These tests pin the contract by compiling the executed statement and
asserting on its SQL text, mirroring the pattern from
``test_types_of_business_filter.py``. They run without a real database
because the conflict-target inference is purely SQL-shape.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.investment_advisors import InvestmentAdvisorRepository
from app.services.service_models import MergedInvestmentAdvisorRecord


class _RecordingSession:
    """AsyncSession stub that captures every executed statement."""

    def __init__(self) -> None:
        self.executed: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.executed.append(statement)
        return MagicMock()


def _merged_record(crd: str, name: str = "ACME ADVISORS") -> MergedInvestmentAdvisorRecord:
    return MergedInvestmentAdvisorRecord(
        crd_number=crd,
        sec_file_number=None,
        cik=None,
        name=name,
        legal_name=None,
        city=None,
        state=None,
        status="active",
        last_filing_date=None,
        website=None,
        website_source=None,
        regulatory_aum=None,
        discretionary_aum=None,
        non_discretionary_aum=None,
        total_clients=None,
        advisory_activities=[],
        client_types=[],
        client_counts={},
        files_13f=False,
        latest_13f_filing_date=None,
    )


def _compile(statement: Any) -> str:
    """Compile against the Postgres dialect with placeholders intact.

    Skipping ``literal_binds`` is intentional: the JSONB columns
    (``advisory_activities``, ``client_types``, ``client_counts``) have
    no literal renderer, but the SQL structure we care about
    (``INSERT``, ``ON CONFLICT``, ``DO UPDATE SET``) is preserved by
    placeholder-form compilation.
    """

    from sqlalchemy.dialects import postgresql

    return str(statement.compile(dialect=postgresql.dialect())).lower()


@pytest.fixture
def repository() -> InvestmentAdvisorRepository:
    return InvestmentAdvisorRepository()


@pytest.mark.asyncio
async def test_upsert_many_emits_single_on_conflict_per_batch(
    repository: InvestmentAdvisorRepository,
) -> None:
    """One INSERT ... ON CONFLICT statement per batch, not N round trips."""

    session = _RecordingSession()
    records = [_merged_record(crd=str(i)) for i in range(3)]

    total = await repository.upsert_many(session, records)

    assert total == 3
    assert len(session.executed) == 1, (
        "expected a single bulk INSERT ... ON CONFLICT statement, "
        f"got {len(session.executed)}"
    )
    sql = _compile(session.executed[0])
    assert "insert into investment_advisors" in sql
    assert "on conflict" in sql
    assert "do update set" in sql


@pytest.mark.asyncio
async def test_upsert_many_targets_partial_unique_crd_index(
    repository: InvestmentAdvisorRepository,
) -> None:
    """Conflict target must match migration 0035's partial unique index.

    Without the ``index_where`` predicate, PostgreSQL cannot infer the
    arbiter index when more than one index covers ``crd_number`` and
    the upsert would fail at runtime with "no unique or exclusion
    constraint matching the ON CONFLICT specification".
    """

    session = _RecordingSession()
    await repository.upsert_many(session, [_merged_record(crd="1")])
    sql = _compile(session.executed[0])

    assert "on conflict (crd_number) where crd_number is not null" in sql, (
        "expected conflict target to match the partial unique index from "
        f"migration 0035; got SQL:\n{sql}"
    )


@pytest.mark.asyncio
async def test_upsert_many_protects_id_and_created_at_from_overwrite(
    repository: InvestmentAdvisorRepository,
) -> None:
    """The DO UPDATE set must not touch ``id`` or ``created_at``.

    Refreshing ``id`` would invalidate FK refs from advisor_filings /
    advisor_contacts / favorite_list_item; refreshing ``created_at``
    would erase the original-insertion timestamp on every IAPD re-run.
    """

    session = _RecordingSession()
    await repository.upsert_many(session, [_merged_record(crd="1")])
    sql = _compile(session.executed[0])

    update_clause = sql.split("do update set", 1)[1]
    assert "id =" not in update_clause, (
        "id is the surrogate key — never overwrite from EXCLUDED"
    )
    assert "created_at =" not in update_clause, (
        "created_at must survive re-imports as the original insertion stamp"
    )


@pytest.mark.asyncio
async def test_upsert_many_never_touches_other_business_names(
    repository: InvestmentAdvisorRepository,
) -> None:
    """``other_business_names`` is enrichment-only — written by the IAPD-summary
    refresh sub-pipeline / one-shot backfill, never the bulk IAPD ingest. It
    must stay out of the upsert entirely (not an INSERT column, not in DO
    UPDATE SET) so a re-ingest can't wipe an enriched value. Same protection
    ``registration_date`` / ``direct_owners`` rely on: they're simply absent
    from ``_record_to_columns``. If a future edit adds it to the ingest path,
    this fails loudly.
    """

    session = _RecordingSession()
    await repository.upsert_many(session, [_merged_record(crd="1")])
    sql = _compile(session.executed[0])

    assert "other_business_names" not in sql, (
        "other_business_names is enrichment-only and must stay out of the bulk "
        f"ingest upsert entirely; got SQL:\n{sql}"
    )


@pytest.mark.asyncio
async def test_upsert_many_chunks_into_batches_of_500(
    repository: InvestmentAdvisorRepository,
) -> None:
    """1,250 rows must split into 500 + 500 + 250."""

    session = _RecordingSession()
    records = [_merged_record(crd=str(i)) for i in range(1250)]

    total = await repository.upsert_many(session, records)

    assert total == 1250
    assert len(session.executed) == 3, (
        f"expected 3 batches of <=500 each, got {len(session.executed)}"
    )


@pytest.mark.asyncio
async def test_upsert_by_crd_uses_same_bulk_path(
    repository: InvestmentAdvisorRepository,
) -> None:
    """``upsert_by_crd`` (dict-input) shares the bulk helper, not the old loop."""

    session = _RecordingSession()
    rows = [
        {"crd_number": "1", "name": "A", "status": "active"},
        {"crd_number": "2", "name": "B", "status": "active"},
    ]

    total = await repository.upsert_by_crd(session, rows)

    assert total == 2
    assert len(session.executed) == 1
    sql = _compile(session.executed[0])
    assert "insert into investment_advisors" in sql
    assert "on conflict" in sql


@pytest.mark.asyncio
async def test_empty_input_short_circuits(
    repository: InvestmentAdvisorRepository,
) -> None:
    """No statements emitted for empty inputs (no-op upsert is free)."""

    session = _RecordingSession()
    assert await repository.upsert_many(session, []) == 0
    assert await repository.upsert_by_crd(session, []) == 0
    assert session.executed == []
