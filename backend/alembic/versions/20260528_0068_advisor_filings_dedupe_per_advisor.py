"""Scope advisor_filings dedupe_key uniqueness per-advisor.

Revision ID: 20260528_0068
Revises: 20260528_0067
Create Date: 2026-05-28

The original ``20260508_0030`` migration declared
``UniqueConstraint('dedupe_key')`` -- a GLOBAL unique on the SEC
accession number. That trips with an ``IntegrityError`` the moment two
advisors share an SEC filer-agent (e.g. fund administrator) and refresh
the same accession number, because each advisor needs its own row even
though the accession is the same string.

Confirmed in production by IA 16817 (ALPHACENTRIC ADVISORS LLC) hitting
``UniqueViolation`` on ``0001580642-26-001910`` -- the same accession
already owned by IA 13236 (different advisor, same filer agent).

The fix matches the table's per-advisor intent (model docstring:
"Tracks Form ADV / Form ADV-W / Form 13F-HR amendments *per advisor*"):
swap the global constraint for ``UNIQUE(advisor_id, dedupe_key)``. The
existing dedupe loop in ``_run_refresh_filings`` already pre-loads
existing keys *for the advisor* and skips matches, so the new
constraint matches that read path 1:1.

Safe to ship without backfill -- no existing rows can be in conflict
because the OLD global constraint would have rejected them. Existing
rows trivially satisfy the new per-advisor constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260528_0068"
down_revision: str | None = "20260528_0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_advisor_filings_dedupe_key",
        "advisor_filings",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_advisor_filings_advisor_dedupe_key",
        "advisor_filings",
        ["advisor_id", "dedupe_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_advisor_filings_advisor_dedupe_key",
        "advisor_filings",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_advisor_filings_dedupe_key",
        "advisor_filings",
        ["dedupe_key"],
    )
