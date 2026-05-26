"""Form 4 XML parser.

Pure (no HTTP, no DB) parsing of SEC Form 4 ``ownershipDocument`` XML
into structured dataclasses. The watcher pipeline feeds the result to
``form4_watcher._build_transaction_records`` which applies the $50K
value filter and emits one DB-bound record per
(reportingOwner × transaction) pair.

Permissive by design — Form 4 XML schemas (X0306 / X0405 / X0508 / …)
have shifted element ordering and made several leaf fields optional
across the years. Missing optional leaves return ``None`` rather than
raising; structurally invalid documents return ``None`` from
``parse_form4_xml`` rather than partial dataclasses.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedIssuer:
    cik: str
    name: str
    trading_symbol: str | None


@dataclass(frozen=True, slots=True)
class ParsedReportingOwnerRelationship:
    is_director: bool
    is_officer: bool
    is_ten_pct: bool
    officer_title: str | None


@dataclass(frozen=True, slots=True)
class ParsedAddress:
    street1: str | None
    street2: str | None
    city: str | None
    state: str | None
    zip_code: str | None


@dataclass(frozen=True, slots=True)
class ParsedReportingOwner:
    cik: str
    name: str
    relationship: ParsedReportingOwnerRelationship
    address: ParsedAddress


@dataclass(frozen=True, slots=True)
class ParsedTransaction:
    is_derivative: bool
    security_title: str | None
    transaction_date: date
    transaction_code: str | None
    ad_code: str  # 'A' or 'D'
    shares: Decimal | None
    price_per_share: Decimal | None


@dataclass(frozen=True, slots=True)
class ParsedForm4Filing:
    issuer: ParsedIssuer
    reporting_owners: list[ParsedReportingOwner]
    transactions: list[ParsedTransaction]


_NS_STRIP = re.compile(r"\{[^}]*\}")


def _strip_ns(tag: str) -> str:
    return _NS_STRIP.sub("", tag)


def _local(elem: ET.Element, name: str) -> ET.Element | None:
    """Find the first child whose local-name matches ``name``.

    Namespace-agnostic. Form 4 XML typically declares no namespace but
    some intermediaries (filing agents, archival mirrors) inject one.
    """
    for child in elem:
        if _strip_ns(child.tag) == name:
            return child
    return None


def _text(elem: ET.Element | None) -> str | None:
    """Pull text from an element, transparently descending into ``<value>``.

    Form 4 wraps most leaf data in ``<value>`` children (e.g.
    ``<transactionDate><value>2026-05-09</value></transactionDate>``) but
    some sibling fields are raw text (e.g. ``<isDirector>1</isDirector>``).
    Try the ``<value>`` child first; fall back to direct text.
    """
    if elem is None:
        return None
    value_child = _local(elem, "value")
    if value_child is not None and value_child.text is not None:
        text = value_child.text.strip()
        return text or None
    if elem.text is not None:
        text = elem.text.strip()
        return text or None
    return None


def _bool_flag(elem: ET.Element | None, child_name: str) -> bool:
    """Read a boolean Form 4 flag (``<isDirector>1</isDirector>``).

    Treats "1" and "true" (case-insensitive) as True; anything else
    (including missing element) as False.
    """
    if elem is None:
        return False
    raw = _text(_local(elem, child_name))
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true"}


def _decimal(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        return None


def _date(raw: str | None) -> date | None:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _parse_issuer(root: ET.Element) -> ParsedIssuer | None:
    issuer_elem = _local(root, "issuer")
    if issuer_elem is None:
        return None
    cik = _text(_local(issuer_elem, "issuerCik"))
    name = _text(_local(issuer_elem, "issuerName"))
    if not cik or not name:
        return None
    return ParsedIssuer(
        cik=cik.lstrip("0") or "0",
        name=name,
        trading_symbol=_text(_local(issuer_elem, "issuerTradingSymbol")),
    )


def _parse_reporting_owner(elem: ET.Element) -> ParsedReportingOwner | None:
    id_elem = _local(elem, "reportingOwnerId")
    if id_elem is None:
        return None
    cik = _text(_local(id_elem, "rptOwnerCik"))
    name = _text(_local(id_elem, "rptOwnerName"))
    if not cik or not name:
        return None

    rel_elem = _local(elem, "reportingOwnerRelationship")
    relationship = ParsedReportingOwnerRelationship(
        is_director=_bool_flag(rel_elem, "isDirector"),
        is_officer=_bool_flag(rel_elem, "isOfficer"),
        is_ten_pct=_bool_flag(rel_elem, "isTenPercentOwner"),
        officer_title=_text(_local(rel_elem, "officerTitle")) if rel_elem is not None else None,
    )

    addr_elem = _local(elem, "reportingOwnerAddress")
    address = ParsedAddress(
        street1=_text(_local(addr_elem, "rptOwnerStreet1")) if addr_elem is not None else None,
        street2=_text(_local(addr_elem, "rptOwnerStreet2")) if addr_elem is not None else None,
        city=_text(_local(addr_elem, "rptOwnerCity")) if addr_elem is not None else None,
        state=_text(_local(addr_elem, "rptOwnerState")) if addr_elem is not None else None,
        zip_code=_text(_local(addr_elem, "rptOwnerZipCode")) if addr_elem is not None else None,
    )

    return ParsedReportingOwner(
        cik=cik.lstrip("0") or "0",
        name=name,
        relationship=relationship,
        address=address,
    )


def _parse_transaction(elem: ET.Element, *, is_derivative: bool) -> ParsedTransaction | None:
    txn_date = _date(_text(_local(elem, "transactionDate")))
    if txn_date is None:
        return None

    amounts = _local(elem, "transactionAmounts")
    ad_raw = _text(_local(amounts, "transactionAcquiredDisposedCode")) if amounts is not None else None
    if not ad_raw or ad_raw.upper() not in {"A", "D"}:
        return None
    ad_code = ad_raw.upper()

    coding = _local(elem, "transactionCoding")
    transaction_code = _text(_local(coding, "transactionCode")) if coding is not None else None

    security_title = _text(_local(elem, "securityTitle"))

    shares = _decimal(_text(_local(amounts, "transactionShares"))) if amounts is not None else None
    price = (
        _decimal(_text(_local(amounts, "transactionPricePerShare"))) if amounts is not None else None
    )

    return ParsedTransaction(
        is_derivative=is_derivative,
        security_title=security_title,
        transaction_date=txn_date,
        transaction_code=transaction_code,
        ad_code=ad_code,
        shares=shares,
        price_per_share=price,
    )


def parse_form4_xml(xml_bytes: bytes) -> ParsedForm4Filing | None:
    """Parse a Form 4 ``ownershipDocument`` XML payload.

    Returns ``None`` if the document is malformed, isn't a Form 4, or
    has no issuer / reportingOwner. Returns a ``ParsedForm4Filing`` with
    a possibly-empty ``transactions`` list otherwise — the caller is
    expected to apply value / A-D filters downstream.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("Form 4 XML parse failed: %s", exc)
        return None

    if _strip_ns(root.tag) != "ownershipDocument":
        logger.warning(
            "Form 4 XML root is %r, expected ownershipDocument.", _strip_ns(root.tag)
        )
        return None

    doc_type = _text(_local(root, "documentType"))
    if doc_type and doc_type.strip() not in {"4", "4/A"}:
        # Could be a Form 3 or Form 5 share-data file mis-routed here.
        logger.debug("Skipping ownershipDocument with documentType=%r", doc_type)
        return None

    issuer = _parse_issuer(root)
    if issuer is None:
        return None

    reporting_owners: list[ParsedReportingOwner] = []
    for child in root:
        if _strip_ns(child.tag) != "reportingOwner":
            continue
        parsed = _parse_reporting_owner(child)
        if parsed is not None:
            reporting_owners.append(parsed)

    if not reporting_owners:
        return None

    transactions: list[ParsedTransaction] = []
    non_derivative_table = _local(root, "nonDerivativeTable")
    if non_derivative_table is not None:
        for child in non_derivative_table:
            if _strip_ns(child.tag) != "nonDerivativeTransaction":
                continue
            parsed = _parse_transaction(child, is_derivative=False)
            if parsed is not None:
                transactions.append(parsed)

    derivative_table = _local(root, "derivativeTable")
    if derivative_table is not None:
        for child in derivative_table:
            if _strip_ns(child.tag) != "derivativeTransaction":
                continue
            parsed = _parse_transaction(child, is_derivative=True)
            if parsed is not None:
                transactions.append(parsed)

    return ParsedForm4Filing(
        issuer=issuer,
        reporting_owners=reporting_owners,
        transactions=transactions,
    )
