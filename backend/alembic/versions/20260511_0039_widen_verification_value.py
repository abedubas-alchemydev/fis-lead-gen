"""widen verification.value from varchar(255) to text

Better Auth (frontend/lib/auth.ts) writes a JSON blob into
``verification.value`` during OAuth sign-in to persist PKCE state:
``codeVerifier`` (43-128 chars), plus ``callbackURL``,
``newUserCallbackURL``, and ``errorCallbackURL`` strings. The
combined JSON routinely exceeds 255 chars and trips Postgres
``22001 string_data_right_truncation``, surfacing on the frontend
as ``POST /api/auth/sign-in/social 500``.

Better Auth's reference schema uses TEXT for ``value`` (no length
cap); aligning our column avoids a class of "works for one
provider, breaks for another" bugs.

Revision ID: 20260511_0039
Revises: 20260511_0038
Create Date: 2026-05-11

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260511_0039"
down_revision: str | None = "20260511_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "verification",
        "value",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Truncates any rows whose ``value`` is currently >255 chars. Acceptable
    # since ``verification`` is an ephemeral CSRF/PKCE store — rows expire
    # within ~10 minutes of creation per Better Auth defaults.
    op.alter_column(
        "verification",
        "value",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
