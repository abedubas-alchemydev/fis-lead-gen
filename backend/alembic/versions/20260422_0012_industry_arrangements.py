"""Add industry_arrangements table.

Revision ID: 20260422_0012
Revises: 20260422_0011
Create Date: 2026-04-22 00:00:00

Captures the 'Firm Operations → Industry Arrangements' subsection of the FINRA
BrokerCheck Detailed Report. Three yes/no statements per firm (books_records,
accounts_funds, customer_accounts), each with an optional partner block.
Together they answer whether a firm is truly self-clearing vs using a third
party.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260422_0012"
down_revision: str | None = "20260422_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "industry_arrangements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "bd_id",
            sa.Integer(),
            sa.ForeignKey("broker_dealers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("has_arrangement", sa.Boolean(), nullable=False),
        sa.Column("partner_name", sa.String(length=255), nullable=True),
        sa.Column("partner_crd", sa.String(length=64), nullable=True),
        sa.Column("partner_address", sa.Text(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bd_id", "kind", name="uq_industry_arrangement_bd_kind"),
    )
    op.create_index(
        op.f("ix_industry_arrangements_bd_id"),
        "industry_arrangements",
        ["bd_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_industry_arrangements_bd_id"), table_name="industry_arrangements")
    op.drop_table("industry_arrangements")
