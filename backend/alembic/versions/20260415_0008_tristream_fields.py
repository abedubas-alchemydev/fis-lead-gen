"""Add Tri-Stream fields: website, types_of_business, owners, officers, niche flag, clearing classification.

Revision ID: 20260415_0008
Revises: 20260410_0007
Create Date: 2026-04-15 20:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260415_0008"
down_revision = "20260410_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_dealers", sa.Column("website", sa.String(length=512), nullable=True))
    op.add_column("broker_dealers", sa.Column("types_of_business", JSONB, nullable=True, server_default=None))
    op.add_column("broker_dealers", sa.Column("direct_owners", JSONB, nullable=True, server_default=None))
    op.add_column("broker_dealers", sa.Column("executive_officers", JSONB, nullable=True, server_default=None))
    op.add_column("broker_dealers", sa.Column("firm_operations_text", sa.Text, nullable=True))
    op.add_column(
        "broker_dealers",
        sa.Column(
            "clearing_classification",
            sa.String(length=32),
            nullable=True,
            comment="true_self_clearing | introducing | unknown",
        ),
    )
    op.add_column(
        "broker_dealers",
        sa.Column("is_niche_restricted", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_broker_dealers_clearing_classification", "broker_dealers", ["clearing_classification"])
    op.create_index("ix_broker_dealers_is_niche_restricted", "broker_dealers", ["is_niche_restricted"])


def downgrade() -> None:
    op.drop_index("ix_broker_dealers_is_niche_restricted", table_name="broker_dealers")
    op.drop_index("ix_broker_dealers_clearing_classification", table_name="broker_dealers")
    op.drop_column("broker_dealers", "is_niche_restricted")
    op.drop_column("broker_dealers", "clearing_classification")
    op.drop_column("broker_dealers", "firm_operations_text")
    op.drop_column("broker_dealers", "executive_officers")
    op.drop_column("broker_dealers", "direct_owners")
    op.drop_column("broker_dealers", "types_of_business")
    op.drop_column("broker_dealers", "website")
