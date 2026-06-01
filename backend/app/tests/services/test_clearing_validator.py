"""Unit tests for the deterministic clearing validator (the audit spine).

``validate_clearing`` is pure + DB-free, so these are plain synchronous
unit tests — no DB, no HTTP, no async. They pin the demote / promote /
consistency / pass behaviour and the missing-data safety rules.
"""
from __future__ import annotations

from app.services.clearing_validator import (
    CARRYING_CAPITAL_FLOOR,
    ClearingSignals,
    format_signals_for_prompt,
    indicates_no_customer_accounts,
    validate_clearing,
)


def _validate(clearing_type, signals, *, partner=None, confidence=0.9):
    return validate_clearing(
        clearing_type=clearing_type,
        clearing_partner=partner,
        confidence=confidence,
        signals=signals,
    )


# ───────────────────────────── DEMOTION ─────────────────────────────


class TestDemoteSelfClearing:
    def test_below_floor_no_customer_accounts_demotes_to_non_carrying(self) -> None:
        """The headline bug: a $5k-floor M&A boutique Gemini called
        self_clearing must become non_carrying."""
        signals = ClearingSignals(
            required_min_capital=5000,
            membership_checked=True,
            no_customer_accounts=True,
        )
        decision = _validate("self_clearing", signals)
        assert decision.clearing_type == "non_carrying"
        assert decision.clearing_partner is None
        assert decision.corrected is True
        assert decision.needs_review is False
        assert decision.action == "demote"

    def test_below_floor_with_finra_partner_demotes_to_fully_disclosed(self) -> None:
        signals = ClearingSignals(
            required_min_capital=5000,
            finra_introducing_partner="Pershing LLC",
        )
        decision = _validate("self_clearing", signals)
        assert decision.clearing_type == "fully_disclosed"
        assert decision.clearing_partner == "Pershing LLC"
        assert decision.corrected is True
        assert decision.action == "demote"

    def test_below_floor_no_other_signal_routes_to_review(self) -> None:
        signals = ClearingSignals(required_min_capital=5000)
        decision = _validate("self_clearing", signals)
        assert decision.clearing_type == "unknown"
        assert decision.corrected is False
        assert decision.needs_review is True
        assert decision.action == "review"

    def test_below_floor_but_confirmed_member_is_not_demoted(self) -> None:
        """A confirmed DTC/NSCC member is trusted even with a low reported
        floor — membership wins over the capital heuristic."""
        signals = ClearingSignals(
            required_min_capital=5000,
            memberships=frozenset({"DTC", "NSCC"}),
            membership_checked=True,
        )
        decision = _validate("self_clearing", signals)
        assert decision.clearing_type == "self_clearing"
        assert decision.action == "pass"

    def test_high_capital_self_clearing_passes(self) -> None:
        signals = ClearingSignals(required_min_capital=6_000_000_000)
        decision = _validate("self_clearing", signals)
        assert decision.clearing_type == "self_clearing"
        assert decision.action == "pass"

    def test_null_capital_is_never_demoted(self) -> None:
        """Missing-data safety: a NULL minimum-capital firm cannot be demoted
        on the capital heuristic."""
        signals = ClearingSignals(required_min_capital=None)
        decision = _validate("self_clearing", signals)
        assert decision.clearing_type == "self_clearing"
        assert decision.action == "pass"


# ──────────────────────────── PROMOTION ─────────────────────────────


