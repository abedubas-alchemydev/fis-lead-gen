"""Convert JSONB scalar 'null' to SQL NULL on broker_dealers list columns.

Revision ID: 20260502_0024
Revises: 20260501_0023
Create Date: 2026-05-02

The ``types_of_business``, ``direct_owners``, and ``executive_officers``
columns are JSONB. They were originally declared without
``none_as_null=True``, so Python ``None`` writes from the FINRA / Form BD
ingestion paths landed as the JSONB scalar ``'null'`` instead of SQL
NULL. The bare ``IS NOT NULL`` guard in
``BrokerDealerRepository.list_types_of_business`` then let those rows
through to ``jsonb_array_elements_text``, which crashes with "cannot
extract elements from a scalar" — surfacing as a 500 on
``GET /api/v1/broker-dealers/types-of-business`` and breaking the
master-list types-of-business filter.

The query-level guard (``WHERE jsonb_typeof = 'array'``) and the
``none_as_null=True`` mapping in ``app.models.broker_dealer`` together
prevent any new bad rows. This migration cleans up the existing ones.
Audit at the time of writing showed 1 affected ``types_of_business``
row in prod; the sibling columns are normalized in the same sweep so
future aggregators don't trip the same issue.

Idempotent — re-running is a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260502_0024"
down_revision: str | None = "20260501_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE broker_dealers
        SET types_of_business = NULL
        WHERE types_of_business IS NOT NULL
          AND jsonb_typeof(types_of_business) = 'null'
        """
    )
    op.execute(
        """
        UPDATE broker_dealers
        SET direct_owners = NULL
        WHERE direct_owners IS NOT NULL
          AND jsonb_typeof(direct_owners) = 'null'
        """
    )
    op.execute(
        """
        UPDATE broker_dealers
        SET executive_officers = NULL
        WHERE executive_officers IS NOT NULL
          AND jsonb_typeof(executive_officers) = 'null'
        """
    )


def downgrade() -> None:
    # Intentionally a no-op: the downgrade target is "JSONB scalar null"
    # which was never a meaningful value — only an artifact of the missing
    # ``none_as_null=True`` mapping. There's no row-level distinction to
    # restore.
    pass
