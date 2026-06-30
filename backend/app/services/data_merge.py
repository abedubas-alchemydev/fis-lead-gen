from __future__ import annotations

import asyncio
import logging

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.services.apollo import ApolloClient, ApolloError
from app.services.normalization import normalize_entity_name, normalize_sec_file_number
from app.services.service_models import (
    BadSourceRow,
    EdgarBrokerDealerRecord,
    FinraBrokerDealerRecord,
    MergeQAReport,
    MergedBrokerDealerRecord,
)


logger = logging.getLogger(__name__)

# Pause between successive Apollo organizations calls during the post-merge
# website fallback. Apollo's documented rate limit is generous, but the merge
# pass touches every firm in the dataset (~3-4k) so a small jitter keeps the
# burst below their 429 line and matches the cadence of the executive Apollo
# enrichment path in ``focus_ceo_extraction.py``.
_APOLLO_ORG_LOOKUP_DELAY_S: float = 0.25

# ──────────────────────────────────────────────────────────────
# Configuration constants
# ──────────────────────────────────────────────────────────────
# Threshold for fuzzy name matching when SEC file numbers don't match.
_FUZZY_MATCH_THRESHOLD = 0.88

# Only firms with these statuses survive into the output.
_ACTIVE_STATUSES = frozenset({"active"})


