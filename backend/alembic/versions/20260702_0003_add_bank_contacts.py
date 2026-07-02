"""add bank_contacts (people from OCC charter-application PDFs)

The banks-vertical sibling of ``executive_contacts``: people extracted
from the public-portion charter-application PDFs already linked on
``banks.digital_asset_pdfs`` (https occ.gov URLs, stored by the watcher's
digital-assets phase).

Written exclusively by the conservative extractor
(``services/bank_contact_extraction.py``, run via
``scripts/watch_bank_charters.py --extract-contacts``): a person lands
only when the surrounding pattern is unambiguous — the contact-person
block, an organizers list, a "proposed <officer>" sentence, or
counsel-of-record (``role_context`` records which). Ambiguous hits are
logged and skipped, never guessed. ``page_number`` + ``context_snippet``
keep the raw-text receipt; ``source_url`` is the occ.gov PDF itself.

Dedupe/upsert key: ``(bank_id, name, coalesce(title,''), source)`` as a
unique EXPRESSION index — ``title`` is nullable and NULL != NULL in
Postgres, so the raw column can't participate in the key. Re-running the
extractor over the same PDFs is a no-op.

Read by ``GET /api/v1/banks/{id}`` (detail-only ``contacts`` array).
See docs/runbooks/bank-charter-watch.md, "Extracting contacts from
application PDFs".

Revision ID: 20260702_0003
Revises: 20260702_0002
Create Date: 2026-07-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260702_0003"
down_revision: str | None = "20260702_0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "bank_contacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bank_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        # Verbatim from the filing when unambiguous; NULL otherwise.
        sa.Column("title", sa.String(length=255), nullable=True),
        # 'contact_person' | 'organizer' | 'proposed_officer' | 'counsel' —
        # deliberately not an enum (new patterns must land, not crash).
        sa.Column("role_context", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column(
            "source", sa.String(length=64), nullable=False, server_default="application_pdf"
        ),
        # The https occ.gov public-portion PDF the person came from.
        sa.Column("source_url", sa.Text(), nullable=False),
        # 1-based page of the hit inside that PDF.
        sa.Column("page_number", sa.Integer(), nullable=True),
        # Short raw text around the hit — the extraction audit trail.
        sa.Column("context_snippet", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        # CASCADE: contacts must not outlive their bank row.
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_bank_contacts_bank_id", "bank_contacts", ["bank_id"])
    # Idempotency/upsert key. Expression index because title is nullable
    # (NULL != NULL would let duplicates through a plain unique constraint).
    op.create_index(
        "uq_bank_contacts_dedupe",
        "bank_contacts",
        ["bank_id", "name", sa.text("coalesce(title, '')"), "source"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_bank_contacts_dedupe", table_name="bank_contacts")
    op.drop_index("ix_bank_contacts_bank_id", table_name="bank_contacts")
    op.drop_table("bank_contacts")
