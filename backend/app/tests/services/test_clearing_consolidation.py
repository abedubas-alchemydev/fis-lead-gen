"""Unit tests for the clearing-partner consolidator.

The consolidator powers two coordinated behaviors on the master-list
filter:

  1. The dropdown shows one canonical short label ("Pershing") per
     known firm, deduping the raw text variants ("PERSHING LLC",
     "PERSHING NFS", "BNY PERSHING") that arrive from PDF extraction.
  2. The filter, when given a canonical label, returns BDs whose raw
     ``current_clearing_partner`` value belongs to that group.

Both surfaces share the same ``CompetitorProvider`` registry. These
tests pin three things:

  * Variant raw strings collapse to the right display label.
  * Sister-brand collisions (e.g. "RBC Capital Markets" vs the seeded
    "RBC Correspondent Services") do *not* collapse — the bare-brand
    alias removal documented at the top of ``competitors.py`` must
    survive any refactor of the consolidator.
  * Long-tail raw values that don't match any provider survive intact.
  * The filter-predicate helper returns the *complete* set of raw
    values that map to each selected label, plus the legacy raw-value
    pass-through used by the dashboard provider-distribution chart.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, String

from app.services.clearing_consolidation import (
    ProviderEntry,
    consolidate_partner,
    consolidated_label_set,
    expand_filter_predicate,
)


# Mirrors the relevant subset of DEFAULT_COMPETITORS so the tests are
# decoupled from the live seed (adding more entries to the seed should
# not change these expectations).
@pytest.fixture
def providers() -> list[ProviderEntry]:
    return [
        ProviderEntry(
            canonical_name="Pershing LLC",
            display_label="Pershing",
            aliases=("Pershing", "BNY Pershing"),
        ),
        ProviderEntry(
            canonical_name="Apex Clearing Corporation",
            display_label="Apex",
            aliases=("Apex Clearing",),
        ),
        ProviderEntry(
            canonical_name="RBC Correspondent Services",
            display_label="RBC",
            aliases=("RBC Correspondent",),
        ),
        ProviderEntry(
            canonical_name="National Financial Services LLC",
            display_label="NFS / Fidelity",
            aliases=(
                "National Financial Services",
                "Fidelity Clearing",
                "Fidelity Brokerage Services",
            ),
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# consolidate_partner
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Pershing LLC", "Pershing"),
        ("PERSHING LLC", "Pershing"),
        ("PERSHING LLC NFS", "Pershing"),
        ("BNY Pershing", "Pershing"),
        ("BNY  Pershing", "Pershing"),  # whitespace variants
        ("  pershing  ", "Pershing"),
        ("Apex Clearing Corp", "Apex"),
        ("Apex Clearing Corporation", "Apex"),
        ("RBC Correspondent Services", "RBC"),
        ("National Financial Services", "NFS / Fidelity"),
        ("Fidelity Clearing & Custody Solutions", "NFS / Fidelity"),
    ],
)
def test_consolidate_known_aliases_collapse_to_display_label(
    providers: list[ProviderEntry], raw: str, expected: str
) -> None:
    assert consolidate_partner(raw, providers) == expected


def test_rbc_capital_markets_does_not_collapse_to_rbc(
    providers: list[ProviderEntry],
) -> None:
    """The bare-brand alias `RBC` was deliberately removed from the seed
    because it would over-match `RBC Capital Markets` — a sister entity
    that is *not* the seeded clearing partner. Regression guard for that
    rule (see comment at the top of ``app/services/competitors.py``)."""

    assert consolidate_partner("RBC Capital Markets", providers) == (
        "RBC Capital Markets"
    )


def test_long_tail_raw_value_passes_through_trimmed(
    providers: list[ProviderEntry],
) -> None:
    raw = "  Joe's Random Clearing Inc  "
    assert consolidate_partner(raw, providers) == "Joe's Random Clearing Inc"


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Trailing period — `\b` at end-of-string after `.` would not
        # fire; lookarounds do.
        ("BofA Securities, Inc.", "BofA Securities"),
        ("BofA Securities Inc.", "BofA Securities"),
        # Trailing `)` — same problem class as `.`. Pattern ends in a
        # non-word char and the next char is whitespace (also non-word).
        ("Mirae Asset Securities (USA)", "Mirae Asset"),
        ("Mirae Asset Securities (USA), Inc.", "Mirae Asset"),
    ],
)
def test_alias_ending_in_non_word_char_matches_its_own_raw(
    raw: str, expected: str
) -> None:
    """Regression: aliases ending in ``.`` or ``)`` failed to match their
    own raw values when the matcher used ``\\b...\\b`` because Python's
    ``\\b`` doesn't fire between two non-word chars. Switched to
    ``(?<!\\w)...(?!\\w)`` lookarounds; pin both classes here."""

    providers_with_punctuation = [
        ProviderEntry(
            canonical_name="BofA Securities, Inc.",
            display_label="BofA Securities",
            aliases=("BofA Securities Inc.", "BofA Securities, Inc."),
        ),
        ProviderEntry(
            canonical_name="Mirae Asset Securities (USA) LLC",
            display_label="Mirae Asset",
            aliases=("Mirae Asset Securities (USA)",),
        ),
    ]
    assert consolidate_partner(raw, providers_with_punctuation) == expected


def test_lookaround_matcher_does_not_break_existing_word_boundary_guard(
    providers: list[ProviderEntry],
) -> None:
    """Sanity: the ``\\b`` -> lookaround swap must still reject prefix
    collisions. ``Pershington`` is the canonical example and was the
    original motivation for whole-word matching."""

    assert consolidate_partner("Pershington Securities", providers) == (
        "Pershington Securities"
    )
    assert consolidate_partner("BNY Pershington", providers) == "BNY Pershington"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_consolidate_empty_inputs_return_none(
    providers: list[ProviderEntry], raw: str | None
) -> None:
    assert consolidate_partner(raw, providers) is None


# ─────────────────────────────────────────────────────────────────────────────
# consolidated_label_set
# ─────────────────────────────────────────────────────────────────────────────


def test_dropdown_dedups_pershing_variants_and_keeps_long_tail(
    providers: list[ProviderEntry],
) -> None:
    raw_distinct = [
        "PERSHING LLC",
        "Pershing",
        "BNY Pershing",
        "Apex Clearing",
        "Joe's Clearing Inc",
        None,
    ]
    labels = consolidated_label_set(raw_distinct, providers)
    assert labels == ["Apex", "Joe's Clearing Inc", "Pershing"]


def test_dropdown_alpha_sort_is_case_insensitive(
    providers: list[ProviderEntry],
) -> None:
    raw_distinct = ["zebra inc", "Apex Clearing", "alpha holdings"]
    labels = consolidated_label_set(raw_distinct, providers)
    assert labels == ["alpha holdings", "Apex", "zebra inc"]


# ─────────────────────────────────────────────────────────────────────────────
# expand_filter_predicate
# ─────────────────────────────────────────────────────────────────────────────


def _raw_column():
    """Detached SQLAlchemy column suitable for use in `.in_()` predicates
    without binding to a real table."""

    return Column("current_clearing_partner", String)


def _compile(predicate) -> str:
    return str(
        predicate.compile(compile_kwargs={"literal_binds": True})
    ).lower()


def test_filter_expands_pershing_label_to_all_raw_variants(
    providers: list[ProviderEntry],
) -> None:
    raw_column = _raw_column()
    distinct_raw = [
        "PERSHING LLC",
        "PERSHING NFS",
        "BNY Pershing",
        "Apex Clearing",
        "Joe's Clearing",
    ]

    predicate = expand_filter_predicate(
        ["Pershing"], providers, raw_column, distinct_raw
    )

    assert predicate is not None
    sql = _compile(predicate)
    assert "'pershing llc'" in sql
    assert "'pershing nfs'" in sql
    assert "'bny pershing'" in sql
    assert "'apex clearing'" not in sql
    assert "'joe''s clearing'" not in sql


def test_filter_combines_multiple_canonical_labels(
    providers: list[ProviderEntry],
) -> None:
    raw_column = _raw_column()
    distinct_raw = ["PERSHING LLC", "Apex Clearing", "Joe's Clearing"]

    predicate = expand_filter_predicate(
        ["Pershing", "Apex"], providers, raw_column, distinct_raw
    )

    assert predicate is not None
    sql = _compile(predicate)
    assert "'pershing llc'" in sql
    assert "'apex clearing'" in sql
    assert "'joe''s clearing'" not in sql


def test_filter_passes_through_long_tail_label_exactly(
    providers: list[ProviderEntry],
) -> None:
    """A label that doesn't match any provider (a long-tail raw value
    selected from the dropdown) should match its own raw row only."""

    raw_column = _raw_column()
    distinct_raw = ["Joe's Clearing", "PERSHING LLC"]

    predicate = expand_filter_predicate(
        ["Joe's Clearing"], providers, raw_column, distinct_raw
    )

    assert predicate is not None
    sql = _compile(predicate)
    assert "'joe''s clearing'" in sql
    assert "'pershing llc'" not in sql


def test_filter_accepts_raw_value_label_for_legacy_chart_links(
    providers: list[ProviderEntry],
) -> None:
    """The dashboard provider-distribution chart pushes raw values like
    ``?clearing_partner=PERSHING+LLC`` directly. The filter should still
    return that single raw row even though the canonical label is
    ``Pershing``."""

    raw_column = _raw_column()
    distinct_raw = ["PERSHING LLC", "PERSHING NFS"]

    predicate = expand_filter_predicate(
        ["PERSHING LLC"], providers, raw_column, distinct_raw
    )

    assert predicate is not None
    sql = _compile(predicate)
    assert "'pershing llc'" in sql
    # The other raw variant is NOT pulled in: a raw-value selection is a
    # narrower filter than a canonical-label selection by design.
    assert "'pershing nfs'" not in sql


def test_filter_returns_false_when_no_raw_value_matches(
    providers: list[ProviderEntry],
) -> None:
    """A garbage label should yield an explicit "match nothing" predicate,
    not silently fall back to "no filter" — that would expose every BD."""

    raw_column = _raw_column()
    distinct_raw = ["PERSHING LLC", "Apex Clearing"]

    predicate = expand_filter_predicate(
        ["Pershington Typo"], providers, raw_column, distinct_raw
    )

    assert predicate is not None
    sql = _compile(predicate)
    assert sql.strip() == "false"


def test_filter_returns_none_for_empty_selection(
    providers: list[ProviderEntry],
) -> None:
    raw_column = _raw_column()
    assert (
        expand_filter_predicate([], providers, raw_column, ["PERSHING LLC"])
        is None
    )
    assert (
        expand_filter_predicate(["", "  "], providers, raw_column, ["PERSHING LLC"])
        is None
    )