@dataclass(slots=True)
class EdgarIndex:
    """The reusable EDGAR lookup index built once by
    :meth:`BrokerDealerMergeService.build_edgar_index`.

    Holds everything the FINRA matching loop needs to resolve a firm without
    rescanning the EDGAR dataset:

    * ``by_sec`` — normalized SEC file number → EDGAR record (primary key).
    * ``by_name`` — normalized entity name → EDGAR record (fuzzy fallback).
    * ``name_block_index`` — first/second-token blocking index that keeps the
      fuzzy match near O(N) instead of O(N*M).

    The streaming initial-load path builds this once over the (comparatively
    cheap, EDGAR-only) dataset and then reuses it across every FINRA chunk, so
    the heavy per-firm FINRA enrichment is the only thing that ever needs to
    be sliced for memory.
    """

    by_sec: dict[str, EdgarBrokerDealerRecord]
    by_name: dict[str, EdgarBrokerDealerRecord]
    name_block_index: dict[str, list[tuple[str, EdgarBrokerDealerRecord]]]


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

        Thin wrapper that composes the three reusable pieces over the ENTIRE
        FINRA set in one pass: :meth:`build_edgar_index` (once) →
        :meth:`merge_chunk` (the whole list as a single chunk) →
        :meth:`finalize` (once). ``scripts.initial_load`` and the existing
        service-contract tests keep calling this and get the identical
        ``(list, MergeQAReport)`` shape. The OOM-hardened initial-load
        background task calls the same three pieces directly but streams FINRA
        in small chunks, so the heavy per-firm enrichment is never resident in
        memory all at once.
        """
        report = MergeQAReport(
            edgar_input_count=len(edgar_records),
            finra_input_count=len(finra_records),
        )
        edgar_index = self.build_edgar_index(edgar_records, report)

        seen_sec_numbers: set[str] = set()
        matched_edgar_secs: set[str] = set()
        merged = self.merge_chunk(
            finra_records,
            edgar_index,
            seen_sec_numbers=seen_sec_numbers,
            matched_edgar_secs=matched_edgar_secs,
            report=report,
        )

        self.finalize(edgar_index, matched_edgar_secs, report)
        return merged, report

    def build_edgar_index(
        self,
        edgar_records: list[EdgarBrokerDealerRecord],
        report: MergeQAReport | None = None,
    ) -> EdgarIndex:
        """Index the EDGAR dataset once, up front, for reuse across chunks.

        Builds the SEC-file-number lookup (the primary match key), the
        normalized-name lookup (fuzzy fallback), and the first/second-token
        blocking index that keeps fuzzy matching near O(N). When a ``report``
        is supplied, bad / duplicate EDGAR rows are recorded on it so the
        single-pass :meth:`merge` and the chunked initial-load path produce
        byte-identical QA numbers. Pulling this out of the FINRA loop is what
        lets the streaming path build the (cheap, EDGAR-only) index a single
        time and then stream FINRA past it chunk by chunk.
        """
        # ── Index EDGAR records by normalized SEC file number ──
        edgar_by_sec: dict[str, EdgarBrokerDealerRecord] = {}
        edgar_by_name: dict[str, EdgarBrokerDealerRecord] = {}
        for record in edgar_records:
            normalized_sec = normalize_sec_file_number(record.sec_file_number)
            if normalized_sec is None:
                if report is not None:
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
                if report is not None:
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

        # ── Build blocking index for fast fuzzy matching ──
        name_block_index = self._build_name_block_index(edgar_by_name)
        return EdgarIndex(
            by_sec=edgar_by_sec,
            by_name=edgar_by_name,
            name_block_index=name_block_index,
        )

    def merge_chunk(
        self,
        finra_chunk: list[FinraBrokerDealerRecord],
        edgar_index: EdgarIndex,
        *,
        seen_sec_numbers: set[str],
        matched_edgar_secs: set[str],
        report: MergeQAReport,
    ) -> list[MergedBrokerDealerRecord]:
        """Match one chunk of FINRA records against the pre-built EDGAR index.

        Returns the merged rows for THIS chunk only. Cross-chunk state is
        threaded in by the caller so a sequence of ``merge_chunk`` calls is
        indistinguishable from a single ``merge`` over the concatenation:

        * ``seen_sec_numbers`` — FINRA SEC file numbers already emitted; a
          duplicate firm surfacing in a later chunk is suppressed exactly as
          it would be within a single pass.
        * ``matched_edgar_secs`` — EDGAR SEC numbers already claimed by a
          FINRA match, so :meth:`finalize` can report the genuinely
          unresolved EDGAR rows once, after the last chunk.
        * ``report`` — the shared QA report; per-row counters accumulate
          across chunks.
        """
        edgar_by_sec = edgar_index.by_sec
        edgar_by_name = edgar_index.by_name
        name_block_index = edgar_index.name_block_index

        merged: list[MergedBrokerDealerRecord] = []

        for finra_record in finra_chunk:
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
                    website_source=finra_record.website_source,
                    types_of_business=finra_record.types_of_business,
                    direct_owners=finra_record.direct_owners,
                    executive_officers=finra_record.executive_officers,
                    firm_operations_text=finra_record.firm_operations_text,
                    dba_names=finra_record.dba_names,
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
                    website_source=finra_record.website_source,
                    types_of_business=finra_record.types_of_business,
                    direct_owners=finra_record.direct_owners,
                    executive_officers=finra_record.executive_officers,
                    firm_operations_text=finra_record.firm_operations_text,
                    dba_names=finra_record.dba_names,
                ))
                report.finra_only_count += 1

        return merged

    def finalize(
        self,
        edgar_index: EdgarIndex,
        matched_edgar_secs: set[str],
        report: MergeQAReport,
    ) -> None:
        """Close out a merge: account for unmatched EDGAR rows and stamp the
        final output count.

        Runs EXACTLY ONCE, after every FINRA chunk has been merged — never per
        chunk. EDGAR-only rows (no FINRA match anywhere across the whole run)
        are dropped, but each is counted and logged so the QA report stays
        honest. Driving this off the shared ``matched_edgar_secs`` accumulator
        is what makes ``edgar_unresolved_count`` match the single-pass
        ``merge`` exactly: calling it once means an unmatched EDGAR row is
        reported a single time, not re-counted for every chunk that failed to
        claim it.
        """
        # ── Count unresolved EDGAR rows (dropped, not emitted) ──
        for sec_number, edgar_record in edgar_index.by_sec.items():
            if sec_number not in matched_edgar_secs:
                report.edgar_unresolved_count += 1
                report.bad_source_rows.append(BadSourceRow(
                    source="edgar",
                    identifier=edgar_record.cik,
                    name=edgar_record.name,
                    reason="No matching FINRA record found — dropped (EDGAR-only not emitted)",
                ))

        # Streaming-friendly equivalent of ``len(merged)`` in the old one-shot
        # merge: every emitted row incremented exactly one of these two
        # counters, so their sum is the total output row count across chunks.
        report.output_count = report.matched_both_count + report.finra_only_count

    async def apply_apollo_website_fallback(
        self,
        records: list[MergedBrokerDealerRecord],
        apollo_client: ApolloClient,
        *,
        delay_s: float = _APOLLO_ORG_LOOKUP_DELAY_S,
    ) -> dict[str, int]:
        """Populate ``website`` from Apollo for records that FINRA missed.

        Walks the merged records in place. For each record where ``website``
        is None, calls ``ApolloClient.search_organization`` with the firm
        name (and CRD when available) and, on a hit, stamps
        ``record.website = org.website_url`` plus
        ``record.website_source = "apollo"``. Records that already have a
        FINRA-sourced website are skipped — wasted Apollo spend, and the
        FINRA Form BD value is the authoritative one.

        ``ApolloError`` (5xx / 429-after-retries / network) is the
        provider-error path. We log a structured warning and continue to
        the next record so one outage doesn't blow up the whole merge run.
        The next pipeline run retries naturally because the row's website
        stays NULL — this is the same convention as the executive Apollo
        fallback in ``focus_ceo_extraction.py``.

        Returns a counts dict so callers can stamp pipeline_run.notes:
        ``{"apollo_filled": N, "apollo_no_match": M, "apollo_error": K}``.
        """
        counts = {"apollo_filled": 0, "apollo_no_match": 0, "apollo_error": 0}
        total = len(records)
        for index, record in enumerate(records):
            if record.website:
                continue

            try:
                org = await apollo_client.search_organization(
                    record.name, record.crd_number
                )
            except ApolloError as exc:
                logger.warning(
                    "apollo_org_lookup_failed for '%s' (CRD %s): %s",
                    record.name,
                    record.crd_number,
                    exc,
                )
                counts["apollo_error"] += 1
                # Pause even on error to avoid hammering an already-stressed
                # provider; the backoff inside the client only spans retries
                # within one call, not the cadence between distinct calls.
                if delay_s > 0 and index < total - 1:
                    await asyncio.sleep(delay_s)
                continue

            if org is not None and org.website_url:
                record.website = org.website_url
                record.website_source = "apollo"
                counts["apollo_filled"] += 1
            else:
                counts["apollo_no_match"] += 1

            if delay_s > 0 and index < total - 1:
                await asyncio.sleep(delay_s)

        return counts

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
