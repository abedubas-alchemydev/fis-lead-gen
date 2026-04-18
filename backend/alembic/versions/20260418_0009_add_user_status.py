"""Add user.status column for signup approval gate.

Revision ID: 20260418_0009
Revises: 20260415_0008
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260418_0009"
down_revision = "20260415_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.create_check_constraint(
        "ck_user_status_enum",
        "user",
        "status IN ('pending', 'active', 'rejected')",
    )
    # Every pre-existing user stays active so current admins aren't locked out.
    op.execute("UPDATE \"user\" SET status = 'active'")
    op.create_index("ix_user_status", "user", ["status"])


def downgrade() -> None:
    op.drop_index("ix_user_status", table_name="user")
    op.drop_constraint("ck_user_status_enum", "user", type_="check")
    op.drop_column("user", "status")
