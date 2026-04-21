"""Add bd_id to extraction_run/discovered_email + Apollo enrichment columns.

Revision ID: 20260423_0013
Revises: 20260422_0012
Create Date: 2026-04-23 00:00:00

- `extraction_run.bd_id`: lets a scan kicked off from a firm detail page
  remember which firm it came from; feeds per-firm scan history.
- `discovered_email.bd_id`: mirrors the scan-level link onto each row so
  queries don't need to join through extraction_run.
- Six enrichment columns on `discovered_email` for the Apollo reverse-email
  lookup (/people/match): name, title, linkedin_url, company, enriched_at,
  enrichment_status (not_enriched | enriched | no_match | error).

All additive; no existing columns altered. All FKs use ON DELETE SET NULL so
a firm deletion doesn't cascade into scan history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260423_0013"
down_revision: str | None = "20260422_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Scan-level firm link
    op.add_column(
        "extraction_run",
        sa.Column(
            "bd_id",
            sa.Integer(),
            sa.ForeignKey("broker_dealers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_extraction_run_bd_id"),
        "extraction_run",
        ["bd_id"],
        unique=False,
    )

    # Per-row firm link
    op.add_column(
        "discovered_email",
        sa.Column(
            "bd_id",
            sa.Integer(),
            sa.ForeignKey("broker_dealers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_discovered_email_bd_id"),
        "discovered_email",
        ["bd_id"],
        unique=False,
    )

    # Apollo enrichment columns
    op.add_column(
        "discovered_email",
        sa.Column("enriched_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "discovered_email",
        sa.Column("enriched_title", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "discovered_email",
        sa.Column("enriched_linkedin_url", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "discovered_email",
        sa.Column("enriched_company", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "discovered_email",
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "discovered_email",
        sa.Column(
            "enrichment_status",
            sa.String(length=32),
            server_default="not_enriched",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("discovered_email", "enrichment_status")
    op.drop_column("discovered_email", "enriched_at")
    op.drop_column("discovered_email", "enriched_company")
    op.drop_column("discovered_email", "enriched_linkedin_url")
    op.drop_column("discovered_email", "enriched_title")
    op.drop_column("discovered_email", "enriched_name")
    op.drop_index(op.f("ix_discovered_email_bd_id"), table_name="discovered_email")
    op.drop_column("discovered_email", "bd_id")
    op.drop_index(op.f("ix_extraction_run_bd_id"), table_name="extraction_run")
    op.drop_column("extraction_run", "bd_id")
