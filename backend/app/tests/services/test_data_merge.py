"""Service-contract tests for ``BrokerDealerMergeService.merge``.

Pins the EDGAR/FINRA merge contract that both the ``scripts.initial_load``
script and the ``_run_initial_load_background`` API call site depend on:

* ``merge(edgar_records, finra_records)`` — EDGAR is the FIRST positional
  argument, FINRA the SECOND, and the method returns a
  ``(records, report)`` tuple. The initial-load background task once called
  this with the arguments INVERTED and never unpacked the tuple, so the
  scheduled run never inserted a single broker-dealer.
* A FINRA row that matches an EDGAR row on normalized SEC file number is
  emitted exactly once, classified ``matched_source == "both"``, carrying
  the CIK from EDGAR and the CRD from FINRA.
"""

from __future__ import annotations

import pytest

from app.services.data_merge import BrokerDealerMergeService
from app.services.service_models import (
    EdgarBrokerDealerRecord,
    FinraBrokerDealerRecord,
    MergedBrokerDealerRecord,
    MergeQAReport,
)


def _edgar_record(
    *,
    cik: str = "0001234567",
    name: str = "Acme Securities LLC",
    sec_file_number: str | None = "8-12345",
    state: str | None = "NY",
    city: str | None = "New York",
) -> EdgarBrokerDealerRecord:
    return EdgarBrokerDealerRecord(
        cik=cik,
        name=name,
        sic="6211",
        state=state,
        city=city,
        sec_file_number=sec_file_number,
        registration_date=None,
        last_filing_date=None,
        filings_index_url="https://www.sec.gov/cgi-bin/browse-edgar",
    )


def _finra_record(
    *,
    crd_number: str = "111",
    name: str = "Acme Securities LLC",
    sec_file_number: str | None = "8-12345",
    registration_status: str = "Active",
    address_state: str | None = "NY",
    address_city: str | None = "New York",
) -> FinraBrokerDealerRecord:
    return FinraBrokerDealerRecord(
        crd_number=crd_number,
        name=name,
        sec_file_number=sec_file_number,
        registration_status=registration_status,
        branch_count=1,
        address_city=address_city,
        address_state=address_state,
        business_type=None,
    )


def test_matches_edgar_and_finra_on_sec_file_number() -> None:
    """One active FINRA firm sharing a SEC file number with one EDGAR firm
    merges into a single ``matched_source == "both"`` row, EDGAR CIK + FINRA
    CRD carried through."""
    edgar = [_edgar_record()]
    finra = [_finra_record()]

    merged, report = BrokerDealerMergeService().merge(edgar, finra)

    assert len(merged) == 1
    row = merged[0]
    assert row.matched_source == "both"
    assert row.sec_file_number == "8-12345"
    assert row.cik == "0001234567"  # carried from EDGAR
    assert row.crd_number == "111"  # carried from FINRA
    assert report.matched_both_count == 1
    assert report.output_count == 1


def test_merge_arg_order_is_edgar_then_finra() -> None:
    """Guard the positional contract: EDGAR first, FINRA second.

    Inverting the arguments feeds an ``EdgarBrokerDealerRecord`` into the
    FINRA loop, which immediately reads ``.registration_status`` — a field
    EDGAR records don't have — and raises. The initial-load call site shipped
    exactly this inversion; pinning it here keeps a refactor from silently
    swapping the order back.
    """
    edgar = [_edgar_record()]
    finra = [_finra_record()]

    with pytest.raises(AttributeError):
        BrokerDealerMergeService().merge(finra, edgar)  # WRONG order on purpose


# ──────────────────────────────────────────────────────────────────────────
# Chunked merge == single merge (OOM-hardening: perf/initial-load-streaming)
#
# The initial-load harvest streams FINRA through ``merge_chunk`` in slices so
# the heavy per-firm enrichment is never resident for the whole ~3.2k-firm set
# at once. These tests pin that the split (``build_edgar_index`` once →
# ``merge_chunk`` per slice with shared accumulators → ``finalize`` once) is
# behaviourally identical to a single ``merge()`` over the same data.
# ──────────────────────────────────────────────────────────────────────────


def _key(record: MergedBrokerDealerRecord) -> tuple[str | None, str, str | None, str | None]:
    """Identity tuple for comparing merged rows: SEC file number + the
    classification + the carried-through CIK/CRD."""
    return (
        record.sec_file_number,
        record.matched_source,
        record.cik,
        record.crd_number,
    )


