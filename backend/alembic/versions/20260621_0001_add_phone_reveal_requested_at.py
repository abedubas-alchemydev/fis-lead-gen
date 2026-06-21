"""Add phone_reveal_requested_at to discovered_email.

Revision ID: 20260621_0001
Revises: 20260619_0001
Create Date: 2026-06-21

Time-bounds the email-extractor "finding phone…" spinner. The spinner is
driven by the computed ``DiscoveredEmail.phone_reveal_pending`` flag, whose
only exit was the async Apollo phone-reveal webhook writing a number back.
When Apollo never delivers one (ephemeral/thin person id, no mobile on file,
dropped callback) the flag stayed true forever and the row spun indefinitely.

This column records when the reveal was actually requested so the property can
gate on a TTL and self-resolve. The enrichment orchestrator stamps it to
``now()`` at reveal-request time (Apollo matched a person, reveal is
configured, no inline phone yet); after the TTL elapses the flag flips false
and the UI simply shows no phone.

Additive, nullable, no server default -- existing rows land NULL, which the
property reads as "not pending" (False). That intentionally clears every
currently-stuck row the instant the new code deploys: a legacy pending row has
no request timestamp, so it can no longer spin. Safe to ship without a
backfill; late webhooks still write the phone (the merge-if-null path is
unchanged -- the TTL only governs the spinner, not the data).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260621_0001"
down_revision: str | None = "20260619_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "discovered_email",
        sa.Column(
            "phone_reveal_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("discovered_email", "phone_reveal_requested_at")