class TestPromote:
    def test_unknown_with_membership_promotes_to_self_clearing(self) -> None:
        signals = ClearingSignals(
            required_min_capital=2_600_000_000,
            memberships=frozenset({"DTC", "NSCC", "OCC"}),
            membership_checked=True,
        )
        decision = _validate("unknown", signals)
        assert decision.clearing_type == "self_clearing"
        assert decision.corrected is True
        assert decision.needs_review is False
        assert decision.action == "promote"

    def test_non_carrying_with_membership_promotes(self) -> None:
        signals = ClearingSignals(
            memberships=frozenset({"DTC"}),
            membership_checked=True,
        )
        decision = _validate("non_carrying", signals)
        assert decision.clearing_type == "self_clearing"
        assert decision.action == "promote"

    def test_no_promotion_when_membership_unchecked(self) -> None:
        """Never act on absence of data: unchecked membership must not promote
        even at carrying-tier capital."""
        signals = ClearingSignals(
            required_min_capital=500_000,
            membership_checked=False,
        )
        decision = _validate("unknown", signals)
        assert decision.clearing_type == "unknown"
        assert decision.action == "pass"

    def test_no_promotion_when_finra_partner_present(self) -> None:
        signals = ClearingSignals(
            memberships=frozenset({"DTC"}),
            membership_checked=True,
            finra_introducing_partner="Pershing LLC",
        )
        decision = _validate("non_carrying", signals)
        # Conflicting signals (member + introduces) -> review, not promote.
        assert decision.action == "consistency"
        assert decision.needs_review is True


# ─────────────────────────── CONSISTENCY ────────────────────────────


class TestConsistency:
    def test_non_carrying_with_carrying_capital_routes_to_review(self) -> None:
        signals = ClearingSignals(required_min_capital=1_000_000)
        decision = _validate("non_carrying", signals)
        assert decision.clearing_type == "unknown"
        assert decision.needs_review is True
        assert decision.action == "consistency"

    def test_non_carrying_below_floor_passes(self) -> None:
        signals = ClearingSignals(required_min_capital=5000, no_customer_accounts=True)
        decision = _validate("non_carrying", signals)
        assert decision.clearing_type == "non_carrying"
        assert decision.action == "pass"


# ───────────────────────────── PASS-THROUGH ─────────────────────────


class TestPassThrough:
    def test_fully_disclosed_with_partner_passes(self) -> None:
        signals = ClearingSignals(
            required_min_capital=5000, finra_introducing_partner="Pershing LLC"
        )
        decision = _validate("fully_disclosed", signals, partner="Pershing LLC")
        assert decision.clearing_type == "fully_disclosed"
        assert decision.clearing_partner == "Pershing LLC"
        assert decision.corrected is False
        assert decision.action == "pass"

    def test_unknown_with_no_signals_passes(self) -> None:
        decision = _validate("unknown", ClearingSignals())
        assert decision.clearing_type == "unknown"
        assert decision.action == "pass"


# ──────────────────────── Signals + helpers ─────────────────────────


class TestSignalProperties:
    def test_carrying_floor_constant(self) -> None:
        assert CARRYING_CAPITAL_FLOOR == 250_000

    def test_below_floor_property(self) -> None:
        assert ClearingSignals(required_min_capital=5000).is_below_carrying_floor
        assert not ClearingSignals(required_min_capital=250_000).is_below_carrying_floor
        assert not ClearingSignals(required_min_capital=None).is_below_carrying_floor

    def test_self_clearing_membership_property(self) -> None:
        assert ClearingSignals(
            memberships=frozenset({"NSCC"})
        ).has_self_clearing_membership
        assert not ClearingSignals(
            memberships=frozenset({"FICC-GOV"})
        ).has_self_clearing_membership

    def test_indicates_no_customer_accounts(self) -> None:
        assert indicates_no_customer_accounts(
            "The Company does not carry customer accounts."
        )
        assert indicates_no_customer_accounts(
            "This firm does not hold or maintain funds or securities."
        )
        assert not indicates_no_customer_accounts("The Company clears through Pershing.")
        assert not indicates_no_customer_accounts(None)

    def test_format_signals_for_prompt_mentions_tier_and_floor(self) -> None:
        text = format_signals_for_prompt(
            ClearingSignals(
                required_min_capital=5000,
                membership_checked=True,
                finra_introducing_partner="Pershing LLC",
            )
        )
        assert "NON-CARRYING tier" in text
        assert "Pershing LLC" in text
        assert "CANNOT be self_clearing" in text

    def test_format_signals_unchecked_membership_says_unknown(self) -> None:
        text = format_signals_for_prompt(ClearingSignals(membership_checked=False))
        assert "not checked" in text
