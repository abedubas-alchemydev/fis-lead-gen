"""Unit tests pinning ``none_as_null=True`` on ``BrokerDealer`` JSONB list columns.

Without ``none_as_null=True``, SQLAlchemy persists Python ``None`` as the
JSONB scalar ``'null'`` (not SQL NULL). That value passes ``IS NOT NULL``
guards but crashes ``jsonb_array_elements_text`` with "cannot extract
elements from a scalar", which in turn 500s the master-list
types-of-business filter (see PR #247 + ``20260502_0024`` migration).

These tests inspect the SQLAlchemy column type metadata so the model
mapping can't quietly regress to bare ``JSONB``.
"""

from __future__ import annotations

import pytest

from app.models.broker_dealer import BrokerDealer


@pytest.mark.parametrize(
    "column_name",
    ["types_of_business", "direct_owners", "executive_officers"],
)
def test_jsonb_list_columns_use_none_as_null(column_name: str) -> None:
    """JSONB list-shape columns must store Python ``None`` as SQL NULL."""
    column = BrokerDealer.__table__.c[column_name]
    assert column.type.none_as_null is True, (
        f"BrokerDealer.{column_name} must use JSONB(none_as_null=True) so "
        f"None writes become SQL NULL instead of the JSONB scalar 'null' — "
        f"otherwise jsonb_array_elements_text and similar set-returning "
        f"functions crash on these rows."
    )
