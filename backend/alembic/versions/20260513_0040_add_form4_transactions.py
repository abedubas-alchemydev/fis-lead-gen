"""add form4_transactions table

Backs the new Investors tab. One row per (reporting-person × Form 4
transaction) pair. Watcher upserts on ``dedupe_key`` so re-running the
EFTS lookback window is idempotent. ``ad_code`` (A = acquired/buy,
D = disposed/sell) partitions the two product-facing lists.

See plans/c-users-dswdsrv-caraga-downloads-potenti-robust-wombat.md
for the design and the meeting transcript that drove the schema.

Revision ID: 20260513_0040
Revises: 20260511_0039
Create Date: 2026-05-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260513_0040"
down_revision: str | None = "20260511_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "form4_transactions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("accession_number", sa.String(length=64), nullable=False),
        sa.Column("transaction_index", sa.Integer(), nullable=False),
        sa.Column(
            "is_derivative",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("issuer_cik", sa.String(length=16), nullable=False),
        sa.Column("issuer_name", sa.String(length=255), nullable=False),
        sa.Column("issuer_ticker", sa.String(length=16), nullable=True),
        sa.Column("reporting_owner_cik", sa.String(length=16), nullable=False),
        sa.Column("reporting_owner_name", sa.String(length=255), nullable=False),
        sa.Column(
            "reporting_owner_is_director",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "reporting_owner_is_officer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "reporting_owner_is_ten_pct",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("reporting_owner_title", sa.String(length=255), nullable=True),
        sa.Column("reporting_owner_street1", sa.String(length=255), nullable=True),
        sa.Column("reporting_owner_street2", sa.String(length=255), nullable=True),
        sa.Column("reporting_owner_city", sa.String(length=128), nullable=True),
        sa.Column("reporting_owner_state", sa.String(length=32), nullable=True),
        sa.Column("reporting_owner_zip", sa.String(length=32), nullable=True),
        sa.Column("security_title", sa.String(length=255), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("transaction_code", sa.String(length=2), nullable=True),
        sa.Column("ad_code", sa.String(length=1), nullable=False),
        sa.Column("shares", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("price_per_share", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column(
            "transaction_value", sa.Numeric(precision=20, scale=2), nullable=True
        ),
        sa.Column("source_filing_url", sa.Text(), nullable=True),
        sa.Column("enriched_phone", sa.String(length=64), nullable=True),
        sa.Column("enriched_email", sa.String(length=255), nullable=True),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "ad_code IN ('A', 'D')", name="form4_transactions_ad_code_chk"
        ),
        sa.UniqueConstraint("dedupe_key", name="form4_transactions_dedupe_key_key"),
    )
    op.create_index(
        "ix_form4_transactions_accession_number",
        "form4_transactions",
        ["accession_number"],
    )
    op.create_index(
        "ix_form4_transactions_dedupe_key",
        "form4_transactions",
        ["dedupe_key"],
    )
    op.create_index(
        "ix_form4_transactions_issuer_cik",
        "form4_transactions",
        ["issuer_cik"],
    )
    op.create_index(
        "ix_form4_transactions_issuer_ticker",
        "form4_transactions",
        ["issuer_ticker"],
    )
    op.create_index(
        "ix_form4_transactions_reporting_owner_cik",
        "form4_transactions",
        ["reporting_owner_cik"],
    )
    op.create_index(
        "ix_form4_transactions_reporting_owner_name",
        "form4_transactions",
        ["reporting_owner_name"],
    )
    op.create_index(
        "ix_form4_transactions_transaction_date",
        "form4_transactions",
        ["transaction_date"],
    )
    op.create_index(
        "ix_form4_transactions_transaction_value",
        "form4_transactions",
        ["transaction_value"],
    )
    op.create_index(
        "ix_form4_ad_date",
        "form4_transactions",
        ["ad_code", "transaction_date"],
    )
    op.create_index(
        "ix_form4_ticker_date",
        "form4_transactions",
        ["issuer_ticker", "transaction_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_form4_ticker_date", table_name="form4_transactions")
    op.drop_index("ix_form4_ad_date", table_name="form4_transactions")
    op.drop_index(
        "ix_form4_transactions_transaction_value", table_name="form4_transactions"
    )
    op.drop_index(
        "ix_form4_transactions_transaction_date", table_name="form4_transactions"
    )
    op.drop_index(
        "ix_form4_transactions_reporting_owner_name",
        table_name="form4_transactions",
    )
    op.drop_index(
        "ix_form4_transactions_reporting_owner_cik",
        table_name="form4_transactions",
    )
    op.drop_index(
        "ix_form4_transactions_issuer_ticker", table_name="form4_transactions"
    )
    op.drop_index(
        "ix_form4_transactions_issuer_cik", table_name="form4_transactions"
    )
    op.drop_index(
        "ix_form4_transactions_dedupe_key", table_name="form4_transactions"
    )
    op.drop_index(
        "ix_form4_transactions_accession_number", table_name="form4_transactions"
    )
    op.drop_table("form4_transactions")
