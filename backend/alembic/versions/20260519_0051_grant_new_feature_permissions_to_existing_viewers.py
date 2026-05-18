"""Auto-grant 5 new feature permissions to existing viewers.

Revision ID: 20260519_0051
Revises: 20260519_0050
Create Date: 2026-05-19

Five new feature keys (``alerts``, ``email_extractor``, ``sent_outreach``,
``my_favorites``, ``visited_firms``) gate the corresponding sidebar nav
items so admins can curate per-user access from
``/settings/users/[id]``. All five surfaces shipped today ungated — every
viewer already sees them — so this migration backfills the keys for
existing viewer accounts to preserve current behavior on the rollout
boundary. Admins then curate downward.

Admins implicitly bypass every feature gate via
``services.auth.ensure_feature``; this migration affects viewer accounts
only. New signups continue to default to ``[]`` (status=pending) and get
permissions assigned when an admin approves them.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260519_0051"
down_revision: str | None = "20260519_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NEW_KEYS_JSON = (
    '["alerts","email_extractor","sent_outreach","my_favorites","visited_firms"]'
)


def upgrade() -> None:
    # Idempotent JSON-array merge via ``jsonb_agg(DISTINCT ...)``. Mirrors
    # migration ``20260518_0047`` (institutional_investors backfill).
    op.execute(
        f"""
        UPDATE "user"
        SET feature_permissions = (
            SELECT jsonb_agg(DISTINCT v)
            FROM jsonb_array_elements_text(
                coalesce(feature_permissions, '[]'::jsonb)
                || '{_NEW_KEYS_JSON}'::jsonb
            ) AS t(v)
        )
        WHERE role = 'viewer'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE "user"
        SET feature_permissions = (
            SELECT coalesce(jsonb_agg(v), '[]'::jsonb)
            FROM jsonb_array_elements_text(feature_permissions) AS t(v)
            WHERE v NOT IN (
                'alerts',
                'email_extractor',
                'sent_outreach',
                'my_favorites',
                'visited_firms'
            )
        )
        WHERE role = 'viewer'
          AND feature_permissions ?| array[
                'alerts',
                'email_extractor',
                'sent_outreach',
                'my_favorites',
                'visited_firms'
              ]
        """
    )
