"""Add client-required fields: introducing_arrangements table, formation_date, clearing_statement_text, total_assets_yoy.

Revision ID: 20260419_0009
Revises: 20260415_0008
Create Date: 2026-04-19 02:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260419_0009"
down_revision = "20260415_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. New table: introducing_arrangements
    op.create_table(
        "introducing_arrangements",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("bd_id", sa.Integer, sa.ForeignKey("broker_dealers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("statement", sa.Text, nullable=True),
        sa.Column("business_name", sa.String(255), nullable=True),
        sa.Column("effective_date", sa.Date, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 2. broker_dealers: add formation_date, total_assets_yoy, clearing_raw_text
    op.add_column("broker_dealers", sa.Column("formation_date", sa.Date, nullable=True))
    op.add_column("broker_dealers", sa.Column("total_assets_yoy", sa.Numeric(8, 2), nullable=True))
    op.add_column("broker_dealers", sa.Column("clearing_raw_text", sa.Text, nullable=True))
    op.add_column("broker_dealers", sa.Column("types_of_business_other", sa.Text, nullable=True))
    op.add_column("broker_dealers", sa.Column("types_of_business_total", sa.Integer, nullable=True))

    # 3. clearing_arrangements: add clearing_statement_text
    op.add_column("clearing_arrangements", sa.Column("clearing_statement_text", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("clearing_arrangements", "clearing_statement_text")
    op.drop_column("broker_dealers", "types_of_business_total")
    op.drop_column("broker_dealers", "types_of_business_other")
    op.drop_column("broker_dealers", "clearing_raw_text")
    op.drop_column("broker_dealers", "total_assets_yoy")
    op.drop_column("broker_dealers", "formation_date")
    op.drop_table("introducing_arrangements")
