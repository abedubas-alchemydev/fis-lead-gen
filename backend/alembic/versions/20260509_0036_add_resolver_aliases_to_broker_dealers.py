"""Add resolver_aliases to broker_dealers.

Revision ID: 20260509_0036
Revises: 20260508_0035
Create Date: 2026-05-09

The website resolver's domain-anchor gate keys off tokens generated
from the firm's legal name plus its FINRA-supplied DBAs (``dba_names``).
That works for firms that register their brand-domain ownership with
FINRA as a trade name, but it leaves a structural blind spot for two
cohorts:

  1. Short-acronym brand names whose remaining words are corporate
     suffixes — e.g., ``BOFA SECURITIES, INC.`` produces only one weak
     anchor token (``"bofasecu"``) because "BOFA" is below the 5-char
     per-word minimum and "SECURITIES" / "INC" are stop-words.
  2. Subsidiary firms that operate under a parent-company domain
     without DBA-registering the parent's name — Bank of America
     Securities's actual web presence is on ``bankofamerica.com``,
     but FINRA's ``firm_other_names`` for BOFA SECURITIES does not
     list "Bank of America" as a trade name.

This column holds an LLM-generated alias list (parent-company brand
expansions, acronym expansions, common stylized variants) that
augments the FINRA-sourced DBA pool when the resolver builds its
token set. Kept SEPARATE from ``dba_names`` so we preserve provenance
— FINRA-sourced trade names retain their first-class column, and the
LLM-augmented alternates are auditable in their own column.

Additive, nullable, no constraint changes — safe to ship before the
backfill runs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "20260509_0036"
down_revision: str | None = "20260508_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broker_dealers",
        sa.Column(
            "resolver_aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("broker_dealers", "resolver_aliases")
