"""Make broker_dealers.cik nullable for real FINRA-only firms.

Revision ID: 20260410_0007
Revises: 20260409_0006
Create Date: 2026-04-10 09:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260410_0007"
down_revision = "20260409_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "broker_dealers",
        "cik",
        existing_type=sa.String(length=32),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "broker_dealers",
        "cik",
        existing_type=sa.String(length=32),
        nullable=False,
    )
