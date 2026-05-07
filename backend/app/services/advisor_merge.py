"""Merge IAPD records with the 13F filer CIK set.

Sibling of ``services/data_merge.py`` for the advisor pipeline. The
broker-dealer merge has to reconcile two authoritative sources (FINRA
+ EDGAR) with overlapping schemas; the advisor merge is simpler — IAPD
is the authority and EDGAR EFTS contributes a single boolean flag
(``files_13f``) plus its companion date.

The QA report shape mirrors ``MergeQAReport`` — same per-source row
counts and dropped-row reasons — so the initial-load script can format
its end-of-run summary the same way for both pipelines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from app.services.service_models import (
    IapdAdvisorRecord,
    MergedInvestmentAdvisorRecord,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AdvisorMergeReport:
    """End-of-merge stats for the initial-load run summary."""

    iapd_input_count: int = 0
    thirteen_f_filer_count: int = 0
    merged_count: int = 0
    files_13f_count: int = 0
    dropped_no_crd: int = 0
    dropped_no_name: int = 0
    cik_match_count: int = 0
    dropped_examples: list[tuple[str, str]] = field(default_factory=list)


class AdvisorMergeService:
    """Joins IAPD records with the 13F CIK set."""

    def merge(
        self,
        iapd_records: list[IapdAdvisorRecord],
        thirteen_f_ciks: dict[str, date],
    ) -> tuple[list[MergedInvestmentAdvisorRecord], AdvisorMergeReport]:
        """Combine the two streams into upsert-ready rows + a QA report.

        ``thirteen_f_ciks`` maps a CIK (unpadded, no leading zeros — see
        ``thirteen_f_filter._normalize_cik``) to the most recent
        13F-HR filing date observed in the EFTS lookback window.
        """

        report = AdvisorMergeReport(
            iapd_input_count=len(iapd_records),
            thirteen_f_filer_count=len(thirteen_f_ciks),
        )

        merged: list[MergedInvestmentAdvisorRecord] = []
        for record in iapd_records:
            if not record.crd_number:
                report.dropped_no_crd += 1
                if len(report.dropped_examples) < 5:
                    report.dropped_examples.append(("missing CRD", record.name or "<unnamed>"))
                continue
            if not record.name:
                report.dropped_no_name += 1
                if len(report.dropped_examples) < 5:
                    report.dropped_examples.append(("missing name", record.crd_number))
                continue

            # Normalize the IAPD CIK the same way EFTS-derived CIKs are
            # normalized in ``thirteen_f_filter._normalize_cik`` so the
            # join matches when the IAPD CSV holds a stripped integer
            # ("1521951") and EFTS returned the zero-padded form.
            normalized_cik = _strip_leading_zeros(record.cik) if record.cik else None
            latest_13f = thirteen_f_ciks.get(normalized_cik) if normalized_cik else None
            files_13f = latest_13f is not None
            if files_13f:
                report.files_13f_count += 1
            if normalized_cik and normalized_cik in thirteen_f_ciks:
                report.cik_match_count += 1

            merged.append(
                MergedInvestmentAdvisorRecord(
                    crd_number=record.crd_number,
                    sec_file_number=record.sec_file_number,
                    cik=normalized_cik,
                    name=record.name,
                    legal_name=record.legal_name,
                    city=record.city,
                    state=record.state,
                    # IAPD's "SEC Current Status" is e.g. "Approved" / "Pending"
                    # / "Withdrawn"; default to "active" when absent rather than
                    # the BD pipeline's "pending" because everything in the
                    # SEC-registered IAPD feed has at least one approved Form
                    # ADV on file.
                    status=(record.status or "active").lower(),
                    last_filing_date=record.last_filing_date,
                    website=record.website,
                    website_source="iapd" if record.website else None,
                    regulatory_aum=record.regulatory_aum,
                    discretionary_aum=record.discretionary_aum,
                    non_discretionary_aum=record.non_discretionary_aum,
                    total_clients=record.total_clients,
                    advisory_activities=record.advisory_activities,
                    client_types=record.client_types,
                    client_counts=record.client_counts,
                    files_13f=files_13f,
                    latest_13f_filing_date=latest_13f,
                )
            )

        report.merged_count = len(merged)
        logger.info(
            "Advisor merge: %d IAPD in → %d merged out (%d 13F filers, %d dropped).",
            report.iapd_input_count,
            report.merged_count,
            report.files_13f_count,
            report.dropped_no_crd + report.dropped_no_name,
        )
        return merged, report


def _strip_leading_zeros(value: str) -> str:
    stripped = value.lstrip("0")
    return stripped or "0"
