"""Unit tests for the clearing-membership name matcher.

Pure (no DB) — exercises ``app.services.clearing_membership_matcher``:
exact normalized matches auto-apply, same-key-multiple-firms route to
review, DBA/alias methods carry their confidence, and normalization parity
with ``normalize_entity_name`` (corporate noise tokens dropped) holds.
"""

from __future__ import annotations

from app.services.clearing_membership_matcher import (
    AMBIGUOUS_CONFIDENCE,
    CONFIDENCE_BY_METHOD,
    FirmIndex,
    index_firm,
    match_name,
)


def _index(*firms: tuple[int, list[tuple[str | None, str]]]) -> FirmIndex:
    index: FirmIndex = {}
    for firm_id, named in firms:
        index_firm(index, firm_id, named)
    return index


def test_exact_single_match_is_active() -> None:
    index = _index((1, [("Goldman Sachs & Co. LLC", "exact_normalized")]))
    results = match_name(index, "GOLDMAN SACHS & CO. LLC")
    assert len(results) == 1
    assert results[0].firm_id == 1
    assert results[0].status == "active"
    assert results[0].method == "exact_normalized"
    assert results[0].confidence == CONFIDENCE_BY_METHOD["exact_normalized"]


def test_normalization_parity_drops_corporate_tokens() -> None:
    # "& Co. LLC" and "and Company" both normalize to "goldman sachs".
    index = _index((1, [("Goldman Sachs & Co. LLC", "exact_normalized")]))
    results = match_name(index, "Goldman Sachs and Company")
    assert [r.firm_id for r in results] == [1]
    assert results[0].status == "active"


def test_same_key_two_firms_routes_both_to_review() -> None:
    index = _index(
        (10, [("Apex Clearing Corporation", "exact_normalized")]),
        (11, [("Apex Clearing Corp.", "exact_normalized")]),
    )
    results = match_name(index, "Apex Clearing")
    assert {r.firm_id for r in results} == {10, 11}
    assert all(r.status == "needs_review" for r in results)
    assert all(r.confidence == AMBIGUOUS_CONFIDENCE for r in results)


def test_exact_name_beats_alias_collision() -> None:
    # Regression (Pershing): "PERSHING LLC" must attach to the firm literally
    # named Pershing LLC (exact_normalized), not to a firm that only carries
    # "Pershing LLC" as a resolver alias. Weaker-method collisions are dropped,
    # so this is a clean single active match — not a needs_review tie.
    index = _index(
        (22280, [("Pershing LLC", "exact_normalized")]),
        (
            24228,
            [
                ("Pershing Advisor Solutions LLC", "exact_normalized"),
                ("Pershing LLC", "alias"),
            ],
        ),
    )
    results = match_name(index, "PERSHING LLC")
    assert len(results) == 1
    assert results[0].firm_id == 22280
    assert results[0].status == "active"
    assert results[0].method == "exact_normalized"


def test_dba_and_alias_methods_carry_confidence() -> None:
    index = _index((2, [("Acme Securities", "exact_normalized"), ("Acme Trading", "dba")]))
    dba_hit = match_name(index, "Acme Trading")
    assert dba_hit[0].method == "dba"
    assert dba_hit[0].status == "active"
    assert dba_hit[0].confidence == CONFIDENCE_BY_METHOD["dba"]

    alias_index = _index((3, [("BofA Securities", "exact_normalized"), ("Bank of America Securities", "alias")]))
    alias_hit = match_name(alias_index, "Bank of America Securities")
    assert alias_hit[0].method == "alias"
    assert alias_hit[0].confidence == CONFIDENCE_BY_METHOD["alias"]


def test_strongest_method_wins_for_same_firm_same_key() -> None:
    # Same firm contributes the same normalized key via both its legal name
    # (exact_normalized) and a DBA — the stronger method must win.
    index = _index((5, [("Pershing LLC", "exact_normalized"), ("Pershing", "dba")]))
    results = match_name(index, "Pershing")
    assert len(results) == 1
    assert results[0].firm_id == 5
    assert results[0].method == "exact_normalized"


def test_unmatched_and_empty_return_empty() -> None:
    index = _index((1, [("Goldman Sachs & Co. LLC", "exact_normalized")]))
    assert match_name(index, "Nonexistent Firm Inc") == []
    assert match_name(index, "") == []
    assert match_name(index, None) == []
    # A name that normalizes to nothing (pure noise tokens) is not indexed.
    noise_index = _index((9, [("The Company LLC", "exact_normalized")]))
    assert match_name(noise_index, "the and of") == []
