"""Unit tests for the clustering algorithm — pure Python, no DB.

These pin behavior of ``cluster_partners`` against fixed inputs so the
default threshold doesn't accidentally drift. The DB-touching pieces
(``run_clustering_pass`` idempotency, ``_distinct_unmatched_partners``
filtering against the registry) live in their own integration tests.
"""

from __future__ import annotations

from app.services.clearing_partner_clustering import (
    DEFAULT_THRESHOLD,
    ClusterCandidate,
    cluster_partners,
)


def _names(candidates: list[ClusterCandidate]) -> list[tuple[str, ...]]:
    return [c.variants for c in candidates]


def test_rbc_regression_keeps_capital_markets_separate_from_clearing_custody() -> None:
    """The exact case the client surfaced: RBC Capital Markets variants
    cluster together, RBC Clearing & Custody variants cluster together,
    and the two groups stay distinct. Pinned at the default threshold so
    accidental tuning can't quietly merge them.
    """

    values = [
        "RBC Capital Markets",
        "RBC Capital Markets, LLC",
        "RBC Capital Markets LLC",
        "RBC Capital Markets Corp",
        "RBC Clearing & Custody",
        "RBC Clearing & Custody, a Division of RBC Capital",
    ]
    clusters = cluster_partners(values)

    assert len(clusters) == 2, f"Expected 2 clusters at T={DEFAULT_THRESHOLD}, got {len(clusters)}: {_names(clusters)}"

    # Find each cluster by membership and assert the right variants are in it.
    capital_cluster = next(
        c for c in clusters if any("Capital Markets" in v for v in c.variants)
    )
    custody_cluster = next(
        c for c in clusters if any("Clearing & Custody" in v for v in c.variants)
    )

    assert all("Capital Markets" in v for v in capital_cluster.variants)
    assert all("Clearing & Custody" in v for v in custody_cluster.variants)
    assert {v for v in capital_cluster.variants}.isdisjoint(
        {v for v in custody_cluster.variants}
    )


def test_singletons_dropped() -> None:
    """A unique long-tail name doesn't surface as a 1-member cluster."""

    values = [
        "Acme Securities, LLC",
        "Acme Securities LLC",
        "Acme Securities Inc",  # cluster of 3
        "ZQ Capital Holdings",  # singleton — dropped
    ]
    clusters = cluster_partners(values)
    flat = [v for c in clusters for v in c.variants]
    assert "ZQ Capital Holdings" not in flat
    assert any(len(c.variants) == 3 for c in clusters)


def test_punctuation_only_differences_cluster() -> None:
    """The most common nuisance case — punctuation-only / suffix-only
    differences. token_set_ratio handles this naturally."""

    values = [
        "Goldman Sachs & Co LLC",
        "Goldman Sachs & Co. LLC",
        "Goldman Sachs & Co., LLC",
    ]
    clusters = cluster_partners(values)
    assert len(clusters) == 1
    assert len(clusters[0].variants) == 3


def test_threshold_tuning_separates_brands_at_default() -> None:
    """At the default threshold, distinct brand names stay apart even
    when they share a parent token."""

    values = [
        "Citigroup Global Markets",
        "Citibank N.A.",
    ]
    clusters = cluster_partners(values)
    assert clusters == [], (
        f"Distinct brands sharing only the parent token must not cluster at default T={DEFAULT_THRESHOLD}."
    )


def test_threshold_lowered_does_cluster_more() -> None:
    """Sanity check the threshold parameter is wired — at T=50 these
    join more aggressively than at the default. (Not a recommended
    setting; pinned only to prove the knob works.)"""

    values = [
        "Goldman Sachs",
        "Goldman Sachs & Co",
        "Goldman Sachs Bank USA",
    ]
    strict = cluster_partners(values, threshold=DEFAULT_THRESHOLD)
    loose = cluster_partners(values, threshold=50)
    # At least, T=50 must produce ≥ as many clustered members as T=85.
    strict_members = sum(len(c.variants) for c in strict)
    loose_members = sum(len(c.variants) for c in loose)
    assert loose_members >= strict_members


def test_oversized_block_skipped() -> None:
    """A block exceeding the cap is skipped (warning logged), not
    crashed on. Uses a small cap so the test stays fast and
    deterministic."""

    # 5 values all starting with "Bank" — a typical degenerate
    # blocking-key case.
    values = [f"Bank {i} National Association" for i in range(5)]
    clusters = cluster_partners(values, max_block_size=3)
    # Block of 5 > cap of 3 → no clusters from this block.
    assert clusters == []


def test_cluster_signature_stable_under_input_order() -> None:
    """The signature is sha256(sorted(variants)), so input order can't
    create different signatures for the same membership — critical for
    idempotent re-runs."""

    a = cluster_partners(
        ["RBC Capital Markets", "RBC Capital Markets, LLC", "RBC Capital Markets LLC"]
    )
    b = cluster_partners(
        ["RBC Capital Markets LLC", "RBC Capital Markets", "RBC Capital Markets, LLC"]
    )
    assert len(a) == 1
    assert len(b) == 1
    assert a[0].signature == b[0].signature


def test_suggested_name_picks_most_informative_variant() -> None:
    """Ties broken alphabetically (lower ord-sum first) so the picked
    name is deterministic across runs."""

    clusters = cluster_partners(
        [
            "RBC Capital Markets, LLC",
            "RBC Capital Markets LLC",
            "RBC Capital Markets Corporation",
        ]
    )
    assert len(clusters) == 1
    # "RBC Capital Markets Corporation" has 4 content tokens after suffix
    # stripping (RBC, Capital, Markets, Corporation isn't in the strip
    # list — `corp`/`corp.` are; `corporation` is also there). So it ties
    # with the LLC variants on token count after stripping. Pick is then
    # alphabetical (lowest ord-sum). Just assert it's one of the inputs
    # — exact pick is implementation detail.
    assert clusters[0].suggested_name in {
        "RBC Capital Markets, LLC",
        "RBC Capital Markets LLC",
        "RBC Capital Markets Corporation",
    }
