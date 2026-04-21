"""
Cross-validator: compares deterministic parser output to LLM output field-by-field.

Outcomes per field:
  AGREE           values match within tolerance → high-confidence accept
  DETERMINISTIC   only deterministic has a value → accept deterministic
  LLM             only LLM has a value → accept LLM
  DISAGREE        both have values but they differ → flag for escalation/review

At the record level:
  - If all critical fields AGREE or one-sided → status=ok, auto-accept
  - If any critical field DISAGREES → escalate to Gemini Pro
  - If post-escalation still disagrees → human review queue
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from ..schema.models import FirmProfile, FocusReport


class AgreementLevel(str, Enum):
    AGREE = "agree"
    DETERMINISTIC_ONLY = "deterministic_only"
    LLM_ONLY = "llm_only"
    DISAGREE = "disagree"
    BOTH_NULL = "both_null"


@dataclass
class FieldDiff:
    field_name: str
    deterministic_value: Any
    llm_value: Any
    level: AgreementLevel
    resolved_value: Any = None


@dataclass
class CrossValidationResult:
    diffs: list[FieldDiff] = field(default_factory=list)
    agrees: int = 0
    disagrees: int = 0
    one_sided: int = 0
    both_null: int = 0

    @property
    def has_disagreements(self) -> bool:
        return self.disagrees > 0

    @property
    def critical_disagreements(self) -> list[FieldDiff]:
        return [d for d in self.diffs if d.level == AgreementLevel.DISAGREE]


# ---------------------------------------------------------------------------
# Generic comparison helpers
# ---------------------------------------------------------------------------

def _norm_str(s: Any) -> Optional[str]:
    if s is None:
        return None
    return " ".join(str(s).strip().split()).lower()


def _money_close(a: Optional[Decimal], b: Optional[Decimal], tol_pct: float = 0.005) -> bool:
    if a is None or b is None:
        return a == b
    if a == b:
        return True
    if a == 0 or b == 0:
        return False
    return abs(float(a - b)) / max(abs(float(a)), abs(float(b))) < tol_pct


def _compare_strings(a: Any, b: Any) -> AgreementLevel:
    na, nb = _norm_str(a), _norm_str(b)
    if na is None and nb is None:
        return AgreementLevel.BOTH_NULL
    if na is None:
        return AgreementLevel.LLM_ONLY
    if nb is None:
        return AgreementLevel.DETERMINISTIC_ONLY
    if na == nb or na in nb or nb in na:
        return AgreementLevel.AGREE
    return AgreementLevel.DISAGREE


def _compare_dates(a: Any, b: Any) -> AgreementLevel:
    if a is None and b is None:
        return AgreementLevel.BOTH_NULL
    if a is None:
        return AgreementLevel.LLM_ONLY
    if b is None:
        return AgreementLevel.DETERMINISTIC_ONLY
    return AgreementLevel.AGREE if a == b else AgreementLevel.DISAGREE


def _compare_money(a: Optional[Decimal], b: Optional[Decimal]) -> AgreementLevel:
    if a is None and b is None:
        return AgreementLevel.BOTH_NULL
    if a is None:
        return AgreementLevel.LLM_ONLY
    if b is None:
        return AgreementLevel.DETERMINISTIC_ONLY
    return AgreementLevel.AGREE if _money_close(a, b) else AgreementLevel.DISAGREE


def _compare_int(a: Any, b: Any) -> AgreementLevel:
    if a is None and b is None:
        return AgreementLevel.BOTH_NULL
    if a is None or a == 0:
        return AgreementLevel.LLM_ONLY if b else AgreementLevel.BOTH_NULL
    if b is None or b == 0:
        return AgreementLevel.DETERMINISTIC_ONLY
    return AgreementLevel.AGREE if int(a) == int(b) else AgreementLevel.DISAGREE


# ---------------------------------------------------------------------------
# FINRA cross-validation
# ---------------------------------------------------------------------------

def cross_validate_finra(
    deterministic: FirmProfile, llm: FirmProfile
) -> tuple[FirmProfile, CrossValidationResult]:
    """Merge two FirmProfile extractions, producing a consolidated profile
    plus a diff report. LLM wins on disagreements; flag for review."""
    result = CrossValidationResult()
    merged = deterministic.model_copy(deep=True)

    pairs = [
        ("crd_number", deterministic.crd_number, llm.crd_number, _compare_strings),
        ("sec_number", deterministic.sec_number, llm.sec_number, _compare_strings),
        ("firm_name", deterministic.firm_name, llm.firm_name, _compare_strings),
        ("formation_date", deterministic.history.formation_date, llm.history.formation_date, _compare_dates),
        ("registration_date", deterministic.history.registration_date, llm.history.registration_date, _compare_dates),
        ("termination_date", deterministic.history.termination_date, llm.history.termination_date, _compare_dates),
        ("types_total", deterministic.types_of_business.total, llm.types_of_business.total, _compare_int),
    ]

    for name, d, l, cmp in pairs:
        level = cmp(d, l)
        diff = FieldDiff(name, d, l, level, resolved_value=_resolve(d, l, level))
        result.diffs.append(diff)
        _tally(result, level)

    # Officer count — use deterministic if within ±1 of LLM; else LLM
    d_count = len(deterministic.officers)
    l_count = len(llm.officers)
    if abs(d_count - l_count) > 1:
        result.diffs.append(
            FieldDiff("officer_count", d_count, l_count, AgreementLevel.DISAGREE)
        )
        result.disagrees += 1
        merged.officers = llm.officers  # LLM tends to be more complete

    # Apply resolved scalar values back into merged profile
    for diff in result.diffs:
        if diff.level == AgreementLevel.LLM_ONLY or diff.level == AgreementLevel.DISAGREE:
            _apply(merged, diff.field_name, diff.resolved_value)

    # If types_total disagreed, LLM's services list is more trustworthy
    # too — don't persist the deterministic garbage list alongside the LLM count
    tob_diff = next((d for d in result.diffs if d.field_name == "types_total"), None)
    if tob_diff and tob_diff.level == AgreementLevel.DISAGREE:
        merged.types_of_business.services = llm.types_of_business.services
        merged.types_of_business.other = llm.types_of_business.other
    # Services list — simple fill if LLM has services and deterministic doesn't
    elif llm.types_of_business.services and not deterministic.types_of_business.services:
        merged.types_of_business.services = llm.types_of_business.services
        merged.types_of_business.total = llm.types_of_business.total

    return merged, result


def _resolve(d: Any, l: Any, level: AgreementLevel) -> Any:
    if level == AgreementLevel.AGREE or level == AgreementLevel.DETERMINISTIC_ONLY:
        return d
    if level == AgreementLevel.LLM_ONLY or level == AgreementLevel.DISAGREE:
        return l
    return None


def _tally(result: CrossValidationResult, level: AgreementLevel) -> None:
    if level == AgreementLevel.AGREE:
        result.agrees += 1
    elif level == AgreementLevel.DISAGREE:
        result.disagrees += 1
    elif level == AgreementLevel.BOTH_NULL:
        result.both_null += 1
    else:
        result.one_sided += 1


def _apply(profile: FirmProfile, field_name: str, value: Any) -> None:
    # Map flat diff names back into the nested FirmProfile
    if field_name == "crd_number":
        profile.crd_number = value
    elif field_name == "sec_number":
        profile.sec_number = value
    elif field_name == "firm_name":
        profile.firm_name = value
    elif field_name == "formation_date":
        profile.history.formation_date = value
    elif field_name == "registration_date":
        profile.history.registration_date = value
    elif field_name == "termination_date":
        profile.history.termination_date = value
    elif field_name == "types_total":
        profile.types_of_business.total = value or 0


# ---------------------------------------------------------------------------
# FOCUS cross-validation
# ---------------------------------------------------------------------------

def cross_validate_focus(
    deterministic: FocusReport, llm: FocusReport
) -> tuple[FocusReport, CrossValidationResult]:
    result = CrossValidationResult()
    merged = deterministic.model_copy(deep=True)

    pairs = [
        ("sec_file_number", deterministic.sec_file_number, llm.sec_file_number, _compare_strings),
        ("firm_name", deterministic.firm_name, llm.firm_name, _compare_strings),
        ("period_ending", deterministic.period_ending, llm.period_ending, _compare_dates),
        ("contact_name", deterministic.contact.full_name, llm.contact.full_name, _compare_strings),
        ("contact_title", deterministic.contact.title, llm.contact.title, _compare_strings),
        ("contact_email", deterministic.contact.email, llm.contact.email, _compare_strings),
        ("contact_phone", deterministic.contact.phone, llm.contact.phone, _compare_strings),
        ("total_assets", deterministic.financials.total_assets, llm.financials.total_assets, _compare_money),
        ("total_liabilities", deterministic.financials.total_liabilities, llm.financials.total_liabilities, _compare_money),
        ("members_equity", deterministic.financials.members_equity, llm.financials.members_equity, _compare_money),
        ("net_capital", deterministic.financials.net_capital, llm.financials.net_capital, _compare_money),
        ("auditor_name", deterministic.auditor_name, llm.auditor_name, _compare_strings),
        ("auditor_pcaob_id", deterministic.auditor_pcaob_id, llm.auditor_pcaob_id, _compare_strings),
    ]

    for name, d, l, cmp in pairs:
        level = cmp(d, l)
        diff = FieldDiff(name, d, l, level, resolved_value=_resolve(d, l, level))
        result.diffs.append(diff)
        _tally(result, level)

    # Apply resolved values back
    for diff in result.diffs:
        if diff.level in (AgreementLevel.LLM_ONLY, AgreementLevel.DISAGREE):
            _apply_focus(merged, diff.field_name, diff.resolved_value)

    return merged, result


def _apply_focus(report: FocusReport, field_name: str, value: Any) -> None:
    if field_name == "sec_file_number":
        report.sec_file_number = value
    elif field_name == "firm_name":
        report.firm_name = value
    elif field_name == "period_ending":
        report.period_ending = value
    elif field_name == "contact_name":
        report.contact.full_name = value
    elif field_name == "contact_title":
        report.contact.title = value
    elif field_name == "contact_email":
        report.contact.email = value
    elif field_name == "contact_phone":
        report.contact.phone = value
    elif field_name == "total_assets":
        report.financials.total_assets = value
    elif field_name == "total_liabilities":
        report.financials.total_liabilities = value
    elif field_name == "members_equity":
        report.financials.members_equity = value
    elif field_name == "net_capital":
        report.financials.net_capital = value
    elif field_name == "auditor_name":
        report.auditor_name = value
    elif field_name == "auditor_pcaob_id":
        report.auditor_pcaob_id = value
