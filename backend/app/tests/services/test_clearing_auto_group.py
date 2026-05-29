"""Unit tests for the auto-grouping layer — pure Python, no DB.

``auto_group_long_tail`` and ``matching_raw_values`` are the source of
truth for BOTH the Clearing Arrangement dropdown (its ``.keys()``) and the
filter expansion (its buckets), so these tests pin the grouping logic and,
critically, the dropdown↔filter consistency invariant (T7).

The endpoint wiring (``GET /broker-dealers/clearing-partners`` →
``repository.list_clearing_partners``) is thin delegation over these pure
functions; an API-level test there would exercise FastAPI plumbing rather
than this logic, and a list-endpoint round-trip needs a Postgres fixture
the local env can't provide (no pgvector). T7's expansion check covers the
round-trip at the unit level instead.
"""

from __future__ import annotations

from app.services.clearing_auto_group import (
    _shortest_base_label,
    auto_group_long_tail,
    matching_raw_values,
)
from app.services.clearing_consolidation import ProviderEntry
from app.services.clearing_partner_clustering import cluster_partners


# T1
def test_prefix_variant_collapses_under_shorter_base_label() -> None:
    """The client's case: a base name and a longer variant collapse into
    one entry keyed by the shorter base name, covering both raws."""

    values = ["Acme Securities", "Acme Securities Trust Co"]
    groups = auto_group_long_tail(values, [])
    assert set(groups.keys()) == {"Acme Securities"}
    assert groups["Acme Securities"] == {
        "Acme Securities",
        "Acme Securities Trust Co",
    }


# T2
def test_suffix_variants_group_under_one_label() -> None:
    """Pure suffix/punctuation variants of one firm collapse to a single
    entry (whatever shortest-base label wins), covering all variants."""

    values = [
        "RBC Capital Markets, LLC",
        "RBC Capital Markets LLC",
        "RBC Capital Markets Corp",
    ]
    groups = auto_group_long_tail(values, [])
    assert len(groups) == 1
    (members,) = groups.values()
    assert members == set(values)


# T3
def test_rejected_signature_splits_cluster_into_singletons() -> None:
    """A cluster whose signature an admin rejected is suppressed — its
    members fall back to individual entries."""

    values = [
        "Acme Securities",
        "Acme Securities Trust Co",
        "Acme Securities Holdings",
    ]
    # The signature auto_group_long_tail will compute for this membership
    # (same pass, same trimmed inputs -> same signature).
    clusters = cluster_partners([v.strip() for v in values])
    assert len(clusters) == 1
    rejected = frozenset({clusters[0].signature})

    grouped = auto_group_long_tail(values, [], rejected)
    assert set(grouped.keys()) == set(values)
    for value in values:
        assert grouped[value] == {value}


# T4
def test_registry_matched_values_are_not_auto_clustered() -> None:
    """Curated registry stays authoritative: matched raws key under the
    provider display label; only the unmatched remainder auto-groups."""

    providers = [
        ProviderEntry(
            canonical_name="Pershing LLC",
            display_label="Pershing",
            aliases=("BNY Pershing",),
        )
    ]
    values = [
        "PERSHING LLC",
        "BNY PERSHING",
        "Acme Securities",
        "Acme Securities Trust Co",
    ]
    groups = auto_group_long_tail(values, providers)
    assert set(groups.keys()) == {"Pershing", "Acme Securities"}
    assert groups["Pershing"] == {"PERSHING LLC", "BNY PERSHING"}
    assert groups["Acme Securities"] == {
        "Acme Securities",
        "Acme Securities Trust Co",
    }


# T5
def test_unique_long_tail_value_keeps_its_own_text() -> None:
    values = ["ZQ Capital Holdings"]
    groups = auto_group_long_tail(values, [])
    assert groups == {"ZQ Capital Holdings": {"ZQ Capital Holdings"}}


# T6
def test_distinct_brands_stay_separate() -> None:
    """Names sharing only a parent token (different first token, score <80)
    must not merge."""

    values = ["Citigroup Global Markets", "Citibank N.A."]
    groups = auto_group_long_tail(values, [])
    assert set(groups.keys()) == {"Citigroup Global Markets", "Citibank N.A."}


# T7  (the critical one)
def test_dropdown_filter_consistency_invariant() -> None:
    """Every non-empty raw lands in exactly one bucket; selecting every
    dropdown label expands back to every raw; selecting one label returns
    exactly its bucket. This is what guarantees the label a user sees maps
    to the rows they get."""

    providers = [
        ProviderEntry(
            canonical_name="Pershing LLC",
            display_label="Pershing",
            aliases=("BNY Pershing",),
        )
    ]
    values = [
        "PERSHING LLC",
        "BNY PERSHING",
        "Acme Securities",
        "Acme Securities Trust Co",
        "RBC Capital Markets, LLC",
        "RBC Capital Markets LLC",
        "Citibank N.A.",
        "   ",  # whitespace-only -> skipped
        None,  # skipped
    ]
    groups = auto_group_long_tail(values, providers)
    non_empty = {v for v in values if v and v.strip()}

    members = [m for bucket in groups.values() for m in bucket]
    assert len(members) == len(set(members)), "buckets must be pairwise disjoint"
    assert sorted(members) == sorted(non_empty), "buckets must cover every raw"

    selected_all = list(groups.keys())
    assert matching_raw_values(selected_all, groups, values) == non_empty

    for label, bucket in groups.items():
        assert matching_raw_values([label], groups, values) == bucket


# T8
def test_whitespace_padded_original_survives_into_bucket() -> None:
    """Labels are trimmed, but the IN clause must match the EXACT stored
    value — so the padded original, not its trimmed copy, lands in the
    bucket."""

    values = [" Acme Securities ", "Acme Securities Trust Co"]
    groups = auto_group_long_tail(values, [])
    assert set(groups.keys()) == {"Acme Securities"}
    assert groups["Acme Securities"] == {
        " Acme Securities ",
        "Acme Securities Trust Co",
    }


# T9
def test_shortest_base_label_tiebreak() -> None:
    # Fewer content tokens wins outright.
    assert (
        _shortest_base_label(["Acme Securities Trust Co", "Acme Securities"])
        == "Acme Securities"
    )
    # Token-count tie (LLC is stripped) -> shorter raw length wins.
    assert (
        _shortest_base_label(["Acme Securities LLC", "Acme Securities"])
        == "Acme Securities"
    )


# T10
def test_oversized_block_falls_back_to_singletons() -> None:
    """A first-token block over the default cap (50) is skipped, so those
    values stay ungrouped rather than pinning the pass — inherited safety
    valve from cluster_partners."""

    values = [f"Bank {i} National Association" for i in range(60)]
    groups = auto_group_long_tail(values, [])
    assert len(groups) == 60
    for value in values:
        assert groups[value] == {value}
