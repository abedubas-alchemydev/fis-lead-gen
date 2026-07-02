"""add lei + charter_type to banks (OCC Institutions API enrichment)

The bank-charter watcher's reconcile phase now uses the official OCC
Institutions API (``api.occ.gov/institutions/active``) as its PRIMARY
charter-number ↔ FDIC CERT ↔ RSSD map (the ``national-by-name.xlsx``
workbook stays as the keyless fallback). The API additionally carries two
cheap enrichment fields the workbook doesn't:

- ``lei`` — the ISO 17442 Legal Entity Identifier (20 chars);
- ``charter_type`` — the OCC CharterType verbatim (e.g. 'National',
  'TrustCo-National'). Descriptive only: deliberately NOT used to infer
  ``digital_assets`` (plenty of TrustCo-National institutions are not
  digital-asset businesses).

Both nullable and additive — rows reconciled via the XLSX fallback (or
never reconciled at all: state charters) simply leave them NULL. Exposed
on the ``GET /api/v1/banks/{id}`` detail response.

Revision ID: 20260702_0002
Revises: 20260702_0001
Create Date: 2026-07-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260702_0002"
down_revision: str | None = "20260702_0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("banks", sa.Column("lei", sa.String(length=20), nullable=True))
    op.add_column("banks", sa.Column("charter_type", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("banks", "charter_type")
    op.drop_column("banks", "lei")
