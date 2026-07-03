"""Add discovery/channel columns to bank_contacts (People parity with BD/IA).

Revision ID: 20260703_0002
Revises: 20260703_0001
Create Date: 2026-07-03

The bank vertical is gaining the same "People" experience the broker-dealer
and investment-advisor profiles already ship: AI contact discovery, a
LinkedIn/Mail/Phone channels column, and per-contact outreach. That means
``bank_contacts`` needs the exact discovery/channel columns
``executive_contacts`` / ``advisor_contacts`` carry (mirrored type-for-type
from ``app/models/executive_contact.py``):

- ``linkedin_url`` TEXT — the discovered public LinkedIn URL.
- ``emails`` / ``phones`` JSONB — multi-value provider hits (work + personal
  emails, mobile + work phones); the scalar ``email`` / ``phone`` columns
  stay for legacy PDF-extracted rows and the schema layer synthesizes a
  1-element array from them on read.
- ``apollo_person_id`` VARCHAR(64), indexed — Apollo's MongoDB-style person
  id, so the shared async phone-reveal webhook (``webhooks_apollo.py``) can
  correlate a late callback back to a bank_contacts row exactly as it already
  does for the other three contact tables.
- ``discovery_source`` VARCHAR(32) / ``discovery_confidence`` NUMERIC(5,2) —
  the fine-grained provider attribution + its 0..100 confidence.

It also relaxes ``role_context`` and ``source_url`` to NULLABLE. Those two
are populated only by the PDF extractor (which knows WHY a person appears in
a filing and WHICH occ.gov PDF they came from); discovery-created rows have
neither, so the NOT NULL constraints would reject them. PDF-extracted rows
still set both.

The existing ``uq_bank_contacts_dedupe`` unique expression index
``(bank_id, name, coalesce(title,''), source)`` is untouched — discovery
rows carry a distinct ``source`` (e.g. ``apollo`` / ``hunter``) so they never
collide with the ``application_pdf`` rows.

Additive + nullable; no data backfill.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260703_0002"
down_revision: str | None = "20260703_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bank_contacts",
        sa.Column("linkedin_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "bank_contacts",
        sa.Column("emails", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "bank_contacts",
        sa.Column("phones", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "bank_contacts",
        sa.Column("apollo_person_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "bank_contacts",
        sa.Column("discovery_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "bank_contacts",
        sa.Column("discovery_confidence", sa.Numeric(precision=5, scale=2), nullable=True),
    )
    # Indexed: the phone-reveal webhook does WHERE apollo_person_id = :id.
    op.create_index(
        "ix_bank_contacts_apollo_person_id",
        "bank_contacts",
        ["apollo_person_id"],
    )

    # Discovery-created rows have no filing provenance — relax the two
    # PDF-only columns so those rows are legal. PDF-extracted rows keep
    # setting both.
    op.alter_column("bank_contacts", "role_context", existing_type=sa.String(length=32), nullable=True)
    op.alter_column("bank_contacts", "source_url", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Restore NOT NULL first. A downgrade past this point with discovery rows
    # on file (role_context/source_url NULL) will fail by design — reverting
    # requires deciding what to do with those channel-only rows.
    op.alter_column("bank_contacts", "source_url", existing_type=sa.Text(), nullable=False)
    op.alter_column("bank_contacts", "role_context", existing_type=sa.String(length=32), nullable=False)

    op.drop_index("ix_bank_contacts_apollo_person_id", table_name="bank_contacts")
    op.drop_column("bank_contacts", "discovery_confidence")
    op.drop_column("bank_contacts", "discovery_source")
    op.drop_column("bank_contacts", "apollo_person_id")
    op.drop_column("bank_contacts", "phones")
    op.drop_column("bank_contacts", "emails")
    op.drop_column("bank_contacts", "linkedin_url")
