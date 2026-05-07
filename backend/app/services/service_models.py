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
    # Stamped to 'finra' when ``website`` is populated from the BrokerCheck
    # detail payload. Stays None for records that fall through to the Apollo
    # organizations fallback (which restamps to 'apollo' if it finds a hit).
    website_source: str | None = None
    types_of_business: list[str] | None = None
    direct_owners: list[dict[str, str]] | None = None
    executive_officers: list[dict[str, str]] | None = None
    firm_operations_text: str | None = None
    # ── Form BD cover-page + Item 12B fields (added 2026-05-04) ──
    # Sourced from services/brokercheck_pdf.py FormBdDetail. Each is None
    # when the PDF parser couldn't recover the field.
    registration_date: date | None = None
    formation_date: date | None = None
    types_of_business_other: str | None = None


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
    # Carried through from FinraBrokerDealerRecord and updated by the Apollo
    # organizations fallback. 'finra' | 'apollo' | None.
    website_source: str | None = None
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
    # Inline base64 of the PDF bytes. Populated under the legacy
    # ``LLM_USE_FILES_API=false`` path so today's downstream consumers
    # (api/v1/endpoints/broker_dealers focus-report endpoint, focus-CEO
    # extraction, focus-reports financial multi-year extraction) keep
    # functioning byte-for-byte unchanged. Under ``LLM_USE_FILES_API=true``
    # the clearing-extraction path streams the bytes to disk and skips this
    # allocation; ``file_id`` carries the provider Files API reference
    # instead. ADR-0001 phase 2.
    bytes_base64: str
    # SEC accession number (e.g. ``0001234567-25-000001``). Used as the LRU
    # key alongside the provider name when ``LLM_USE_FILES_API=true``: a
    # re-file gets a new accession by construction, so re-files automatically
    # force a fresh upload — staleness collapses out of the design.
    accession_number: str | None = None
    # Provider-scoped Files API reference (e.g. ``files/abc123`` for Gemini,
    # ``file-abc123`` for OpenAI). Populated by the LLM client after a
    # successful upload under ``LLM_USE_FILES_API=true``. None on the legacy
    # path. The downloader never sets this — it is provider-side state.
    file_id: str | None = None


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
    # The LLM's verbatim ``evidence_excerpt`` — the FOCUS-report sentence(s)
    # that named the clearing partner. Populated on success; None on error /
    # missing-PDF paths. Read by services/classification.py to give the
    # classifier the audited FOCUS text alongside the (self-declared) FINRA
    # operations text. Has a default so synthetic test fixtures that omit it
    # keep working unchanged.
    clearing_statement_text: str | None = None


@dataclass(slots=True)
class ProviderDistributionRecord:
    provider: str
    count: int
    percentage: float
    is_competitor: bool


# ──────────────────────────────────────────────────────────────
# IAPD Investment Adviser ingestion (PR 2 of advisor-list rollout)
# ──────────────────────────────────────────────────────────────


@dataclass(slots=True)
class IapdAdvisorRecord:
    """One row parsed from the SEC IAPD ``ia{MMDDYY}.zip`` Compilation Report.

    The Compilation Report CSV has 448 columns; this record captures the
    subset the master-list page renders. Form ADV Item 5 columns are
    extracted as raw lists of activity codes ("5G(1)", "5D(1)(a)", ...);
    the merge service maps them to the canonical labels the FE filter
    dropdown uses. Numeric fields land as ``Decimal`` for safe Numeric(20,2)
    storage; empty CSV cells become None.
    """

    crd_number: str
    sec_file_number: str | None
    cik: str | None
    name: str
    legal_name: str | None
    city: str | None
    state: str | None
    status: str | None
    last_filing_date: date | None
    website: str | None
    # Form ADV Item 5.F.(2) — regulatory AUM and split. (a) discretionary,
    # (b) non-discretionary, (c) total = a + b. Stored as native float
    # because pydantic Numeric serialization is happier with floats than
    # Decimal in the upsert path.
    discretionary_aum: float | None
    non_discretionary_aum: float | None
    regulatory_aum: float | None
    total_clients: int | None
    # Item 5.G.(1)..(12) — yes/no checkboxes. We collect just the codes
    # that are checked ("yes" / "Y") into a list of canonical strings; the
    # FE renders human-readable labels via a static map.
    advisory_activities: list[str]
    # Item 5.D.(1)(a)..(m) — yes/no per client type.
    client_types: list[str]
    # Item 5.D.(2)(a)..(m) — count of clients per type. Empty when the
    # firm did not break out per-type counts.
    client_counts: dict[str, int]


@dataclass(slots=True)
class MergedInvestmentAdvisorRecord:
    """Post-13F-merge advisor record ready for ``upsert_many``.

    Same shape as ``IapdAdvisorRecord`` plus the two denormalized 13F
    flags. ``files_13f`` is True iff the firm's CIK appears in the
    EFTS 13F-HR result set inside the ``thirteen_f_lookback_days``
    window; ``latest_13f_filing_date`` carries the most recent filing
    date EFTS returned for that CIK.
    """

    crd_number: str
    sec_file_number: str | None
    cik: str | None
    name: str
    legal_name: str | None
    city: str | None
    state: str | None
    status: str
    last_filing_date: date | None
    website: str | None
    website_source: str | None
    regulatory_aum: float | None
    discretionary_aum: float | None
    non_discretionary_aum: float | None
    total_clients: int | None
    advisory_activities: list[str]
    client_types: list[str]
    client_counts: dict[str, int]
    files_13f: bool
    latest_13f_filing_date: date | None
    matched_source: str = "iapd"
