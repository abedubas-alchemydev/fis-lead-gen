"""Regression: ``BrokerDealerRepository.upsert_many`` must de-duplicate each
ON CONFLICT sub-batch by its conflict key.

Staging 2026-07-01: the incremental extract job's chunk 1 resolved 19/25 EDGAR
rows, but two of them shared a CIK. ``INSERT ... ON CONFLICT (cik) DO UPDATE``
cannot affect the same target row twice, so Postgres raised
``CardinalityViolation`` and aborted the ENTIRE batch — nothing committed, the
BD count stuck. The MERGE step already dedupes by ``sec_file_number``, but the
UPSERT conflicts on ``cik`` (a different key), so two firms with distinct SEC
numbers that resolve to one CIK slipped through and collided.

These pin the fix without a real database: the dedupe is pure Python, and the
SQL shape is asserted by compiling the captured statement against the Postgres
dialect (same approach as ``test_investment_advisors_upsert.py``).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.broker_dealers import (
    BrokerDealerRepository,
    _dedupe_by_conflict_key,
)
from app.services.service_models import MergedBrokerDealerRecord

_LOGGER_NAME = "app.services.broker_dealers"


class _RecordingSession:
    """AsyncSession stub that captures executed statements. The cik-null path's
    existence SELECT returns ``existing_sec`` via ``.scalars().all()``; every
    other execute's return value is ignored by ``upsert_many``."""

    def __init__(self, existing_sec: tuple[str, ...] = ()) -> None:
        self.executed: list[Any] = []
        self._existing_sec = list(existing_sec)

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.executed.append(statement)
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(self._existing_sec)
        return result

    async def commit(self) -> None:
        return None


def _merged(
    *, cik: str | None, crd: str, sec: str | None, name: str = "ACME SECURITIES LLC"
) -> MergedBrokerDealerRecord:
    return MergedBrokerDealerRecord(
        cik=cik,
        crd_number=crd,
        sec_file_number=sec,
        name=name,
        city="New York",
        state="NY",
        status="active",
        branch_count=1,
        business_type=None,
        registration_date=None,
        matched_source="both" if cik else "finra_only",
        last_filing_date=None,
        filings_index_url=None,
    )


def _compile(statement: Any) -> str:
    return str(statement.compile(dialect=postgresql.dialect())).lower()


def _insert_column_values(statement: Any, column: str) -> list[Any]:
    """Values bound to ``column`` across a compiled INSERT's VALUES rows.

    A multi-row ``insert().values([...])`` names its binds ``<col>_m0``,
    ``<col>_m1``, ...; a single-dict insert uses the bare ``<col>``. Return one
    entry per VALUES row so the count doubles as a row count.
    """
    params = statement.compile(dialect=postgresql.dialect()).params
    return [
        value
        for key, value in params.items()
        if key == column or key.startswith(f"{column}_m")
    ]


def _inserts(session: _RecordingSession) -> list[Any]:
    return [s for s in session.executed if "insert into broker_dealers" in _compile(s)]


@pytest.fixture
def repository() -> BrokerDealerRepository:
    return BrokerDealerRepository()


@pytest.mark.asyncio
async def test_upsert_dedupes_batch_sharing_cik(
    repository: BrokerDealerRepository, caplog: pytest.LogCaptureFixture
) -> None:
    """Two rows with the same CIK collapse to one (last-wins) BEFORE the
    ON CONFLICT (cik) INSERT — a single VALUES row, so no CardinalityViolation —
    and a WARNING names the colliding key + crd_numbers.

    Regression guard: pre-fix this built a 2-row INSERT (which Postgres rejects)
    and returned 2, so ``total == 1`` and the single-row assertion both fail on
    the old code.
    """
    session = _RecordingSession()
    records = [
        _merged(cik="0000012345", crd="111", sec="8-1", name="OLD NAME LLC"),
        _merged(cik="0000012345", crd="222", sec="8-2", name="NEW NAME LLC"),
    ]

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        total = await repository.upsert_many(session, records)

    assert total == 1  # one row persisted, not two
    inserts = _inserts(session)
    assert len(inserts) == 1
    # Exactly one VALUES row reaches ON CONFLICT (cik) → no CardinalityViolation.
    assert _insert_column_values(inserts[0], "cik") == ["0000012345"]
    # Last occurrence wins: the surviving row carries the second record's data.
    assert _insert_column_values(inserts[0], "crd_number") == ["222"]
    assert "on conflict (cik)" in _compile(inserts[0])
    # Visibility: the drop is logged, not silent.
    assert "0000012345" in caplog.text
    assert "111" in caplog.text and "222" in caplog.text


@pytest.mark.asyncio
async def test_upsert_dedupes_cik_null_sharing_sec_file_number(
    repository: BrokerDealerRepository, caplog: pytest.LogCaptureFixture
) -> None:
    """Two cik-null rows sharing a sec_file_number collapse to a single INSERT
    on the select-then-insert path (which reads existing keys ONCE up front and
    would otherwise double-insert both as new rows), with a WARNING.

    Regression guard: pre-fix this emitted two INSERT statements.
    """
    session = _RecordingSession(existing_sec=())  # neither row exists yet
    records = [
        _merged(cik=None, crd="333", sec="8-99", name="FIRST"),
        _merged(cik=None, crd="444", sec="8-99", name="SECOND"),
    ]

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        total = await repository.upsert_many(session, records)

    assert total == 1
    assert len(_inserts(session)) == 1  # one INSERT, not two
    assert "8-99" in caplog.text
    assert "333" in caplog.text and "444" in caplog.text


@pytest.mark.asyncio
async def test_upsert_no_duplicates_unchanged(
    repository: BrokerDealerRepository, caplog: pytest.LogCaptureFixture
) -> None:
    """A batch with distinct conflict keys is untouched: all rows flow into the
    one INSERT, the count matches, and nothing is logged."""
    session = _RecordingSession()
    records = [
        _merged(cik="0000000001", crd="1", sec="8-1"),
        _merged(cik="0000000002", crd="2", sec="8-2"),
        _merged(cik="0000000003", crd="3", sec="8-3"),
    ]

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        total = await repository.upsert_many(session, records)

    assert total == 3
    inserts = _inserts(session)
    assert len(inserts) == 1
    assert len(_insert_column_values(inserts[0], "cik")) == 3  # all three rows
    assert "dropped" not in caplog.text


def test_dedupe_helper_keeps_last_and_passes_through_key_null() -> None:
    """Unit-level: the last occurrence of a key wins; records whose key is None
    cannot collide and pass through unchanged."""
    first = _merged(cik="0000000009", crd="a", sec="8-a", name="A")
    last = _merged(cik="0000000009", crd="b", sec="8-b", name="B")
    keyless = _merged(cik=None, crd="c", sec=None, name="C")

    result = _dedupe_by_conflict_key([first, last, keyless], "cik")

    kept_crds = {r.crd_number for r in result}
    assert kept_crds == {"b", "c"}  # 'a' (earlier dup) dropped; 'c' passes through
