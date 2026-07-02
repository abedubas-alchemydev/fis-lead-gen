"""add Tier-2 Apollo enrichment bookkeeping to bank_contacts

The people extracted from OCC charter-application PDFs
(``20260702_0003``) mostly land with NULL email/phone (and often a NULL
title) — the public portion of a filing rarely prints channels. The
Tier-2 paid enrichment job (``services/bank_contact_enrichment.py``, run
via ``scripts/enrich_bank_contacts.py``) fills those gaps from Apollo
``/people/match``, one credit per person lookup.

Two additive, nullable bookkeeping columns make that job idempotent:

- ``enriched_at`` — timestamp of the last PAID lookup that reached a
  decision (matched OR no-match). NULL = never attempted. A re-run skips
  every stamped row, so the credit is spent at most once per contact.
  Provider errors (timeouts, 429/5xx retry exhaustion) deliberately do
  NOT stamp — a transient Apollo outage must not permanently mark the
  row as attempted.
- ``enrich_status`` — 'matched' | 'no_match', the decision itself.
  Deliberately not an enum (the vocabulary must be able to grow without
  a migration), mirroring ``role_context`` on the same table.

Both NULL for every existing row (never attempted). The API payloads
(``GET /api/v1/banks/{id}`` detail ``contacts`` array) are deliberately
unchanged — these are ops bookkeeping columns, not product fields.

Revision ID: 20260702_0004
Revises: 20260702_0003
Create Date: 2026-07-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260702_0004"
down_revision: str | None = "20260702_0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "bank_contacts",
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bank_contacts",
        sa.Column("enrich_status", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bank_contacts", "enrich_status")
    op.drop_column("bank_contacts", "enriched_at")
