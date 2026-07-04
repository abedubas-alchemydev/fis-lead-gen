"""Alembic up/down/up roundtrip for the banks People-parity migrations.

Integration-marked: requires a reachable local Postgres (the same one the rest
of the integration suite uses) with permission to ``CREATE DATABASE``. The test
is fully isolated — it spins up a throwaway database, runs the FULL migration
chain to ``head``, downgrades the three new revisions back to their parent
(``20260703_0001``), then re-upgrades to ``head``, asserting the bank
People-parity schema appears, disappears, and reappears. The scratch DB is
dropped in teardown, so the dev DB is never touched.

Covers migrations:
- ``20260703_0002`` — discovery/channel columns on ``bank_contacts``.
- ``20260703_0003`` — ``bank_id`` / ``bank_contact_id`` on ``outreach_sends``
  + the three re-cast CHECK constraints.
- ``20260703_0004`` — ``bank_id`` on ``extraction_run`` + ``discovered_email``.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from textwrap import dedent

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import exc as sa_exc
from sqlalchemy.engine import make_url

from app.core.config import settings

pytestmark = pytest.mark.integration

_PARENT_REVISION = "20260703_0001"
_NEW_CHANNEL_COLUMNS = (
    "linkedin_url",
    "emails",
    "phones",
    "apollo_person_id",
    "discovery_source",
    "discovery_confidence",
)
_BACKEND_DIR = Path(__file__).resolve().parents[3]


def _sync_dsn(url_str: str, *, database: str | None = None) -> str:
    """Driverless libpq DSN for psycopg from a SQLAlchemy URL, optionally
    swapping the database name (used to reach the ``postgres`` maintenance DB
    for CREATE/DROP DATABASE)."""
    url = make_url(url_str).set(drivername="postgresql")
    if database is not None:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


def _columns(cur: psycopg.Cursor, table: str) -> dict[str, str]:
    cur.execute(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name = %s",
        (table,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def _check_defs(cur: psycopg.Cursor) -> dict[str, str]:
    cur.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname LIKE 'ck_outreach_sends%%'"
    )
    return {row[0]: row[1] for row in cur.fetchall()}


@pytest.fixture
def scratch_db(monkeypatch: pytest.MonkeyPatch) -> str:
    """Create an isolated throwaway DB, point ``settings.database_url`` at it
    (Alembic's ``env.py`` reads that at run time), and drop it on teardown."""
    base_url = settings.database_url
    db_name = f"deshorn_migtest_{secrets.token_hex(6)}"
    admin_dsn = _sync_dsn(base_url, database="postgres")

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{db_name}"')

    scratch_url = make_url(base_url).set(database=db_name).render_as_string(hide_password=False)
    monkeypatch.setattr(settings, "database_url", scratch_url)
    try:
        yield scratch_url
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return cfg


def test_bank_people_parity_migrations_roundtrip(scratch_db: str) -> None:
    cfg = _alembic_config()
    inspect_dsn = _sync_dsn(scratch_db)

    # ── UP: full chain to head ────────────────────────────────────────
    command.upgrade(cfg, "head")
    with psycopg.connect(inspect_dsn) as conn, conn.cursor() as cur:
        bank_cols = _columns(cur, "bank_contacts")
        for col in _NEW_CHANNEL_COLUMNS:
            assert col in bank_cols, f"{col} missing after upgrade"
        # Relaxed to nullable for discovery rows.
        assert bank_cols["role_context"] == "YES"
        assert bank_cols["source_url"] == "YES"

        send_cols = _columns(cur, "outreach_sends")
        assert "bank_id" in send_cols
        assert "bank_contact_id" in send_cols
        checks = _check_defs(cur)
        assert "bank_id" in checks["ck_outreach_sends_at_most_one_firm"]
        assert "bank_contact_id" in checks["ck_outreach_sends_at_most_one_contact"]
        assert "bank_contact_id" in checks["ck_outreach_sends_adhoc_has_recipient"]

        assert "bank_id" in _columns(cur, "extraction_run")
        assert "bank_id" in _columns(cur, "discovered_email")

    # ── DOWN: revert the three new revisions to their parent ──────────
    command.downgrade(cfg, _PARENT_REVISION)
    with psycopg.connect(inspect_dsn) as conn, conn.cursor() as cur:
        bank_cols = _columns(cur, "bank_contacts")
        for col in _NEW_CHANNEL_COLUMNS:
            assert col not in bank_cols, f"{col} lingered after downgrade"
        # NOT NULL restored.
        assert bank_cols["role_context"] == "NO"
        assert bank_cols["source_url"] == "NO"

        send_cols = _columns(cur, "outreach_sends")
        assert "bank_id" not in send_cols
        assert "bank_contact_id" not in send_cols
        checks = _check_defs(cur)
        assert "bank_id" not in checks["ck_outreach_sends_at_most_one_firm"]

        assert "bank_id" not in _columns(cur, "extraction_run")
        assert "bank_id" not in _columns(cur, "discovered_email")

    # ── UP AGAIN: re-apply cleanly ────────────────────────────────────
    command.upgrade(cfg, "head")
    with psycopg.connect(inspect_dsn) as conn, conn.cursor() as cur:
        assert "apollo_person_id" in _columns(cur, "bank_contacts")
        assert "bank_id" in _columns(cur, "outreach_sends")


def test_downgrade_0003_fails_by_design_on_bank_sends(scratch_db: str) -> None:
    """Pins the documented 0003 downgrade contract: a real bank ``outreach_sends``
    row (only ``bank_contact_id`` set — no ``recipient_email``) makes the
    downgrade FAIL rather than silently discard the audit row. ``outreach_sends``
    is a compliance trail, so reverting must be a conscious human step.

    Postgres DDL is transactional, so the failed downgrade must roll back whole —
    the bank columns/constraints stay intact, never left half-reverted.
    """
    cfg = _alembic_config()
    inspect_dsn = _sync_dsn(scratch_db)

    command.upgrade(cfg, "head")

    # Seed a realistic bank send: user -> bank -> bank_contact -> outreach_sends
    # carrying (bank_id, bank_contact_id) and NO recipient_email. Committed so
    # Alembic's separate downgrade connection sees it.
    uid = f"u_{secrets.token_hex(6)}"
    with psycopg.connect(inspect_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "user" (id, name, email) VALUES (%s, %s, %s)',
            (uid, "Mig Test", f"{uid}@example.com"),
        )
        cur.execute(
            "INSERT INTO banks (name) VALUES (%s) RETURNING id", ("Roundtrip Bank",)
        )
        bank_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO bank_contacts (bank_id, name) VALUES (%s, %s) RETURNING id",
            (bank_id, "Jane Officer"),
        )
        contact_id = cur.fetchone()[0]
        cur.execute(
            dedent(
                """
                INSERT INTO outreach_sends
                    (user_id, bank_id, bank_contact_id, subject, body, provider, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
            ),
            (uid, bank_id, contact_id, "Hello", "Body", "google", "sent"),
        )

    # Downgrade only across 0003 (to its parent 20260703_0002), isolating the
    # 0003 hazard. It must RAISE (the three-way CHECK rejects the bank send).
    with pytest.raises(sa_exc.IntegrityError):
        command.downgrade(cfg, "20260703_0002")

    # Rolled back whole: the bank columns + audit row survive untouched.
    with psycopg.connect(inspect_dsn) as conn, conn.cursor() as cur:
        send_cols = _columns(cur, "outreach_sends")
        assert "bank_id" in send_cols
        assert "bank_contact_id" in send_cols
        cur.execute("SELECT count(*) FROM outreach_sends WHERE bank_contact_id IS NOT NULL")
        assert cur.fetchone()[0] == 1