def _mixed_dataset() -> tuple[list[EdgarBrokerDealerRecord], list[FinraBrokerDealerRecord]]:
    """A dataset that exercises every merge outcome at once:

    * F1 ↔ E1 — SEC-number match → ``both``
    * F2, F3  — no EDGAR row → ``finra_only``
    * F4      — duplicate SEC number of F2; placed so it falls in the SECOND
                chunk, suppressed only if ``seen_sec_numbers`` is shared
    * E2      — no FINRA row → EDGAR-unresolved (dropped, must be counted once)
    """
    edgar = [
        _edgar_record(
            cik="0000000001", name="Alpha Securities LLC",
            sec_file_number="8-11111", state="NY", city="New York",
        ),
        _edgar_record(
            cik="0000000002", name="Beta Capital Partners LLC",
            sec_file_number="8-22222", state="NY", city="Albany",
        ),
    ]
    finra = [
        _finra_record(
            crd_number="111", name="Alpha Securities LLC",
            sec_file_number="8-11111", address_state="NY", address_city="New York",
        ),
        _finra_record(
            crd_number="222", name="Gamma Trading Co",
            sec_file_number="8-33333", address_state="CA", address_city="San Diego",
        ),
        _finra_record(
            crd_number="333", name="Delta Advisors Inc",
            sec_file_number="8-44444", address_state="TX", address_city="Austin",
        ),
        _finra_record(
            crd_number="444", name="Gamma Trading Co Duplicate",
            sec_file_number="8-33333", address_state="CA", address_city="San Diego",
        ),
    ]
    return edgar, finra


def _merge_in_chunks(
    service: BrokerDealerMergeService,
    edgar: list[EdgarBrokerDealerRecord],
    finra: list[FinraBrokerDealerRecord],
    *,
    chunk_size: int,
) -> tuple[list[MergedBrokerDealerRecord], MergeQAReport]:
    """Replicate the streaming initial-load harvest: build the EDGAR index
    once (recording EDGAR-side bad/dup rows into the shared report), run
    ``merge_chunk`` over each FINRA slice with shared accumulators, then call
    ``finalize`` exactly once."""
    report = MergeQAReport(
        edgar_input_count=len(edgar),
        finra_input_count=len(finra),
    )
    edgar_index = service.build_edgar_index(edgar, report)
    seen_sec_numbers: set[str] = set()
    matched_edgar_secs: set[str] = set()
    merged: list[MergedBrokerDealerRecord] = []
    for start in range(0, len(finra), chunk_size):
        chunk = finra[start : start + chunk_size]
        merged.extend(
            service.merge_chunk(
                chunk,
                edgar_index,
                seen_sec_numbers=seen_sec_numbers,
                matched_edgar_secs=matched_edgar_secs,
                report=report,
            )
        )
    service.finalize(edgar_index, matched_edgar_secs, report)
    return merged, report


def test_chunked_merge_matches_single_merge() -> None:
    """Streaming FINRA through ``merge_chunk`` in two chunks yields the EXACT
    same merged rows (and ``matched_source`` classification) as one ``merge()``
    over the whole set — the core invariant the OOM-hardening rewrite preserves.
    """
    edgar, finra = _mixed_dataset()

    single_merged, single_report = BrokerDealerMergeService().merge(edgar, finra)

    # 4 FINRA rows, chunk_size 2 → exactly two chunks, with the duplicate (F4)
    # landing in the second chunk so cross-chunk dedup is genuinely exercised.
    chunk_merged, chunk_report = _merge_in_chunks(
        BrokerDealerMergeService(), edgar, finra, chunk_size=2
    )

    # Same rows, same order, same classification + carried CIK/CRD.
    assert [_key(r) for r in chunk_merged] == [_key(r) for r in single_merged]
    assert {_key(r) for r in chunk_merged} == {
        ("8-11111", "both", "0000000001", "111"),
        ("8-33333", "finra_only", None, "222"),
        ("8-44444", "finra_only", None, "333"),
    }

    # Every QA counter the chunked path accumulates matches the single pass.
    assert chunk_report.output_count == single_report.output_count == 3
    assert chunk_report.matched_both_count == single_report.matched_both_count == 1
    assert chunk_report.finra_only_count == single_report.finra_only_count == 2
    # The duplicate (F4) is suppressed exactly once — only possible if
    # ``seen_sec_numbers`` is shared ACROSS chunks (F2 is in chunk 1, its
    # duplicate F4 in chunk 2).
    assert (
        chunk_report.duplicate_suppressed_count
        == single_report.duplicate_suppressed_count
        == 1
    )


def test_edgar_unresolved_reported_once_across_chunks() -> None:
    """``finalize`` runs ONCE for the whole run, not per chunk. E2 matches no
    FINRA row; across two chunks it must be counted a single time — running the
    EDGAR-unresolved pass per chunk would double-count it."""
    edgar, finra = _mixed_dataset()

    _single_merged, single_report = BrokerDealerMergeService().merge(edgar, finra)
    _chunk_merged, chunk_report = _merge_in_chunks(
        BrokerDealerMergeService(), edgar, finra, chunk_size=2
    )

    assert single_report.edgar_unresolved_count == 1
    # Once — NOT once-per-chunk (which would be 2 for this two-chunk split).
    assert chunk_report.edgar_unresolved_count == 1

    # And the dropped EDGAR row is logged exactly once in the bad-source log.
    edgar_unresolved_rows = [
        row
        for row in chunk_report.bad_source_rows
        if row.source == "edgar" and "No matching FINRA record" in row.reason
    ]
    assert len(edgar_unresolved_rows) == 1
    assert edgar_unresolved_rows[0].identifier == "0000000002"
