"""
Confidence scoring for deterministic parser output.

Detects the cases where the regex parser almost certainly mis-read the PDF
and should defer to the LLM. Runs in constant time; no LLM call required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..schema.models import FirmProfile, FocusReport


@dataclass
class ConfidenceReport:
    score: float                              # 0.0 - 1.0
    needs_llm_fallback: bool
    reasons: list[str] = field(default_factory=list)

    def add(self, reason: str) -> None:
        self.reasons.append(reason)


# ---------------------------------------------------------------------------
# FINRA
# ---------------------------------------------------------------------------

def score_finra(
    profile: FirmProfile, raw_text_sample: Optional[str] = None
) -> ConfidenceReport:
    """Estimate how confident we can be in a deterministic FINRA parse."""
    rpt = ConfidenceReport(score=1.0, needs_llm_fallback=False)

    # --- Critical identity fields must be present
    if not profile.crd_number:
        rpt.score -= 0.4
        rpt.add("missing_crd")
    if not profile.firm_name:
        rpt.score -= 0.3
        rpt.add("missing_firm_name")

    # --- Parse warnings are strong fallback signals
    if profile.parse_warnings:
        rpt.score -= 0.25
        for w in profile.parse_warnings:
            rpt.add(f"warn:{w}")

    # --- Types of business consistency
    tob = profile.types_of_business
    if tob.total and tob.total != len(tob.services):
        # Mismatch can mean the parser mis-counted or leaked preamble
        rpt.score -= 0.2
        rpt.add(f"tob_count_mismatch:{tob.total}!={len(tob.services)}")

    # --- Suspicious service list entries (shouldn't start with "This" etc.)
    for s in tob.services:
        if re.match(r"^(This |expects |Firm )", s):
            rpt.score -= 0.15
            rpt.add(f"tob_preamble_leak:{s[:40]}")
            break

    # --- Space-collapse detection on raw text (legacy PDF encoding issue)
    if raw_text_sample:
        non_ws = len(re.sub(r"\s", "", raw_text_sample))
        spaces = raw_text_sample.count(" ")
        if non_ws > 500 and (spaces / non_ws) < 0.08:
            # Very low space ratio = pdfplumber collapsed word boundaries
            rpt.score -= 0.5
            rpt.add("space_collapse_detected")

    # --- Clearing section should have SOMETHING
    if (
        not profile.operations.clearing_statement
        and not profile.operations.clearing_raw_text
    ):
        rpt.score -= 0.1
        rpt.add("clearing_section_empty")

    rpt.score = max(0.0, min(1.0, rpt.score))
    rpt.needs_llm_fallback = rpt.score < 0.75
    return rpt


# ---------------------------------------------------------------------------
# FOCUS
# ---------------------------------------------------------------------------

def score_focus(report: FocusReport) -> ConfidenceReport:
    rpt = ConfidenceReport(score=1.0, needs_llm_fallback=False)

    # --- Identity
    if not report.sec_file_number:
        rpt.score -= 0.25
        rpt.add("missing_sec_file")
    if not report.firm_name:
        rpt.score -= 0.2
        rpt.add("missing_firm_name")
    if not report.period_ending:
        rpt.score -= 0.2
        rpt.add("missing_period_ending")

    # --- Contact (client-required)
    c = report.contact
    if not c.full_name:
        rpt.score -= 0.1
        rpt.add("missing_contact_name")
    if not c.email:
        rpt.score -= 0.1
        rpt.add("missing_contact_email")
    if not c.phone:
        rpt.score -= 0.1
        rpt.add("missing_contact_phone")
    if not c.title:
        rpt.score -= 0.1
        rpt.add("missing_contact_title")

    # --- Financials — balance-sheet identity must hold
    f = report.financials
    if f.total_assets and f.total_liabilities and (f.members_equity or f.stockholders_equity):
        equity = f.members_equity or f.stockholders_equity or Decimal(0)
        expected = f.total_liabilities + equity
        diff = abs(f.total_assets - expected)
        tolerance = max(Decimal("100"), f.total_assets * Decimal("0.001"))
        if diff > tolerance:
            rpt.score -= 0.3
            rpt.add(f"balance_sheet_identity_fails:{diff}")

    # --- At least one of members/stockholders equity should be present
    if f.total_assets and not (f.members_equity or f.stockholders_equity):
        rpt.score -= 0.2
        rpt.add("missing_equity")

    if not f.total_assets:
        rpt.score -= 0.3
        rpt.add("missing_total_assets")

    if report.parse_warnings:
        rpt.score -= 0.1
        for w in report.parse_warnings:
            rpt.add(f"warn:{w}")

    rpt.score = max(0.0, min(1.0, rpt.score))
    rpt.needs_llm_fallback = rpt.score < 0.75
    return rpt
