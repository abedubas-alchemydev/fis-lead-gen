"""Hybrid pipeline regression tests."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from brokercheck_extractor.parsers.finra_parser import parse_finra_pdf
from brokercheck_extractor.parsers.focus_parser import parse_focus_pdf
from brokercheck_extractor.orchestrator_hybrid import _text_sample
from brokercheck_extractor.schema.models import (
    FinancialStanding,
    FirmHistory,
    FirmProfile,
    FirmOperations,
    FocusReport,
    IntroducingArrangement,
    PrimaryContact,
    TypesOfBusiness,
)
from brokercheck_extractor.validation.confidence import score_finra, score_focus
from brokercheck_extractor.validation.cross_validator import (
    AgreementLevel,
    cross_validate_finra,
    cross_validate_focus,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Confidence scoring — modern vs legacy PDF
# ---------------------------------------------------------------------------

def test_confidence_schwab_high():
    pdf = (FIXTURES / "firm_5393_schwab.pdf").read_bytes()
    profile = parse_finra_pdf(pdf)
    conf = score_finra(profile, raw_text_sample=_text_sample(pdf))
    assert conf.score >= 0.95
    assert not conf.needs_llm_fallback
    assert conf.reasons == []


def test_confidence_rh_securities_flags_legacy_pdf():
    """R H Securities is the legacy/space-collapsed case. The scorer MUST
    detect this and route to LLM fallback."""
    pdf = (FIXTURES / "firm_10997_rhsecurities.pdf").read_bytes()
    profile = parse_finra_pdf(pdf)
    conf = score_finra(profile, raw_text_sample=_text_sample(pdf))
    assert conf.score < 0.5, f"expected low confidence, got {conf.score}"
    assert conf.needs_llm_fallback
    assert any("space_collapse" in r for r in conf.reasons)


# ---------------------------------------------------------------------------
# Cross-validator — merge behavior
# ---------------------------------------------------------------------------

def _simulated_good_rh_llm() -> FirmProfile:
    return FirmProfile(
        crd_number="10997",
        sec_number="8-28229",
        firm_name="R H SECURITIES",
        is_registered=False,
        history=FirmHistory(
            formation_date=date(1982, 5, 12),
            registration_date=date(1983, 1, 5),
            termination_date=date(1987, 8, 6),
        ),
        types_of_business=TypesOfBusiness(
            total=1,
            services=[
                "Broker or dealer selling tax shelters or limited partnerships in primary distributions"
            ],
        ),
        operations=FirmOperations(),
    )


def test_xval_fills_deterministic_gaps():
    """LLM values should populate fields where deterministic parser found null."""
    rh_pdf = (FIXTURES / "firm_10997_rhsecurities.pdf").read_bytes()
    deterministic = parse_finra_pdf(rh_pdf)
    llm = _simulated_good_rh_llm()

    merged, xval = cross_validate_finra(deterministic, llm)

    assert merged.history.formation_date == date(1982, 5, 12)
    assert merged.history.registration_date == date(1983, 1, 5)
    assert merged.history.termination_date == date(1987, 8, 6)

    # Dates were deterministic-null → llm_only resolution
    llm_only_fields = {
        d.field_name for d in xval.diffs if d.level == AgreementLevel.LLM_ONLY
    }
    assert "formation_date" in llm_only_fields
    assert "registration_date" in llm_only_fields


def test_xval_types_of_business_disagreement_replaces_garbage_list():
    """When types_total disagrees, deterministic's garbage services list
    must be replaced entirely by the LLM's clean list — not merged."""
    rh_pdf = (FIXTURES / "firm_10997_rhsecurities.pdf").read_bytes()
    deterministic = parse_finra_pdf(rh_pdf)
    llm = _simulated_good_rh_llm()

    merged, xval = cross_validate_finra(deterministic, llm)

    tob_diff = next(d for d in xval.diffs if d.field_name == "types_total")
    assert tob_diff.level == AgreementLevel.DISAGREE
    assert merged.types_of_business.total == 1
    # Must be exactly the 1 LLM service, not the 36-entry garbage list
    assert len(merged.types_of_business.services) == 1
    assert "tax shelters" in merged.types_of_business.services[0]


def test_xval_agrees_on_identity_fields():
    rh_pdf = (FIXTURES / "firm_10997_rhsecurities.pdf").read_bytes()
    deterministic = parse_finra_pdf(rh_pdf)
    llm = _simulated_good_rh_llm()
    _, xval = cross_validate_finra(deterministic, llm)

    agreed = {d.field_name for d in xval.diffs if d.level == AgreementLevel.AGREE}
    assert {"crd_number", "sec_number", "firm_name"}.issubset(agreed)


# ---------------------------------------------------------------------------
# FOCUS cross-validator
# ---------------------------------------------------------------------------

def test_xval_focus_balance_sheet_identity():
    """When deterministic and LLM agree on the balance sheet,
    cross-validator should flag AGREE across all financial fields."""
    pdf = (FIXTURES / "xfocus_andpartners.pdf").read_bytes()
    deterministic = parse_focus_pdf(pdf)

    # LLM produces identical numbers
    llm = deterministic.model_copy(deep=True)

    merged, xval = cross_validate_focus(deterministic, llm)
    assert xval.disagrees == 0
    assert merged.financials.total_assets == Decimal("68899951")


def test_xval_focus_disagreement_on_net_capital():
    """Deterministic net capital was a known false-positive failure mode
    (matched '17 CFR' citations). If LLM says null and deterministic says
    a number, cross-validator should flag the disagreement."""
    pdf = (FIXTURES / "xfocus_andpartners.pdf").read_bytes()
    deterministic = parse_focus_pdf(pdf)

    llm = deterministic.model_copy(deep=True)
    llm.financials.net_capital = None  # LLM correctly returns null

    _, xval = cross_validate_focus(deterministic, llm)

    net_cap_diff = next(d for d in xval.diffs if d.field_name == "net_capital")
    # deterministic has a value, LLM has null → DETERMINISTIC_ONLY
    # This is the healthy case — either value may be right; keep deterministic
    # but surface it in the trace
    assert net_cap_diff.level == AgreementLevel.DETERMINISTIC_ONLY


def test_focus_confidence_detects_balance_sheet_violation():
    """If totals don't balance, confidence MUST drop."""
    report = FocusReport(
        sec_file_number="8-00000",
        firm_name="Test Firm LLC",
        period_ending=date(2025, 12, 31),
        contact=PrimaryContact(
            full_name="Jane Doe",
            title="CFO",
            email="jane@test.com",
            phone="555-000-0000",
        ),
        financials=FinancialStanding(
            total_assets=Decimal("100000000"),
            total_liabilities=Decimal("60000000"),
            members_equity=Decimal("30000000"),  # 60M + 30M = 90M, not 100M → 10M violation
        ),
    )
    conf = score_focus(report)
    assert conf.score < 0.8
    assert any("balance_sheet" in r for r in conf.reasons)
