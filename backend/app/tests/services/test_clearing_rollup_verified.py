"""Unit tests for the verified-aware clearing rollup helpers.

``pick_authoritative_arrangement`` and ``apply_clearing_rollup`` are pure
(no DB / no session needed to exercise the logic), so these construct
in-memory ORM objects and assert the selection + field-copy behaviour.
The load-bearing property: a verified row (FINRA-reconciled / validator-
confirmed) must win over a newer-but-unverified raw FOCUS extraction.
"""
from __future__ import annotations

from app.models.broker_dealer import BrokerDealer
from app.models.clearing_arrangement import ClearingArrangement
from app.services.broker_dealers import (
    apply_clearing_rollup,
    pick_authoritative_arrangement,
)


def _arr(**kw) -> ClearingArrangement:
    return ClearingArrangement(**kw)


class TestPickAuthoritativeArrangement:
    def test_empty_returns_none(self) -> None:
        assert pick_authoritative_arrangement([]) is None

    def test_single_row(self) -> None:
        a = _arr(id=1, bd_id=1, filing_year=2024, is_verified=False)
        assert pick_authoritative_arrangement([a]) is a

    def test_verified_preferred_over_newer_unverified(self) -> None:
        verified_old = _arr(id=1, bd_id=1, filing_year=2023, is_verified=True)
        unverified_new = _arr(id=2, bd_id=1, filing_year=2024, is_verified=False)
        assert (
            pick_authoritative_arrangement([unverified_new, verified_old])
            is verified_old
        )

    def test_most_recent_verified_among_verified(self) -> None:
        v1 = _arr(id=1, bd_id=1, filing_year=2022, is_verified=True)
        v2 = _arr(id=2, bd_id=1, filing_year=2024, is_verified=True)
        assert pick_authoritative_arrangement([v1, v2]) is v2

    def test_most_recent_when_none_verified(self) -> None:
        a = _arr(id=1, bd_id=1, filing_year=2022, is_verified=False)
        b = _arr(id=2, bd_id=1, filing_year=2024, is_verified=False)
        assert pick_authoritative_arrangement([a, b]) is b

    def test_null_filing_year_sorts_last_without_crashing(self) -> None:
        good = _arr(id=1, bd_id=1, filing_year=2024, is_verified=False)
        nully = _arr(id=2, bd_id=1, filing_year=None, is_verified=False)
        assert pick_authoritative_arrangement([nully, good]) is good


class TestApplyClearingRollup:
    def test_copies_fields_from_arrangement(self) -> None:
        bd = BrokerDealer(name="X")
        a = _arr(
            id=1,
            bd_id=1,
            filing_year=2024,
            is_verified=True,
            clearing_type="self_clearing",
            clearing_partner="Self-Clearing",
            is_competitor=False,
            source_filing_url="http://example/filing",
            extraction_confidence=0.9,
        )
        apply_clearing_rollup(bd, a)
        assert bd.current_clearing_type == "self_clearing"
        assert bd.current_clearing_partner == "Self-Clearing"
        assert bd.current_clearing_is_competitor is False
        assert bd.current_clearing_source_filing_url == "http://example/filing"
        assert bd.current_clearing_extraction_confidence == 0.9

    def test_none_clears_fields(self) -> None:
        bd = BrokerDealer(name="X")
        bd.current_clearing_type = "self_clearing"
        bd.current_clearing_partner = "Self-Clearing"
        apply_clearing_rollup(bd, None)
        assert bd.current_clearing_type is None
        assert bd.current_clearing_partner is None
        assert bd.current_clearing_is_competitor is False
        assert bd.current_clearing_extraction_confidence is None
