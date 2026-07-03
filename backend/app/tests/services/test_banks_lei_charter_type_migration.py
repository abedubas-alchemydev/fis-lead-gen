"""Schema additions for the OCC Institutions API enrichment
(``20260702_0002`` — ``banks.lei`` + ``banks.charter_type``).

DB-less pins on the three places the addition must agree with itself:

1. the alembic chain stays SINGLE-headed at ``20260702_0002``, a strict
   child of the banks migration ``20260702_0001`` (a second head here
   silently breaks the deploy pipeline's ``alembic upgrade head``);
2. the ORM model carries both columns, nullable + string-typed (the
   watcher writes them additively — a NOT NULL would break every
   XLSX-reconciled and state-charter row);
3. the API surface exposes them on the DETAIL schema only (list payloads
   are deliberately unchanged).
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import String

from app.models.bank import Bank
from app.schemas.bank import BankDetail, BankListItem

# backend/ = app/tests/services/<this file> → parents[3].
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _script_directory() -> ScriptDirectory:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    # Absolute so the test passes regardless of pytest's cwd.
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_chain_has_a_single_head() -> None:
    # Single-headedness is the invariant (a second head silently breaks the
    # deploy pipeline's ``alembic upgrade head``); the newest revision is
    # currently the banks full-directory OCC charter-number unique index.
    assert _script_directory().get_heads() == ["20260703_0001"]


def test_migration_is_an_additive_child_of_the_banks_migration() -> None:
    revision = _script_directory().get_revision("20260702_0002")
    assert revision.down_revision == "20260702_0001"


def test_bank_contacts_migration_is_a_child_of_the_lei_migration() -> None:
    revision = _script_directory().get_revision("20260702_0003")
    assert revision.down_revision == "20260702_0002"


def test_full_directory_index_migration_is_a_child_of_the_enrichment_migration() -> None:
    # The banks full-directory work adds ONE migration: a partial unique
    # index on occ_charter_number (the ON CONFLICT arbiter for
    # upsert_occ_institutions), stacked directly on the current head.
    revision = _script_directory().get_revision("20260703_0001")
    assert revision.down_revision == "20260702_0004"


def test_bank_model_columns_are_nullable_strings() -> None:
    lei = Bank.__table__.c.lei
    charter_type = Bank.__table__.c.charter_type
    assert lei.nullable and charter_type.nullable
    assert isinstance(lei.type, String) and isinstance(charter_type.type, String)
    # LEI is a fixed-width ISO 17442 identifier (20 chars).
    assert lei.type.length == 20


def test_detail_schema_exposes_the_fields_and_list_schema_does_not() -> None:
    assert "lei" in BankDetail.model_fields
    assert "charter_type" in BankDetail.model_fields
    # Nullable end-to-end: a pre-migration-shaped row must still validate.
    assert BankDetail.model_fields["lei"].default is None
    assert BankDetail.model_fields["charter_type"].default is None
    # Deliberately detail-only — the list payload is unchanged.
    assert "lei" not in BankListItem.model_fields
    assert "charter_type" not in BankListItem.model_fields
