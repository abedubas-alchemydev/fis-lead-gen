"""Tests for FINRA Form BD clearing reconciliation.

Two pure layers are covered here, no DB and no network:

1. ``brokercheck_pdf._parse_introducing_arrangements`` — the Form BD Item 12
   parser. Fixtures are verbatim text snippets in the layout pdfplumber
   produces for real BrokerCheck PDFs (verified live against CRD 322213
   ORTEX → Alpaca and CRD 120002 OBEX → Webull/Interactive Brokers).
2. ``finra_reconciler`` pure helpers — ``names_match``,
   ``decide_reconciliation``, ``_order_partners`` — the reconciliation
   decision factored out of the async DB path so it's unit-testable like
   ``refresh_all_orchestrator.decide_pipelines``.

The async ``reconcile_for_broker_dealer`` DB path is intentionally thin and
not exercised here (the local DB lacks pgvector, so the suite avoids real
DB); its branching all routes through ``decide_reconciliation``, which is
covered exhaustively below.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.brokercheck_pdf import (
    IntroducingArrangementRecord,
    _parse_introducing_arrangements,
)
from app.services.finra_reconciler import (
    STATUS_FINRA_EMPTY,
    STATUS_MATCH,
    STATUS_RECONCILED,
    _order_partners,
    decide_reconciliation,
    names_match,
)


# ── Form BD Item 12 parser ────────────────────────────────────────────────

# ORTEX SECURITIES (CRD 322213). Single introducing partner; the firm
# switched to Alpaca effective 2026-03-19 — the case that opened the audit.
_ORTEX_SECTION = """Introducing Arrangements
This firm does refer or introduce customers to other brokers and dealers.
Name: ALPACA SECURITIES LLC
CRD #: 288202
Business Address: 12 E. 49TH STREET
FLOOR 11
NEW YORK, NY 10017
Effective Date: 03/19/2026
Description: THE FIRM CLEARS ALL TRANSACTIONS ON A FULLY DISCLOSED BASIS
WITH ALPACA SECURITIES LLC"""

# OBEX SECURITIES (CRD 120002). Multiple introducing partners.
_OBEX_SECTION = """Introducing Arrangements
This firm does refer or introduce customers to other brokers and dealers.
Name: WEBULL FINANCIAL LLC
CRD #: 289063
Effective Date: 03/19/2026
Description: ENTERED INTO FULLY DISCLOSED CARRYING AGREEMENT.
Name: INTERACTIVE BROKERS LLC
CRD #: 36418
Effective Date: 08/23/2013
Description: OBEX HAS ENTERED INTO A FULLY DISCLOSED CLEARING AGREEMENT"""

# A firm with no introducing relationship (genuinely self-clearing / no
# customer accounts). FINRA prints the negative statement.
_NEGATIVE_SECTION = """Introducing Arrangements
This firm does not refer or introduce customers to other brokers and dealers."""

# Description body that runs into the next section header when FINRA omits a
# hard break (pdfplumber concatenates "Firm Operations").
_BLEED_SECTION = """Introducing Arrangements
This firm does refer or introduce customers to other brokers and dealers.
Name: APEX CLEARING CORPORATION
CRD #: 13071
Effective Date: 01/15/2026
Description: CLEARING BROKER-DEALER Firm Operations"""


class TestParseIntroducingArrangements:
    def test_single_partner_full_fields(self) -> None:
        recs = _parse_introducing_arrangements(_ORTEX_SECTION)
        assert len(recs) == 1
        rec = recs[0]
        assert rec.business_name == "ALPACA SECURITIES LLC"
        assert rec.crd == "288202"
        assert rec.effective_date == date(2026, 3, 19)
        assert rec.statement and rec.statement.startswith("This firm does refer")
        assert "FULLY DISCLOSED" in (rec.description or "")

    def test_multiple_partners(self) -> None:
        recs = _parse_introducing_arrangements(_OBEX_SECTION)
        assert [r.business_name for r in recs] == [
            "WEBULL FINANCIAL LLC",
            "INTERACTIVE BROKERS LLC",
        ]
        assert recs[0].effective_date == date(2026, 3, 19)
        assert recs[1].effective_date == date(2013, 8, 23)

    def test_negative_statement_returns_empty(self) -> None:
        assert _parse_introducing_arrangements(_NEGATIVE_SECTION) == []

    def test_empty_section_returns_empty(self) -> None:
        assert _parse_introducing_arrangements("") == []

    def test_description_header_bleed_is_trimmed(self) -> None:
        recs = _parse_introducing_arrangements(_BLEED_SECTION)
        assert len(recs) == 1
        # "Firm Operations" is the next section's header bleeding into the
        # description body — it must be cut, leaving just the real text.
        assert recs[0].description == "CLEARING BROKER-DEALER"

    def test_missing_effective_date_is_none(self) -> None:
        section = (
            "Introducing Arrangements\n"
            "This firm does refer or introduce customers to other brokers and dealers.\n"
            "Name: SOME CLEARING LLC\n"
            "CRD #: 999999\n"
            "Description: CLEARS ON A FULLY DISCLOSED BASIS"
        )
        recs = _parse_introducing_arrangements(section)
        assert len(recs) == 1
        assert recs[0].effective_date is None
        assert recs[0].crd == "999999"


# ── names_match ───────────────────────────────────────────────────────────

class TestNamesMatch:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("Pershing LLC", "Pershing, LLC"),
            ("RBC Capital Markets, LLC", "RBC CAPITAL MARKETS LLC"),
            ("Apex Clearing Corporation", "APEX CLEARING CORPORATION"),
            ("National Financial Services LLC", "National Financial Services, LLC"),
        ],
    )
    def test_same_firm_variants_match(self, a: str, b: str) -> None:
        assert names_match(a, b)

    @pytest.mark.parametrize(
        "a,b",
        [
            ("RBC Clearing & Custody", "RBC Capital Markets, LLC"),
            ("Apex Clearing Corporation", "Alpaca Securities LLC"),
            ("Self-Clearing", "Alpaca Securities LLC"),
            ("Multiple Partners", "Pershing LLC"),
            ("Pershing LLC", ""),
            ("", "Pershing LLC"),
        ],
    )
    def test_distinct_or_empty_do_not_match(self, a: str, b: str) -> None:
        assert not names_match(a, b)


# ── decide_reconciliation ─────────────────────────────────────────────────

class TestDecideReconciliation:
    def test_finra_empty(self) -> None:
        d = decide_reconciliation("Self-Clearing", [])
        assert d.action == STATUS_FINRA_EMPTY
        assert d.primary_partner is None

    def test_match_named(self) -> None:
        d = decide_reconciliation("Alpaca Securities LLC", ["ALPACA SECURITIES LLC"])
        assert d.action == STATUS_MATCH
        assert d.primary_partner == "ALPACA SECURITIES LLC"

    def test_reconcile_self_clearing_missed(self) -> None:
        # The big audit class — we said Self-Clearing, FINRA has a partner.
        d = decide_reconciliation("Self-Clearing", ["APEX CLEARING CORPORATION"])
        assert d.action == STATUS_RECONCILED
        assert d.primary_partner == "APEX CLEARING CORPORATION"

    def test_reconcile_wrong_partner(self) -> None:
        # The ORTEX class — we said Apex, FINRA says Alpaca.
        d = decide_reconciliation("Apex Clearing Corporation", ["ALPACA SECURITIES LLC"])
        assert d.action == STATUS_RECONCILED
        assert d.primary_partner == "ALPACA SECURITIES LLC"

    def test_match_when_any_finra_partner_matches(self) -> None:
        # Multi-partner FINRA; our partner matches the second one → match.
        d = decide_reconciliation(
            "Interactive Brokers LLC",
            ["WEBULL FINANCIAL LLC", "INTERACTIVE BROKERS LLC"],
        )
        assert d.action == STATUS_MATCH

    def test_null_partner_with_finra_reconciles(self) -> None:
        d = decide_reconciliation(None, ["PERSHING LLC"])
        assert d.action == STATUS_RECONCILED
        assert d.primary_partner == "PERSHING LLC"

    def test_blank_finra_names_treated_as_empty(self) -> None:
        d = decide_reconciliation("Self-Clearing", ["", "  "])
        assert d.action == STATUS_FINRA_EMPTY


# ── _order_partners ───────────────────────────────────────────────────────

class TestOrderPartners:
    def test_latest_effective_date_first(self) -> None:
        recs = [
            IntroducingArrangementRecord("OLD CLEARING LLC", effective_date=date(2013, 8, 23)),
            IntroducingArrangementRecord("NEW CLEARING LLC", effective_date=date(2026, 3, 19)),
        ]
        ordered = _order_partners(recs)
        assert [r.business_name for r in ordered] == ["NEW CLEARING LLC", "OLD CLEARING LLC"]

    def test_undated_sorts_last(self) -> None:
        recs = [
            IntroducingArrangementRecord("UNDATED LLC", effective_date=None),
            IntroducingArrangementRecord("DATED LLC", effective_date=date(2020, 1, 1)),
        ]
        ordered = _order_partners(recs)
        assert ordered[0].business_name == "DATED LLC"
        assert ordered[1].business_name == "UNDATED LLC"
