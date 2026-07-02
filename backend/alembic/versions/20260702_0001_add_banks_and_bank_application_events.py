"""add banks + bank_application_events (bank-charter vertical)

New vertical tracking banking charters (national + state) from official
public sources, mirroring the broker-dealer vertical:

- ``banks`` — one row per institution or pending charter application,
  reconciled across FDIC BankFind (opened insured institutions, keyed by
  ``fdic_cert``) and OCC Corporate Applications Search "New Bank Charter"
  filings (applications, keyed by ``occ_control_number`` e.g.
  '2026-Charter-344521'). Both keys are unique-NULLable because a row can
  be known to only one source (state charters never touch the OCC; a
  pending OCC application has no FDIC cert yet).
- ``bank_application_events`` — the OCC action timeline (Receipt /
  Approved / Consummated-Effective / Withdrawn ...), one row per
  (bank, action, action_date) so the watcher's overlapping-window re-runs
  upsert idempotently. ``raw`` JSONB keeps the undocumented CAS item
  verbatim.

Banks matched to the OCC "Digital Assets Licensing Applications" page
(client addition) carry ``digital_assets=true`` plus the public-portion
application PDF links in ``digital_asset_pdfs``.

Populated by ``scripts/watch_bank_charters.py`` (nightly Cloud Run Job);
read by ``GET /api/v1/banks*``. See docs/runbooks/bank-charter-watch.md.

Revision ID: 20260702_0001
Revises: 20260623_0002
Create Date: 2026-07-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260702_0001"
down_revision: str | None = "20260623_0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "banks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # Identifiers — unique-NULLable keys per source (Postgres treats
        # NULL != NULL, so many key-less rows coexist under the constraint).
        sa.Column("fdic_cert", sa.String(length=16), nullable=True),
        sa.Column("fed_rssd", sa.String(length=16), nullable=True),
        sa.Column("occ_charter_number", sa.String(length=16), nullable=True),
        sa.Column("occ_control_number", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=True),
        sa.Column("zip", sa.String(length=16), nullable=True),
        sa.Column("website", sa.String(length=512), nullable=True),
        # 'OCC' | 'STATE' (FDIC CHRTAGNT verbatim; 'OCC' for CAS rows).
        sa.Column("charter_authority", sa.String(length=16), nullable=True),
        sa.Column("bkclass", sa.String(length=8), nullable=True),
        sa.Column("regulator", sa.String(length=16), nullable=True),
        # pending | approved | opened | withdrawn | rescinded
        sa.Column("charter_status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("established_date", sa.Date(), nullable=True),
        sa.Column("insured_date", sa.Date(), nullable=True),
        sa.Column("application_received_date", sa.Date(), nullable=True),
        sa.Column("last_action_date", sa.Date(), nullable=True),
        # FDIC BankFind ASSET/DEP as reported ($ thousands).
        sa.Column("asset", sa.Numeric(18, 2), nullable=True),
        sa.Column("deposits", sa.Numeric(18, 2), nullable=True),
        sa.Column("offices", sa.Integer(), nullable=True),
        # Tri-state: NULL = no FDIC record yet (OCC-only application).
        sa.Column("active", sa.Boolean(), nullable=True),
        # OCC "Digital Assets Licensing Applications" page match (novel /
        # de-novo digital-asset national bank charters + conversions).
        # Sticky true once matched; never auto-unset by the watcher.
        sa.Column("digital_assets", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # Public-portion application PDF links from that page:
        # [{"title": ..., "url": ..., "received_date": "YYYY-MM-DD"|null}, ...]
        sa.Column("digital_asset_pdfs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="fdic"),
        sa.Column("fdic_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occ_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    # Unique indexes double as the watcher's upsert conflict targets
    # (ON CONFLICT (fdic_cert) / (occ_control_number)).
    op.create_index("ix_banks_fdic_cert", "banks", ["fdic_cert"], unique=True)
    op.create_index("ix_banks_occ_control_number", "banks", ["occ_control_number"], unique=True)
    op.create_index("ix_banks_fed_rssd", "banks", ["fed_rssd"])
    op.create_index("ix_banks_name", "banks", ["name"])
    op.create_index("ix_banks_state", "banks", ["state"])
    op.create_index("ix_banks_charter_authority", "banks", ["charter_authority"])
    op.create_index("ix_banks_charter_status", "banks", ["charter_status"])
    # Covers the default list sort + the new-charter window filter
    # (established_after/established_before).
    op.create_index("ix_banks_established_date", "banks", ["established_date"])
    op.create_index("ix_banks_digital_assets", "banks", ["digital_assets"])

    op.create_table(
        "bank_application_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bank_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("action_date", sa.Date(), nullable=True),
        sa.Column("filing_type", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        # CASCADE: the timeline must not outlive its bank row.
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"], ondelete="CASCADE"),
        # Idempotency key for the watcher's overlapping-window upserts.
        sa.UniqueConstraint(
            "bank_id", "action", "action_date",
            name="uq_bank_application_events_bank_action_date",
        ),
    )
    op.create_index("ix_bank_application_events_bank_id", "bank_application_events", ["bank_id"])


def downgrade() -> None:
    op.drop_index("ix_bank_application_events_bank_id", table_name="bank_application_events")
    op.drop_table("bank_application_events")
    for name in (
        "ix_banks_digital_assets",
        "ix_banks_established_date",
        "ix_banks_charter_status",
        "ix_banks_charter_authority",
        "ix_banks_state",
        "ix_banks_name",
        "ix_banks_fed_rssd",
        "ix_banks_occ_control_number",
        "ix_banks_fdic_cert",
    ):
        op.drop_index(name, table_name="banks")
    op.drop_table("banks")
