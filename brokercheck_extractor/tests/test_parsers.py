"""Parser regression tests using real PDFs from FINRA and SEC."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from brokercheck_extractor.derivation.clearing_classifier import (
    ClearingType,
    classify_clearing,
)
from brokercheck_extractor.derivation.yoy_calculator import compute_yoy
from brokercheck_extractor.parsers.finra_parser import parse_finra_pdf
from brokercheck_extractor.parsers.focus_parser import parse_focus_pdf

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# FINRA parser — Charles Schwab (active, fully populated)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def schwab_profile():
    pdf = (FIXTURES / "firm_5393_schwab.pdf").read_bytes()
    return parse_finra_pdf(pdf)


def test_finra_identity(schwab_profile):
    assert schwab_profile.crd_number == "5393"
    assert schwab_profile.sec_number == "8-16514"
    assert "SCHWAB" in schwab_profile.firm_name.upper()
    assert schwab_profile.is_registered is True


def test_finra_history(schwab_profile):
    assert schwab_profile.history.formation_date.isoformat() == "1971-04-01"
    assert schwab_profile.history.registration_date.isoformat() == "1971-06-13"
    assert schwab_profile.history.termination_date is None


def test_finra_types_of_business(schwab_profile):
    tob = schwab_profile.types_of_business
    assert tob.total == 6
    assert len(tob.services) == 6
    assert any("Investment advisory" in s for s in tob.services)
    # The section preamble fragment must not leak into services
    assert not any(s.startswith(("This ", "expects ", "Firm ")) for s in tob.services)


def test_finra_officers(schwab_profile):
    assert len(schwab_profile.officers) >= 8
    names = {o.name for o in schwab_profile.officers}
    assert any("SCHWAB HOLDINGS" in n for n in names)
    assert any("VERDESCHI" in n for n in names)
    # Multi-line positions must be fully captured
    craig = next(o for o in schwab_profile.officers if "CRAIG" in o.name)
    assert "SERVICES AND MARKETING" in craig.position


def test_finra_clearing_statement(schwab_profile):
    stmt = schwab_profile.operations.clearing_statement.lower()
    assert "hold or maintain" in stmt


def test_finra_clearing_classification(schwab_profile):
    verdict = classify_clearing(schwab_profile)
    assert verdict.classification == ClearingType.SELF_CLEARING
    assert verdict.confidence >= 0.9
    assert verdict.raw_text  # always preserved


# ---------------------------------------------------------------------------
# FOCUS parser — &Partners LLC 2025 Part III
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def andpartners_report():
    pdf = (FIXTURES / "xfocus_andpartners.pdf").read_bytes()
    return parse_focus_pdf(pdf)


def test_focus_identity(andpartners_report):
    assert andpartners_report.sec_file_number == "8-03774"
    assert "Partners" in andpartners_report.firm_name
    assert andpartners_report.period_ending.isoformat() == "2025-12-31"
    assert andpartners_report.period_beginning.isoformat() == "2025-01-01"


def test_focus_contact(andpartners_report):
    c = andpartners_report.contact
    assert c.full_name == "Michael C Schaller"
    assert "FinOp" in c.title
    assert "Controller" in c.title
    assert c.phone == "314-897-4920"
    assert c.email == "mike.schaller@andpartners.com"


def test_focus_auditor(andpartners_report):
    assert "Anders" in andpartners_report.auditor_name
    assert andpartners_report.auditor_pcaob_id == "2100"


def test_focus_financials(andpartners_report):
    f = andpartners_report.financials
    assert f.total_assets == Decimal("68899951")
    assert f.total_liabilities == Decimal("42164489")
    assert f.members_equity == Decimal("26735462")
    # Balance-sheet identity
    assert f.total_assets == f.total_liabilities + f.members_equity


# ---------------------------------------------------------------------------
# Derivation — YoY
# ---------------------------------------------------------------------------

def test_yoy_happy_path():
    y = compute_yoy(Decimal("110"), Decimal("100"))
    assert not y.insufficient_data
    assert y.growth_pct == pytest.approx(0.10)


def test_yoy_missing_prior():
    y = compute_yoy(Decimal("110"), None)
    assert y.insufficient_data
    assert y.growth_pct is None


def test_yoy_zero_prior():
    y = compute_yoy(Decimal("110"), Decimal("0"))
    assert y.insufficient_data
