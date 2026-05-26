"""Unit tests for the Form 4 XML parser.

Locks the contract of ``parse_form4_xml`` against the SEC's published
Form 4 XML shapes. Fixtures are minimal hand-written XMLs rather than
real anonymized filings — they're easier to reason about and exercise
the optional-element branches deterministically.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.form4_xml_parser import parse_form4_xml


_BASIC_NON_DERIVATIVE_XML = b"""\
<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0508</schemaVersion>
  <documentType>4</documentType>
  <periodOfReport>2026-05-09</periodOfReport>
  <issuer>
    <issuerCik>0000320193</issuerCik>
    <issuerName>Apple Inc.</issuerName>
    <issuerTradingSymbol>AAPL</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001214128</rptOwnerCik>
      <rptOwnerName>COOK TIMOTHY D</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerAddress>
      <rptOwnerStreet1>ONE APPLE PARK WAY</rptOwnerStreet1>
      <rptOwnerCity>CUPERTINO</rptOwnerCity>
      <rptOwnerState>CA</rptOwnerState>
      <rptOwnerZipCode>95014</rptOwnerZipCode>
    </reportingOwnerAddress>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>1</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <officerTitle>CEO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle>
        <value>Common Stock</value>
      </securityTitle>
      <transactionDate>
        <value>2026-05-09</value>
      </transactionDate>
      <transactionCoding>
        <transactionFormType>4</transactionFormType>
        <transactionCode>S</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>50000</value></transactionShares>
        <transactionPricePerShare><value>180.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_parses_issuer_cik_strips_leading_zeros() -> None:
    filing = parse_form4_xml(_BASIC_NON_DERIVATIVE_XML)
    assert filing is not None
    assert filing.issuer.cik == "320193"
    assert filing.issuer.name == "Apple Inc."
    assert filing.issuer.trading_symbol == "AAPL"


def test_parses_reporting_owner_name_address_relationship() -> None:
    filing = parse_form4_xml(_BASIC_NON_DERIVATIVE_XML)
    assert filing is not None
    assert len(filing.reporting_owners) == 1
    owner = filing.reporting_owners[0]
    assert owner.cik == "1214128"
    assert owner.name == "COOK TIMOTHY D"
    assert owner.address.street1 == "ONE APPLE PARK WAY"
    assert owner.address.city == "CUPERTINO"
    assert owner.address.state == "CA"
    assert owner.address.zip_code == "95014"
    assert owner.relationship.is_director is True
    assert owner.relationship.is_officer is True
    assert owner.relationship.is_ten_pct is False
    assert owner.relationship.officer_title == "CEO"


def test_parses_non_derivative_transaction() -> None:
    filing = parse_form4_xml(_BASIC_NON_DERIVATIVE_XML)
    assert filing is not None
    assert len(filing.transactions) == 1
    txn = filing.transactions[0]
    assert txn.is_derivative is False
    assert txn.security_title == "Common Stock"
    assert txn.transaction_date == date(2026, 5, 9)
    assert txn.transaction_code == "S"
    assert txn.ad_code == "D"
    assert txn.shares == Decimal("50000")
    assert txn.price_per_share == Decimal("180.00")


def test_rejects_non_form4_document_types() -> None:
    """Form 3 / Form 5 ownership docs share the same root tag.

    parse_form4_xml should return None rather than emit a Form 3
    transaction as if it were a Form 4 row.
    """
    xml = _BASIC_NON_DERIVATIVE_XML.replace(b"<documentType>4</documentType>", b"<documentType>3</documentType>")
    assert parse_form4_xml(xml) is None


def test_returns_none_on_malformed_xml() -> None:
    assert parse_form4_xml(b"<not-actually>xml") is None


def test_returns_none_when_no_issuer() -> None:
    xml = b"""<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>1</rptOwnerCik>
      <rptOwnerName>X</rptOwnerName>
    </reportingOwnerId>
  </reportingOwner>
</ownershipDocument>
"""
    assert parse_form4_xml(xml) is None


def test_handles_missing_optional_fields_in_address_and_title() -> None:
    """Older Form 4 schemas omit street2 / officerTitle / trading symbol."""
    xml = b"""<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer>
    <issuerCik>123</issuerCik>
    <issuerName>Tiny Corp</issuerName>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>456</rptOwnerCik>
      <rptOwnerName>SMITH JANE</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerAddress>
      <rptOwnerStreet1>100 MAIN ST</rptOwnerStreet1>
    </reportingOwnerAddress>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-04-01</value></transactionDate>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>10.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""
    filing = parse_form4_xml(xml)
    assert filing is not None
    assert filing.issuer.trading_symbol is None
    owner = filing.reporting_owners[0]
    assert owner.address.street2 is None
    assert owner.address.city is None
    assert owner.relationship.is_officer is False
    assert owner.relationship.officer_title is None


def test_collects_both_non_derivative_and_derivative_transactions() -> None:
    xml = b"""<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer>
    <issuerCik>1</issuerCik>
    <issuerName>Demo Inc</issuerName>
    <issuerTradingSymbol>DEMO</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>2</rptOwnerCik>
      <rptOwnerName>DOE JOHN</rptOwnerName>
    </reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-04-01</value></transactionDate>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <derivativeTable>
    <derivativeTransaction>
      <transactionDate><value>2026-04-02</value></transactionDate>
      <transactionAmounts>
        <transactionShares><value>200</value></transactionShares>
        <transactionPricePerShare><value>5</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>
"""
    filing = parse_form4_xml(xml)
    assert filing is not None
    assert len(filing.transactions) == 2
    assert filing.transactions[0].is_derivative is False
    assert filing.transactions[1].is_derivative is True


def test_skips_transaction_without_ad_code() -> None:
    """A/D code is the partition key for the two product lists — if EDGAR
    delivers a malformed row, we drop it rather than guess.
    """
    xml = b"""<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer>
    <issuerCik>1</issuerCik>
    <issuerName>Demo Inc</issuerName>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>2</rptOwnerCik>
      <rptOwnerName>DOE JOHN</rptOwnerName>
    </reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-04-01</value></transactionDate>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>10</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""
    filing = parse_form4_xml(xml)
    assert filing is not None
    assert filing.transactions == []
