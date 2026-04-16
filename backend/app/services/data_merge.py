from __future__ import annotations

import logging

from difflib import SequenceMatcher

from app.services.normalization import normalize_entity_name, normalize_sec_file_number
from app.services.service_models import (
    BadSourceRow,
    EdgarBrokerDealerRecord,
    FinraBrokerDealerRecord,
    MergeQAReport,
    MergedBrokerDealerRecord,
)


logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Configuration constants
# ──────────────────────────────────────────────────────────────
# Threshold for fuzzy name matching when SEC file numbers don't match.
_FUZZY_MATCH_THRESHOLD = 0.88

# Only firms with these statuses survive into the output.
_ACTIVE_STATUSES = frozenset({"active"})


class BrokerDealerMergeService:
    """Merges EDGAR and FINRA datasets with full QA reporting.

    Source-of-truth rules
    ─────────────────────
    1. FINRA is the **primary** source (it is the broker-dealer registrar).
    2. Every output row is classified as either:
       • ``"both"``       — matched across EDGAR and FINRA via SEC file number
                            or high-confidence fuzzy name match.
       • ``"finra_only"`` — the firm exists in FINRA BrokerCheck but has no
                            corresponding EDGAR entity (justified: some firms
                            file exclusively through FINRA and have no EDGAR
                            submissions with SIC 6211).
    3. EDGAR-only rows (no FINRA match) are **dropped** because:
       • An SIC-6211 EDGAR filer with no FINRA presence may be a holding company
         or a non-BD entity that happens to share the SIC code.
       • The system targets *active* broker-dealers; FINRA registration is the
         authoritative signal.
    4. Duplicates are suppressed by SEC file number. If two FINRA rows share the
       same normalized file number, only the first is kept.
    5. Inactive firms (status ≠ "Active") are excluded.

    Field precedence
    ────────────────
    • ``name``  → prefer FINRA (official registrar name)
    • ``city``, ``state`` → prefer FINRA; fall back to EDGAR
    • ``cik``, ``registration_date``, ``last_filing_date`` → EDGAR only
    • ``crd_number``, ``branch_count``, ``business_type`` → FINRA only
    """

    def merge(
        self,
        edgar_records: list[EdgarBrokerDealerRecord],
        finra_records: list[FinraBrokerDealerRecord],
    ) -> tuple[list[MergedBrokerDealerRecord], MergeQAReport]:
        """Merge EDGAR and FINRA datasets.

        Returns a tuple of ``(merged_records, qa_report)``.
        """
        report = MergeQAReport(
            edgar_input_count=len(edgar_records),
            finra_input_count=len(finra_records),
        )

        # ── Step 1: Index EDGAR records by normalized SEC file number ──
        edgar_by_sec: dict[str, EdgarBrokerDealerRecord] = {}
        edgar_by_name: dict[str, EdgarBrokerDealerRecord] = {}
        for record in edgar_records:
            normalized_sec = normalize_sec_file_number(record.sec_file_number)
            if normalized_sec is None:
                report.bad_sec_number_count += 1
                report.bad_source_rows.append(BadSourceRow(
                    source="edgar",
                    identifier=record.cik,
                    name=record.name,
                    reason=f"SEC file number could not be normalized: {record.sec_file_number!r}",
                ))
                continue
            if normalized_sec in edgar_by_sec:
                # Duplicate CIK in EDGAR — keep the first occurrence.
                report.duplicate_suppressed_count += 1
                report.bad_source_rows.append(BadSourceRow(
                    source="edgar",
                    identifier=record.cik,
                    name=record.name,
                    reason=f"Duplicate SEC file number {normalized_sec} (first: CIK {edgar_by_sec[normalized_sec].cik})",
                ))
                continue
            edgar_by_sec[normalized_sec] = record
            # Also index by normalized name for fuzzy fallback.
            normalized_name = normalize_entity_name(record.name)
            if normalized_name and normalized_name not in edgar_by_name:
                edgar_by_name[normalized_name] = record

        # ── Step 1b: Build blocking index for fast fuzzy matching ──
        name_block_index = self._build_name_block_index(edgar_by_name)

        # ── Step 2: Walk FINRA records and try to match to EDGAR ──
        merged: list[MergedBrokerDealerRecord] = []
        seen_sec_numbers: set[str] = set()
        matched_edgar_secs: set[str] = set()

        for finra_record in finra_records:
            # ── Filter: inactive ──
            if finra_record.registration_status.lower() not in _ACTIVE_STATUSES:
                report.inactive_suppressed_count += 1
                continue

            # ── Filter: bad SEC file number ──
            normalized_sec = normalize_sec_file_number(finra_record.sec_file_number)
            if normalized_sec is None:
                report.bad_sec_number_count += 1
                report.bad_source_rows.append(BadSourceRow(
                    source="finra",
                    identifier=finra_record.crd_number,
                    name=finra_record.name,
                    reason=f"SEC file number could not be normalized: {finra_record.sec_file_number!r}",
                ))
                continue

            # ── Filter: duplicate SEC file number in FINRA set ──
            if normalized_sec in seen_sec_numbers:
                report.duplicate_suppressed_count += 1
                report.bad_source_rows.append(BadSourceRow(
                    source="finra",
                    identifier=finra_record.crd_number,
                    name=finra_record.name,
                    reason=f"Duplicate SEC file number {normalized_sec} (already present in output)",
                ))
                continue
            seen_sec_numbers.add(normalized_sec)

            # ── Try match: SEC file number ──
            edgar_match = edgar_by_sec.get(normalized_sec)

            # ── Try match: fuzzy name (only if SEC didn't match) ──
            if edgar_match is None:
                edgar_match = self._find_name_match(finra_record, edgar_by_name, name_block_index)

            # ── Build output row ──
            if edgar_match is not None:
                matched_edgar_sec = normalize_sec_file_number(edgar_match.sec_file_number)
                if matched_edgar_sec:
                    matched_edgar_secs.add(matched_edgar_sec)

                merged.append(MergedBrokerDealerRecord(
                    cik=edgar_match.cik,
                    crd_number=finra_record.crd_number,
                    sec_file_number=normalized_sec,
                    name=finra_record.name,  # FINRA name is authoritative
                    city=finra_record.address_city or edgar_match.city,
                    state=finra_record.address_state or edgar_match.state,
                    status=finra_record.registration_status,
                    branch_count=finra_record.branch_count,
                    business_type=finra_record.business_type,
                    registration_date=edgar_match.registration_date,
                    matched_source="both",
                    last_filing_date=edgar_match.last_filing_date,
                    filings_index_url=edgar_match.filings_index_url,
                    website=finra_record.website,
                    types_of_business=finra_record.types_of_business,
                    direct_owners=finra_record.direct_owners,
                    executive_officers=finra_record.executive_officers,
                    firm_operations_text=finra_record.firm_operations_text,
                ))
                report.matched_both_count += 1
            else:
                # Justified finra_only: no EDGAR entity for this firm.
                merged.append(MergedBrokerDealerRecord(
                    cik=None,
                    crd_number=finra_record.crd_number,
                    sec_file_number=normalized_sec,
                    name=finra_record.name,
                    city=finra_record.address_city,
                    state=finra_record.address_state,
                    status=finra_record.registration_status,
                    branch_count=finra_record.branch_count,
                    business_type=finra_record.business_type,
                    registration_date=None,
                    matched_source="finra_only",
                    last_filing_date=None,
                    filings_index_url=None,
                    website=finra_record.website,
                    types_of_business=finra_record.types_of_business,
                    direct_owners=finra_record.direct_owners,
                    executive_officers=finra_record.executive_officers,
                    firm_operations_text=finra_record.firm_operations_text,
                ))
                report.finra_only_count += 1

        # ── Step 3: Count unresolved EDGAR rows (dropped, not emitted) ──
        for sec_number, edgar_record in edgar_by_sec.items():
            if sec_number not in matched_edgar_secs:
                report.edgar_unresolved_count += 1
                report.bad_source_rows.append(BadSourceRow(
                    source="edgar",
                    identifier=edgar_record.cik,
                    name=edgar_record.name,
                    reason="No matching FINRA record found — dropped (EDGAR-only not emitted)",
                ))

        report.output_count = len(merged)
        return merged, report

    def _build_name_block_index(
        self,
        edgar_by_name: dict[str, EdgarBrokerDealerRecord],
    ) -> dict[str, list[tuple[str, EdgarBrokerDealerRecord]]]:
        """Build a blocking index keyed by the first token of the normalized name.

        This reduces the fuzzy-matching search space from O(N*M) to approximately
        O(N * avg_block_size) — typically a 20-50x speedup.
        """
        blocks: dict[str, list[tuple[str, EdgarBrokerDealerRecord]]] = {}
        for edgar_name, edgar_record in edgar_by_name.items():
            tokens = edgar_name.split()
            if not tokens:
                continue
            first_token = tokens[0]
            blocks.setdefault(first_token, []).append((edgar_name, edgar_record))
            # Also index by second token (if present) for cases where the first
            # token is a common word that survived normalization.
            if len(tokens) > 1:
                blocks.setdefault(tokens[1], []).append((edgar_name, edgar_record))
        return blocks

    def _find_name_match(
        self,
        finra_record: FinraBrokerDealerRecord,
        edgar_by_name: dict[str, EdgarBrokerDealerRecord],
        name_block_index: dict[str, list[tuple[str, EdgarBrokerDealerRecord]]] | None = None,
    ) -> EdgarBrokerDealerRecord | None:
        """Attempt a fuzzy name match between a FINRA record and the EDGAR index.

        Uses a blocking strategy: first narrows candidates by shared first/second
        token, then applies SequenceMatcher only to the blocked subset.  Falls back
        to a full scan if the block produces no candidates.

        Requires both a high name similarity score AND matching state (if available)
        to avoid false positives across firms with similar names in different states.
        """
        finra_name = normalize_entity_name(finra_record.name)
        if not finra_name:
            return None

        finra_state = (finra_record.address_state or "").strip().upper()

        # Determine the candidate set via blocking.
        candidates: list[tuple[str, EdgarBrokerDealerRecord]] | None = None
        if name_block_index is not None:
            finra_tokens = finra_name.split()
            candidate_set: dict[str, tuple[str, EdgarBrokerDealerRecord]] = {}
            for token in finra_tokens[:3]:
                for item in name_block_index.get(token, []):
                    candidate_set[item[0]] = item
            if candidate_set:
                candidates = list(candidate_set.values())

        # Fall back to full scan if blocking produced no candidates.
        if candidates is None:
            candidates = list(edgar_by_name.items())

        best_score = 0.0
        best_match: EdgarBrokerDealerRecord | None = None

        for edgar_name, edgar_record in candidates:
            score = SequenceMatcher(None, finra_name, edgar_name).ratio()
            if score < _FUZZY_MATCH_THRESHOLD:
                continue

            # State cross-check: if both have states, they must match.
            edgar_state = (edgar_record.state or "").strip().upper()
            if finra_state and edgar_state and finra_state != edgar_state:
                continue

            if score > best_score:
                best_score = score
                best_match = edgar_record

        return best_match
