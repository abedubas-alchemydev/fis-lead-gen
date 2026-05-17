"""Add feature_permissions to user.

Revision ID: 20260518_0040
Revises: 20260511_0039
Create Date: 2026-05-18

Adds a per-user feature-permissions array so admins can grant individual
viewers access to specific list features (Master List, Investment Advisors,
and the reserved-for-future Investors page) without granting access to all
of them. Admins implicitly bypass the gate in code, so their column value
is irrelevant and stays at the empty-array default.

Existing viewers are backfilled with the current shipping grant
(``master_list`` + ``investment_advisors``) to preserve today's behavior on
the rollout boundary — every authenticated user can hit both list APIs
today (only ``Depends(get_current_user)`` is in place), and a ``[]``
backfill would silently revoke that access from everyone before any admin
has a chance to curate it. Admins curate downward from the new
``/settings/users/[id]`` page after the FE ships.

New signups continue to default to ``[]`` post-migration — they're in
``status='pending'`` and can't hit any feature anyway until an admin
approves and grants.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "20260518_0040"
down_revision: str | None = "20260511_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "feature_permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE "user"
               SET feature_permissions = '["master_list","investment_advisors"]'::jsonb
             WHERE role = 'viewer'
            """
        )
    )


def downgrade() -> None:
    op.drop_column("user", "feature_permissions")
