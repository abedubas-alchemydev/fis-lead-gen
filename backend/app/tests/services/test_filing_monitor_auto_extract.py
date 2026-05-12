"""Unit tests for the filing-monitor auto re-extraction filter.

``filter_auto_extract_bd_ids`` reduces the ``upsert_many`` output to the
broker-dealers whose newly-inserted alerts should trigger a per-firm
financial re-extraction. The filter has two predicates:

  1. ``form_type == "Form X-17A-5"`` — Form BD (new registrations) and
     Form 17a-11 (deficiency notices) are surfaced as alerts but carry
     no FOCUS-report bytes for the financial pipeline to extract.
  2. ``bd_id in watched_bd_ids`` — bounds Gemini cost to the firms we
     actively care about (favorites OR ``lead_priority='hot'``).

The output must be deduped + sorted for stable enqueue order.
"""

from __future__ import annotations

from app.services.filing_monitor import filter_auto_extract_bd_ids


def test_empty_inserted_rows_returns_empty() -> None:
    assert filter_auto_extract_bd_ids([], {1, 2, 3}) == []


def test_empty_watched_set_returns_empty() -> None:
    rows = [(1, 10, "Form X-17A-5"), (2, 20, "Form X-17A-5")]
    assert filter_auto_extract_bd_ids(rows, set()) == []


def test_filters_form_type_to_x17a5() -> None:
    """Form BD + Form 17a-11 alerts MUST be excluded even when the firm
    is watched — they carry no financial-pipeline payload."""
    rows = [
        (1, 10, "Form BD"),
        (2, 20, "Form 17a-11"),
        (3, 30, "Form X-17A-5"),
    ]
    watched = {10, 20, 30}
    assert filter_auto_extract_bd_ids(rows, watched) == [30]


def test_filters_unwatched_firms() -> None:
    """A new X-17A-5 on an unwatched firm must be ignored — that's the
    cost-bounding promise of the hybrid design."""
    rows = [
        (1, 10, "Form X-17A-5"),
        (2, 20, "Form X-17A-5"),
        (3, 30, "Form X-17A-5"),
    ]
    watched = {20}
    assert filter_auto_extract_bd_ids(rows, watched) == [20]


def test_dedupes_when_one_firm_has_multiple_x17a5_inserts() -> None:
    """A single batch can include a 2024 and 2023 X-17A-5 for the same
    firm (e.g. a late-filed amendment) — both rows are valid, but we
    only want to enqueue ONE extraction (the pipeline picks the latest
    PDF anyway, so two enqueues would just race)."""
    rows = [
        (101, 42, "Form X-17A-5"),
        (102, 42, "Form X-17A-5"),
    ]
    watched = {42}
    assert filter_auto_extract_bd_ids(rows, watched) == [42]


def test_output_is_sorted_for_stable_enqueue() -> None:
    rows = [
        (1, 30, "Form X-17A-5"),
        (2, 10, "Form X-17A-5"),
        (3, 20, "Form X-17A-5"),
    ]
    watched = {10, 20, 30}
    assert filter_auto_extract_bd_ids(rows, watched) == [10, 20, 30]


def test_combination_of_form_type_and_watched_filter() -> None:
    """Both predicates active — only the X-17A-5 row for the watched
    firm survives."""
    rows = [
        (1, 10, "Form X-17A-5"),  # watched, right form  ✓
        (2, 20, "Form X-17A-5"),  # not watched, dropped
        (3, 10, "Form BD"),       # watched but wrong form, dropped
        (4, 30, "Form 17a-11"),   # not watched + wrong form, dropped
    ]
    watched = {10, 99}
    assert filter_auto_extract_bd_ids(rows, watched) == [10]
