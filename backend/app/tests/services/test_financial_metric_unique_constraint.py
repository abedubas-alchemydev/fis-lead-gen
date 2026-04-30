"""Phase 2C-schema tests - UNIQUE(bd_id, report_date) + narrowed DELETE.

Covers the four behaviours Phase 2C-schema must guarantee:

  1. Dedupe correctness - the migration's dedupe SQL collapses multiple
     rows sharing (bd_id, report_date) to a single survivor (the most-
     recently-inserted row, since financial_metrics has no
     confidence_score column today).
  2. UNIQUE rejection - post-constraint, an INSERT duplicating
     (bd_id, report_date) raises IntegrityError.
  3. Narrowed DELETE preserves other-date rows - the focus_reports
     DELETE now keys on (bd_id, report_date) tuples; rows with a
     different report_date for the same bd_id survive.
  4. Migration round-trip - upgrade -> downgrade -> upgrade runs cleanly
     against a real Postgres. Integration-marked; run via
     `pytest -m integration` against the staging Neon DB.

Tests 1-3 use an in-memory SQLite engine; the SQL under test is ANSI
(ROW_NUMBER OVER, CTE, tuple IN) and SQLite 3.25+ supports it. Test 4
requires real Postgres for alembic upgrade/downgrade against a live
migration chain.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    Table,
    UniqueConstraint,
    create_engine,
    delete,
    insert,
    select,
    text,
    tuple_,
)
from sqlalchemy.exc import IntegrityError


# Local shim of the financial_metrics schema. We deliberately avoid
# importing the ORM model because app.models pulls in the full
# SQLAlchemy graph (BrokerDealer, auth tables, ...). Only the columns
# relevant to the Phase 2C-schema change are modeled here.
def _build_table(metadata: MetaData, *, with_constraint: bool) -> Table:
    args: list = [
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("bd_id", Integer, nullable=False),
        Column("report_date", Date, nullable=False),
        Column("net_capital", Numeric(18, 2), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    ]
    if with_constraint:
        args.append(
            UniqueConstraint(
                "bd_id", "report_date", name="uq_financial_metrics_bd_report_date"
            )
        )
    return Table("financial_metrics", metadata, *args)


_DEDUPE_SQL = """
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY bd_id, report_date
               ORDER BY created_at DESC, id DESC
           ) AS rn
    FROM financial_metrics
)
DELETE FROM financial_metrics
WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
"""


@pytest.fixture
def sqlite_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    yield engine
    engine.dispose()


def test_dedupe_keeps_most_recent_row_per_pair(sqlite_engine) -> None:
    """Seed three rows with the same (bd_id, report_date) but staggered
    created_at. The migration's dedupe SQL should keep exactly the
    most-recently-inserted row."""
    metadata = MetaData()
    table = _build_table(metadata, with_constraint=False)
    metadata.create_all(sqlite_engine)

    t0 = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    report_date = date(2025, 12, 31)

    with sqlite_engine.begin() as conn:
        conn.execute(
            insert(table),
            [
                {"bd_id": 42, "report_date": report_date, "net_capital": 100, "created_at": t0},
                {"bd_id": 42, "report_date": report_date, "net_capital": 200, "created_at": t2},
                {"bd_id": 42, "report_date": report_date, "net_capital": 150, "created_at": t1},
            ],
        )

    with sqlite_engine.begin() as conn:
        conn.execute(text(_DEDUPE_SQL))

    with sqlite_engine.connect() as conn:
        rows = conn.execute(select(table)).all()

    assert len(rows) == 1
    # The row with created_at=t2 is the one expected to survive. Its
    # net_capital was 200.
    assert rows[0].net_capital == 200


def test_unique_constraint_rejects_duplicate_pair(sqlite_engine) -> None:
    """After the migration runs, inserting a duplicate (bd_id,
    report_date) must raise IntegrityError."""
    metadata = MetaData()
    table = _build_table(metadata, with_constraint=True)
    metadata.create_all(sqlite_engine)

    now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    report_date = date(2025, 12, 31)

    with sqlite_engine.begin() as conn:
        conn.execute(
            insert(table).values(
                bd_id=7, report_date=report_date, net_capital=1000, created_at=now
            )
        )

    with pytest.raises(IntegrityError):
        with sqlite_engine.begin() as conn:
            conn.execute(
                insert(table).values(
                    bd_id=7, report_date=report_date, net_capital=2000, created_at=now
                )
            )


def test_narrowed_delete_preserves_other_dates(sqlite_engine) -> None:
    """The narrowed DELETE in focus_reports.py keys on (bd_id,
    report_date) tuples. Seed two rows for the same bd_id on different
    dates; delete only one pair; the other must survive."""
    metadata = MetaData()
    table = _build_table(metadata, with_constraint=True)
    metadata.create_all(sqlite_engine)

    now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    current_year = date(2025, 12, 31)
    prior_year = date(2024, 12, 31)

    with sqlite_engine.begin() as conn:
        conn.execute(
            insert(table),
            [
                {"bd_id": 9, "report_date": current_year, "net_capital": 500, "created_at": now},
                {"bd_id": 9, "report_date": prior_year, "net_capital": 400, "created_at": now},
            ],
        )

    # Emulate focus_reports.py's narrowed DELETE: only the pair the
    # current run is about to re-insert is deleted. Here the run
    # extracts (9, 2025-12-31) only; (9, 2024-12-31) must be preserved.
    target_pairs = [(9, current_year)]
    with sqlite_engine.begin() as conn:
        conn.execute(
            delete(table).where(tuple_(table.c.bd_id, table.c.report_date).in_(target_pairs))
        )

    with sqlite_engine.connect() as conn:
        rows = conn.execute(select(table).order_by(table.c.report_date)).all()

    assert len(rows) == 1
    assert rows[0].bd_id == 9
    assert rows[0].report_date == prior_year
    assert rows[0].net_capital == 400


@pytest.mark.integration
def test_migration_round_trip_on_postgres() -> None:
    """Upgrade -> downgrade -> upgrade runs cleanly against the real DB.

    Requires DATABASE_URL to point at a Postgres already at migration
    head 20260423_0013 (the revision immediately before Phase 2C-schema)
    or at the new head. Run via `pytest -m integration` against staging
    Neon. Skipped in the default test run.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from app.core.config import settings

    cfg = Config("alembic.ini")
    # psycopg3's SQLAlchemy dialect supports both sync and async; psycopg2 isn't
    # installed. Force the +psycopg prefix so create_engine doesn't fall back to
    # psycopg2 on a bare postgresql:// URL.
    sync_url = settings.database_url.replace("+asyncpg", "+psycopg")
    if sync_url.startswith("postgresql://"):
        sync_url = "postgresql+psycopg://" + sync_url[len("postgresql://") :]
    cfg.set_main_option("sqlalchemy.url", sync_url)

    def _constraint_present() -> bool:
        engine = create_engine(sync_url)
        try:
            insp = inspect(engine)
            names = {
                uq["name"]
                for uq in insp.get_unique_constraints("financial_metrics")
            }
            return "uq_financial_metrics_bd_report_date" in names
        finally:
            engine.dispose()

    # Target the specific migration this test exercises, not "-1"/"head".
    # The relative form was brittle: when later migrations land, "head" advances
    # past 2cc4af2a4ef5 and "downgrade -1" undoes the wrong migration.
    target_revision = "2cc4af2a4ef5"
    prior_revision = "20260423_0013"  # down_revision of target

    # CI's pre-test step has already applied head. Walk back to the revision
    # immediately before our target so the constraint starts absent.
    command.downgrade(cfg, prior_revision)
    assert not _constraint_present(), "constraint should be absent before target migration"

    command.upgrade(cfg, target_revision)
    assert _constraint_present(), "constraint missing after upgrade to target"

    command.downgrade(cfg, prior_revision)
    assert not _constraint_present(), "constraint still present after downgrade"

    # Restore DB to head so subsequent tests in the same run see the expected
    # schema state.
    command.upgrade(cfg, "head")
    assert _constraint_present(), "constraint missing after restoring to head"
