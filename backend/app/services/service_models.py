from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(slots=True)
class EdgarBrokerDealerRecord:
    cik: str
    name: str
    sic: str
    state: str | None
    city: str | None
    sec_file_number: str | None
    registration_date: date | None
    last_filing_date: date | None
    filings_index_url: str
    sic_description: str | None = None


@dataclass(slots=True)
class FinraBrokerDealerRecord:
    crd_number: str
    name: str
    sec_file_number: str | None
    registration_status: str
    branch_count: int | None
    address_city: str | None
    address_state: str | None
    business_type: str | None
    # ── Tri-Stream fields (Revision 1 - Stream A) ──
    website: str | None = None
    types_of_business: list[str] | None = None
    direct_owners: list[dict[str, str]] | None = None
    executive_officers: list[dict[str, str]] | None = None
    firm_operations_text: str | None = None


@dataclass(slots=True)
class MergedBrokerDealerRecord:
    cik: str | None
    crd_number: str | None
    sec_file_number: str | None
    name: str
    city: str | None
    state: str | None
    status: str
    branch_count: int | None
    business_type: str | None
    registration_date: date | None
    matched_source: str  # "both" | "finra_only"
    last_filing_date: date | None
    filings_index_url: str | None
    # ── Tri-Stream fields (Revision 1) ──
    website: str | None = None
    types_of_business: list[str] | None = None
    direct_owners: list[dict[str, str]] | None = None
    executive_officers: list[dict[str, str]] | None = None
    firm_operations_text: str | None = None


# ──────────────────────────────────────────────────────────────
# Ingestion QA Report — produced by BrokerDealerMergeService
# ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class BadSourceRow:
    """A single row that was rejected during merge."""
    source: str            # "edgar" or "finra"
    identifier: str        # CIK or CRD
    name: str
    reason: str            # human-readable rejection reason

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "identifier": self.identifier,
            "name": self.name,
            "reason": self.reason,
        }


@dataclass(slots=True)
class MergeQAReport:
    """Full quality-assurance report produced after every merge run."""
    edgar_input_count: int = 0
    finra_input_count: int = 0
    matched_both_count: int = 0
    finra_only_count: int = 0
    edgar_unresolved_count: int = 0
    duplicate_suppressed_count: int = 0
    inactive_suppressed_count: int = 0
    bad_sec_number_count: int = 0
    output_count: int = 0
    bad_source_rows: list[BadSourceRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "edgar_input_count": self.edgar_input_count,
            "finra_input_count": self.finra_input_count,
            "matched_both_count": self.matched_both_count,
            "finra_only_count": self.finra_only_count,
            "edgar_unresolved_count": self.edgar_unresolved_count,
            "duplicate_suppressed_count": self.duplicate_suppressed_count,
            "inactive_suppressed_count": self.inactive_suppressed_count,
            "bad_sec_number_count": self.bad_sec_number_count,
            "output_count": self.output_count,
            "bad_source_row_count": len(self.bad_source_rows),
        }

    def bad_source_rows_as_dicts(self) -> list[dict[str, str]]:
        return [row.to_dict() for row in self.bad_source_rows]

    def summary_lines(self) -> list[str]:
        """Return human-readable summary lines for logging."""
        return [
            "═══════════════════════════════════════════════════",
            "          INGESTION QA REPORT",
            "═══════════════════════════════════════════════════",
            f"  EDGAR input rows:            {self.edgar_input_count:>6,}",
            f"  FINRA input rows:            {self.finra_input_count:>6,}",
            f"  ─────────────────────────────────────────────",
            f"  Matched (both sources):      {self.matched_both_count:>6,}",
            f"  FINRA-only (justified):      {self.finra_only_count:>6,}",
            f"  EDGAR unresolved (dropped):  {self.edgar_unresolved_count:>6,}",
            f"  ─────────────────────────────────────────────",
            f"  Duplicates suppressed:       {self.duplicate_suppressed_count:>6,}",
            f"  Inactive firms suppressed:   {self.inactive_suppressed_count:>6,}",
            f"  Bad SEC file numbers:        {self.bad_sec_number_count:>6,}",
            f"  ─────────────────────────────────────────────",
            f"  FINAL OUTPUT ROWS:           {self.output_count:>6,}",
            "═══════════════════════════════════════════════════",
        ]

    def bad_source_summary(self, max_rows: int = 25) -> list[str]:
        """Return the first N bad-source rows as formatted log lines."""
        if not self.bad_source_rows:
            return ["  (no bad-source rows)"]
        lines: list[str] = []
        for row in self.bad_source_rows[:max_rows]:
            lines.append(f"  [{row.source}] {row.identifier} - {row.name!r}: {row.reason}")
        if len(self.bad_source_rows) > max_rows:
            lines.append(f"  ... and {len(self.bad_source_rows) - max_rows} more.")
        return lines


# ──────────────────────────────────────────────────────────────
# Financial / pipeline models (unchanged)
# ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class FinancialMetricRecord:
    bd_id: int
    report_date: date
    net_capital: float
    excess_net_capital: float | None
    total_assets: float | None
    required_min_capital: float | None
    source_filing_url: str | None
    # Defaults to "parsed" so legacy call sites that construct this dataclass
    # (e.g. CSV loader, tests) without thinking about the review queue still
    # land in the successful-extraction bucket. The financial pipeline's
    # write path in focus_reports.py overrides this explicitly via
    # classify_financial_extraction_status.
    extraction_status: str = "parsed"


@dataclass(slots=True)
class FilingAlertRecord:
    bd_id: int
    dedupe_key: str
    form_type: str
    priority: str
    filed_at: datetime
    summary: str
    source_filing_url: str | None


@dataclass(slots=True)
class DownloadedPdfRecord:
    bd_id: int
    filing_year: int
    report_date: date | None
    source_filing_url: str | None
    source_pdf_url: str | None
    local_document_path: str
    bytes_base64: str


@dataclass(slots=True)
class ClearingExtractionResult:
    bd_id: int
    filing_year: int
    report_date: date | None
    source_filing_url: str | None
    source_pdf_url: str | None
    local_document_path: str | None
    clearing_partner: str | None
    clearing_type: str | None
    agreement_date: date | None
    extraction_confidence: float | None
    extraction_status: str
    extraction_notes: str | None
    extracted_at: datetime | None


@dataclass(slots=True)
class ProviderDistributionRecord:
    provider: str
    count: int
    percentage: float
    is_competitor: bool
