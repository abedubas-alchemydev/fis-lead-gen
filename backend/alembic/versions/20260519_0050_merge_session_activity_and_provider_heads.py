"""Merge migration: unify 0048 (session.last_activity_at) + 0049 (outreach_sends.provider).

Revision ID: 20260519_0050
Revises: 20260519_0048, 20260519_0049
Create Date: 2026-05-19

Both PR-B (#434) and PR-C (#436) branched off ``20260518_0047`` to add
their own migrations:
  - 0048: ``session.last_activity_at`` + two indexes
  - 0049: ``outreach_sends.provider`` column

When both PRs merged into develop, alembic had two heads, and the
``alembic upgrade head`` step in CI failed with "Multiple head revisions
are present". This migration has no DDL of its own — it just sets
``down_revision`` to the tuple of both heads, collapsing the chain back
to a single head so the deploy pipeline can run migrations again.
"""

from __future__ import annotations

from collections.abc import Sequence


revision: str = "20260519_0050"
down_revision: tuple[str, ...] = ("20260519_0048", "20260519_0049")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No-op: the two parent migrations already applied all the schema
    # changes; this revision exists solely to merge the heads.
    pass


def downgrade() -> None:
    # No-op for the same reason. Rolling back the chain means rolling
    # back the parent migrations independently.
    pass
