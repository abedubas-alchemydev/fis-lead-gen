"""Auto-grant institutional_investors feature permission to existing INVESTORS users.

Revision ID: 20260518_0047
Revises: 20260518_0046
Create Date: 2026-05-18

The new Institutional Investors firm list and the legacy Insider
Transactions surface share a conceptual "Investors" name. Any user who
already had the ``investors`` permission gets the new
``institutional_investors`` permission granted in the same migration
so the rebrand doesn't accidentally hide the surface they were
already entitled to see.

Admins implicitly bypass every feature gate via
``services.auth.ensure_feature`` -- this migration affects viewer
accounts only. If the org wants a tiered rollout instead, drop this
migration before deploy.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260518_0047"
down_revision: str | None = "20260518_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent JSON-array append using ``coalesce`` so users with NULL
    # ``feature_permissions`` get an array, users with the new permission
    # already are skipped (no duplicate), and users missing the
    # ``investors`` permission are not affected.
    op.execute(
        """
        UPDATE "user"
        SET feature_permissions = (
            SELECT jsonb_agg(DISTINCT v)
            FROM jsonb_array_elements_text(
                coalesce(feature_permissions, '[]'::jsonb)
                || '["institutional_investors"]'::jsonb
            ) AS t(v)
        )
        WHERE feature_permissions ? 'investors'
          AND NOT (feature_permissions ? 'institutional_investors')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE "user"
        SET feature_permissions = (
            SELECT jsonb_agg(v)
            FROM jsonb_array_elements_text(feature_permissions) AS t(v)
            WHERE v <> 'institutional_investors'
        )
        WHERE feature_permissions ? 'institutional_investors'
        """
    )
