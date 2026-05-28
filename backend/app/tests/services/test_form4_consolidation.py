"""Unit tests for the consolidation helper in ``form4_transactions``.

The full SQL aggregation is exercised end-to-end by manual staging
smoke; here we pin the deterministic mapping-row → dataclass transform,
because that's where the ``txn_count > 1 → price_per_share = None``
rule lives — the FE relies on that signal to hide the "@ price"
decoration on consolidated rows.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.services.form4_transactions import _row_to_consolidated


def _base_mapping(**overrides) -> dict:
    mapping = {
        "id": 42,
        "accession_number": "0001234567-26-000001",
        "is_derivative": False,
        "issuer_cik": "1234567",
        "issuer_name": "AAON, INC.",
        "issuer_ticker": "AAON",
        "reporting_owner_cik": "9999991",
        "reporting_owner_name": "KIDWELL CASEY",
        "reporting_owner_title": "Chief Administration Officer",
        "reporting_owner_is_director": False,
        "reporting_owner_is_officer": True,
        "reporting_owner_is_ten_pct": False,
        "reporting_owner_street1": "2425 S YUKON AVE",
        "reporting_owner_street2": None,
        "reporting_owner_city": "TULSA",
        "reporting_owner_state": "OK",
        "reporting_owner_zip": "74107",
        "security_title": "Common Stock",
        "transaction_date": date(2026, 5, 13),
        "transaction_code": "P",
        "ad_code": "A",
        "price_per_share": Decimal("82.4"),
        "sum_shares": Decimal("3153"),
        "sum_value": Decimal("254300.00"),
        "txn_count": 2,
        "enriched_phone": None,
        "enriched_email": None,
        "enriched_linkedin_url": None,
        "enriched_at": None,
        "source_filing_url": "https://www.sec.gov/Archives/edgar/data/123/0001234567-26-000001-index.htm",
        "filed_at": datetime(2026, 5, 13, 14, 30, tzinfo=timezone.utc),
        # reporting_owner_id is None until the insider is first favorited
        # (lazy-creates the reporting_owners row); is_favorited is False when
        # the caller passes no user_id (or the insider isn't on any of the
        # caller's lists).
        "reporting_owner_id": None,
        "is_favorited": False,
    }
    mapping.update(overrides)
    return mapping


def test_consolidated_row_hides_price_when_multi_txn() -> None:
    row = _row_to_consolidated(_base_mapping(txn_count=2))

    assert row.txn_count == 2
    assert row.price_per_share is None
    assert row.shares == 3153.0
    assert row.transaction_value == 254300.0


def test_consolidated_row_keeps_price_when_single_txn() -> None:
    row = _row_to_consolidated(
        _base_mapping(txn_count=1, sum_shares=Decimal("1069"), sum_value=Decimal("88087.6"))
    )

    assert row.txn_count == 1
    assert row.price_per_share == 82.4
    assert row.shares == 1069.0
    assert row.transaction_value == 88087.6


def test_consolidated_row_passes_through_leader_metadata() -> None:
    row = _row_to_consolidated(_base_mapping())

    assert row.id == 42
    assert row.issuer_ticker == "AAON"
    assert row.reporting_owner_name == "KIDWELL CASEY"
    assert row.ad_code == "A"
    assert row.source_filing_url is not None
    assert row.transaction_date == date(2026, 5, 13)


def test_consolidated_row_handles_null_aggregates() -> None:
    row = _row_to_consolidated(
        _base_mapping(sum_shares=None, sum_value=None, price_per_share=None)
    )

    assert row.shares is None
    assert row.transaction_value is None
    assert row.price_per_share is None
