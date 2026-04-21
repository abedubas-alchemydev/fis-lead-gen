"""
Pydantic models that mirror the client requirements document exactly.
Every field carries a `source` hint so downstream consumers know which PDF produced it.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ClearingType(str, Enum):
    SELF_CLEARING = "self_clearing"
    FULLY_DISCLOSED = "fully_disclosed"
    UNKNOWN = "unknown"  # triggers raw-text override per client spec


# ---------------------------------------------------------------------------
# Domain 1 — FINRA BrokerCheck Firm Profile
# ---------------------------------------------------------------------------

class Officer(BaseModel):
    """Entry in the 'Direct Owners and Executive Officers' section."""
    name: str
    position: Optional[str] = None
    is_control_affiliate: Optional[bool] = None
    position_start: Optional[str] = None           # FINRA stores as MM/YYYY strings
    ownership_code: Optional[str] = None           # e.g. '75% or more', '25% but less than 50%'
    pr: Optional[bool] = None                      # public reporting flag
    raw: Optional[str] = None                      # raw paragraph for audit


class IntroducingArrangement(BaseModel):
    """One row from the 'Introducing Arrangements' subsection."""
    business_name: Optional[str] = None            # e.g. 'Pershing LLC'
    effective_date: Optional[str] = None
    description: Optional[str] = None
    statement: Optional[str] = None                # full sentence block


IndustryArrangementKind = Literal["books_records", "accounts_funds", "customer_accounts"]


class IndustryArrangement(BaseModel):
    """One of three yes/no Industry Arrangements statements from the FINRA
    BrokerCheck 'Firm Operations → Industry Arrangements' subsection.

    The three kinds combined determine whether the firm is truly self-clearing
    versus using a third party for books/records, firm-level asset custody, or
    customer-level asset custody.
    """
    kind: IndustryArrangementKind
    has_arrangement: bool
    partner_name: Optional[str] = None
    partner_crd: Optional[str] = None
    partner_address: Optional[str] = None
    effective_date: Optional[str] = None           # FINRA stores as MM/DD/YYYY strings
    description: Optional[str] = None
    statement: Optional[str] = None                # full raw sentence block


class TypesOfBusiness(BaseModel):
    """Client spec requires: total number + list + 'other' freeform."""
    total: int = 0
    services: list[str] = Field(default_factory=list)
    other: Optional[str] = None


class FirmHistory(BaseModel):
    registration_date: Optional[date] = None       # first FINRA registration
    formation_date: Optional[date] = None          # state of formation / incorporation
    termination_date: Optional[date] = None        # if applicable


class FirmOperations(BaseModel):
    clearing_statement: Optional[str] = None
    introducing_arrangements: list[IntroducingArrangement] = Field(default_factory=list)
    industry_arrangements: list[IndustryArrangement] = Field(default_factory=list)
    clearing_type: ClearingType = ClearingType.UNKNOWN
    clearing_raw_text: Optional[str] = None        # always preserved for client override


class FirmProfile(BaseModel):
    """Domain 1 — pulled from FINRA BrokerCheck PDF."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Literal["finra_brokercheck"] = "finra_brokercheck"
    crd_number: Optional[str] = None
    sec_number: Optional[str] = None
    firm_name: Optional[str] = None
    legal_name: Optional[str] = None
    is_registered: Optional[bool] = None

    officers: list[Officer] = Field(default_factory=list)
    types_of_business: TypesOfBusiness = Field(default_factory=TypesOfBusiness)
    operations: FirmOperations = Field(default_factory=FirmOperations)
    history: FirmHistory = Field(default_factory=FirmHistory)

    raw_pdf_hash: Optional[str] = None
    parsed_at: datetime = Field(default_factory=datetime.utcnow)
    parse_warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Domain 2 — SEC X-17A-5 Financial / Contact
# ---------------------------------------------------------------------------

class PrimaryContact(BaseModel):
    full_name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class FinancialStanding(BaseModel):
    period_end: Optional[date] = None
    total_assets: Optional[Decimal] = None
    total_liabilities: Optional[Decimal] = None
    members_equity: Optional[Decimal] = None       # LLC
    stockholders_equity: Optional[Decimal] = None  # Corp
    net_capital: Optional[Decimal] = None          # from computation — may be absent in Part III


class FocusReport(BaseModel):
    """Domain 2 — pulled from SEC X-17A-5 PDF."""
    source: Literal["sec_x17a5"] = "sec_x17a5"
    sec_file_number: Optional[str] = None
    firm_name: Optional[str] = None
    period_beginning: Optional[date] = None
    period_ending: Optional[date] = None

    contact: PrimaryContact = Field(default_factory=PrimaryContact)
    financials: FinancialStanding = Field(default_factory=FinancialStanding)

    auditor_name: Optional[str] = None
    auditor_pcaob_id: Optional[str] = None

    raw_pdf_hash: Optional[str] = None
    parsed_at: datetime = Field(default_factory=datetime.utcnow)
    parse_warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Combined record + derivation
# ---------------------------------------------------------------------------

class YoYGrowth(BaseModel):
    current_value: Optional[Decimal] = None
    prior_value: Optional[Decimal] = None
    growth_pct: Optional[float] = None
    insufficient_data: bool = False


class FirmRecord(BaseModel):
    """The full row we persist per firm."""
    firm_id: str                                   # your internal ID from the 3K input DB
    queried_name: str

    finra: Optional[FirmProfile] = None
    focus_current: Optional[FocusReport] = None
    focus_prior: Optional[FocusReport] = None

    net_capital_yoy: YoYGrowth = Field(default_factory=YoYGrowth)
    total_assets_yoy: YoYGrowth = Field(default_factory=YoYGrowth)

    status: Literal["ok", "partial", "failed"] = "partial"
    failure_reason: Optional[str] = None
