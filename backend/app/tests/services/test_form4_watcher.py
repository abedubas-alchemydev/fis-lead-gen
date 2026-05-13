"""Unit tests for ``form4_watcher._build_transaction_records``.

The HTTP / EFTS / DB layers are covered by the endpoint smoke tests
plus a manual staging run; the value-filter + cartesian-product
record builder is the deterministic transform worth pinning here.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.services.form4_watcher import _build_transaction_records, _dedupe_key
from app.services.form4_xml_parser import (
    ParsedAddress,
    ParsedForm4Filing,
    ParsedIssuer,
    ParsedReportingOwner,
    ParsedReportingOwnerRelationship,
    ParsedTransaction,
)


def _owner(*, cik: str = "1214128", name: str = "COOK TIMOTHY D") -> ParsedReportingOwner:
    return ParsedReportingOwner(
        cik=cik,
        name=name,
        relationship=ParsedReportingOwnerRelationship(
            is_director=True,
            is_officer=True,
            is_ten_pct=False,
            officer_title="CEO",
        ),
        address=ParsedAddress(
            street1="ONE APPLE PARK WAY",
            street2=None,
            city="CUPERTINO",
            state="CA",
            zip_code="95014",
        ),
    )


def _txn(
    *,
    shares: Decimal | None = Decimal("1000"),
    price: Decimal | None = Decimal("180"),
    ad_code: str = "D",
    is_derivative: bool = False,
    transaction_date: date = date(2026, 5, 9),
) -> ParsedTransaction:
    return ParsedTransaction(
        is_derivative=is_derivative,
        security_title="Common Stock",
        transaction_date=transaction_date,
        transaction_code="S",
        ad_code=ad_code,
        shares=shares,
        price_per_share=price,
    )


def _filing(
    *, owners: list[ParsedReportingOwner], transactions: list[ParsedTransaction]
) -> ParsedForm4Filing:
    return ParsedForm4Filing(
        issuer=ParsedIssuer(cik="320193", name="Apple Inc.", trading_symbol="AAPL"),
        reporting_owners=owners,
        transactions=transactions,
    )


_ACCESSION = "0000320193-26-000001"
_FILED_AT = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
_SOURCE_URL = "https://example.test/source"


def test_emits_one_record_per_owner_x_transaction_pair() -> None:
    filing = _filing(
        owners=[_owner(cik="1", name="A A"), _owner(cik="2", name="B B")],
        transactions=[
            _txn(shares=Decimal("1000"), price=Decimal("100"), ad_code="A"),
            _txn(shares=Decimal("2000"), price=Decimal("100"), ad_code="D"),
        ],
    )
    records = _build_transaction_records(
        filing,
        accession_number=_ACCESSION,
        filed_at=_FILED_AT,
        source_filing_url=_SOURCE_URL,
        min_value=Decimal("50000"),
    )
    assert len(records) == 4
    # All four pass the floor ($100K and $200K).
    assert {r.reporting_owner_cik for r in records} == {"1", "2"}
    assert {r.ad_code for r in records} == {"A", "D"}


def test_drops_transactions_below_min_value() -> None:
    filing = _filing(
        owners=[_owner()],
        transactions=[
            _txn(shares=Decimal("100"), price=Decimal("100"), ad_code="A"),  # 10K
            _txn(shares=Decimal("1000"), price=Decimal("100"), ad_code="D"),  # 100K
        ],
    )
    records = _build_transaction_records(
        filing,
        accession_number=_ACCESSION,
        filed_at=_FILED_AT,
        source_filing_url=_SOURCE_URL,
        min_value=Decimal("50000"),
    )
    assert len(records) == 1
    assert records[0].ad_code == "D"
    assert records[0].transaction_value == 100000.0


def test_drops_transactions_with_missing_price_or_shares() -> None:
    """Gifts / awards typically file with no price → value is None →
    they fall below any value floor. Confirm they're not silently
    treated as zero-value pass-throughs.
    """
    filing = _filing(
        owners=[_owner()],
        transactions=[
            _txn(shares=Decimal("1000"), price=None, ad_code="A"),
            _txn(shares=None, price=Decimal("100"), ad_code="A"),
            _txn(shares=Decimal("1000"), price=Decimal("100"), ad_code="A"),
        ],
    )
    records = _build_transaction_records(
        filing,
        accession_number=_ACCESSION,
        filed_at=_FILED_AT,
        source_filing_url=_SOURCE_URL,
        min_value=Decimal("50000"),
    )
    assert len(records) == 1
    assert records[0].shares == 1000.0


def test_includes_derivative_transactions_when_they_clear_the_floor() -> None:
    filing = _filing(
        owners=[_owner()],
        transactions=[
            _txn(
                shares=Decimal("10000"),
                price=Decimal("10"),
                ad_code="A",
                is_derivative=True,
            ),
        ],
    )
    records = _build_transaction_records(
        filing,
        accession_number=_ACCESSION,
        filed_at=_FILED_AT,
        source_filing_url=_SOURCE_URL,
        min_value=Decimal("50000"),
    )
    assert len(records) == 1
    assert records[0].is_derivative is True


def test_dedupe_key_distinguishes_derivative_from_non_derivative_table() -> None:
    """Both tables index from 0; the marker prevents collision."""
    nd = _dedupe_key(
        accession=_ACCESSION, is_derivative=False, owner_cik="1", index=0
    )
    d = _dedupe_key(accession=_ACCESSION, is_derivative=True, owner_cik="1", index=0)
    assert nd != d
    assert ":nd:" in nd
    assert ":d:" in d


def test_records_carry_issuer_owner_and_transaction_fields_through() -> None:
    filing = _filing(
        owners=[_owner(cik="42", name="DOE JANE")],
        transactions=[_txn(ad_code="A", shares=Decimal("1000"), price=Decimal("100"))],
    )
    records = _build_transaction_records(
        filing,
        accession_number=_ACCESSION,
        filed_at=_FILED_AT,
        source_filing_url=_SOURCE_URL,
        min_value=Decimal("0"),
    )
    assert len(records) == 1
    r = records[0]
    assert r.issuer_cik == "320193"
    assert r.issuer_ticker == "AAPL"
    assert r.reporting_owner_cik == "42"
    assert r.reporting_owner_name == "DOE JANE"
    assert r.reporting_owner_title == "CEO"
    assert r.transaction_date == date(2026, 5, 9)
    assert r.ad_code == "A"
    assert r.shares == 1000.0
    assert r.price_per_share == 100.0
    assert r.transaction_value == 100000.0
    assert r.source_filing_url == _SOURCE_URL
    assert r.filed_at == _FILED_AT
    assert r.dedupe_key == f"Form 4:{_ACCESSION}:nd:42:0"


def test_empty_owners_or_empty_transactions_yields_no_records() -> None:
    assert (
        _build_transaction_records(
            _filing(owners=[], transactions=[_txn()]),
            accession_number=_ACCESSION,
            filed_at=_FILED_AT,
            source_filing_url=_SOURCE_URL,
            min_value=Decimal("0"),
        )
        == []
    )
    assert (
        _build_transaction_records(
            _filing(owners=[_owner()], transactions=[]),
            accession_number=_ACCESSION,
            filed_at=_FILED_AT,
            source_filing_url=_SOURCE_URL,
            min_value=Decimal("0"),
        )
        == []
    )
