"""Deterministic guardrail for broker-dealer clearing classification.

Gemini reads the FOCUS X-17A-5 PDF (plus the injected regulatory signals)
and proposes a clearing label. This module is the *audit spine*: a pure,
DB-free function that checks that proposal against hard regulatory signals
and corrects it ONLY when a signal is conclusive, otherwise routing to a
review flag (the balanced "do what's best" policy chosen with the user).

The deterministic signals:
  * ``required_min_capital`` -- the SEC Rule 15c3-1 minimum-net-capital floor.
    A firm below $250,000 legally holds no customer funds or securities and so
    CANNOT carry/self-clear; a firm at $250,000+ is at the carrying tier.
  * clearing-agency membership (DTC/NSCC/OCC) -- a firm that self-clears or
    runs omnibus is a member of the depositories / clearing corporations.
    ``membership_checked`` distinguishes a *confirmed* non-member from a firm
    that was simply never evaluated, so we never act on absence of data.
  * FINRA Form BD Item 12 introducing partner -- the authoritative current
    clearing partner for an introducing (fully-disclosed) firm.

``validate_clearing`` is pure and unit-tested like
``finra_reconciler.decide_reconciliation``. ``load_clearing_signals`` is the
DB-backed assembler. See plans/plan-it-well-use-rustling-swan.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer
from app.models.clearing_agency_membership import ClearingAgencyMembership
from app.models.introducing_arrangement import IntroducingArrangement

# SEC Rule 15c3-1: a broker-dealer that carries customer accounts / holds
# customer funds or securities must maintain >= $250,000 minimum net capital.
# Below this floor a firm is non-carrying and cannot legitimately self-clear.
CARRYING_CAPITAL_FLOOR = 250_000

# Memberships whose presence CONCLUSIVELY indicates the firm carries/settles
# in-house: DTC (securities depository/custody) and OCC (options clearing).
# NSCC is deliberately EXCLUDED — NSCC membership alone is most often Fund/SERV
# (mutual-fund distribution), which a non-carrying distributor holds without
# self-clearing. A genuine equities self-clearer is also a DTC participant
# (CNS settlement requires the depository), so DTC/OCC already captures it
# without sweeping in the fund distributors.
_SELF_CLEARING_AGENCIES = frozenset({"DTC", "OCC"})

# Boilerplate that indicates the firm carries no customer accounts at all
# (M&A advisory, private placement, (k)(1)/(k)(2)(i) exempt).
_NO_CUSTOMER_ACCOUNTS_RE = re.compile(
    r"does\s+not\s+(?:carry|hold\s+or\s+maintain|hold|maintain)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ClearingSignals:
    """Deterministic regulatory signals for one broker-dealer."""

    required_min_capital: Optional[float] = None
    # Active clearing-agency memberships (e.g. {"DTC", "NSCC"}). An empty set
    # is only meaningful when ``membership_checked`` is True.
    memberships: frozenset[str] = field(default_factory=frozenset)
    # Whether the OCC/DTCC membership importer has evaluated this firm. When
    # False, ``memberships`` being empty means UNKNOWN, not "not a member".
    membership_checked: bool = False
    # Authoritative current introducing partner from FINRA Form BD Item 12,
    # if any. Presence => the firm introduces to this clearing broker.
    finra_introducing_partner: Optional[str] = None
    # Whether the firm's operations text indicates no customer accounts at all.
    no_customer_accounts: bool = False

    @property
    def has_self_clearing_membership(self) -> bool:
        return bool(self.memberships & _SELF_CLEARING_AGENCIES)

    @property
    def is_below_carrying_floor(self) -> bool:
        return (
            self.required_min_capital is not None
            and self.required_min_capital < CARRYING_CAPITAL_FLOOR
        )

    @property
    def is_at_or_above_carrying_floor(self) -> bool:
        return (
            self.required_min_capital is not None
            and self.required_min_capital >= CARRYING_CAPITAL_FLOOR
        )


@dataclass(frozen=True, slots=True)
class ClearingDecision:
    """Outcome of validation: the (possibly corrected) label + provenance.

    ``clearing_type`` is ALWAYS one of the five canonical enum values
    (fully_disclosed / self_clearing / omnibus / non_carrying / unknown) —
    review state is carried separately by ``needs_review`` (a status, not a
    type). ``corrected`` and ``needs_review`` are mutually exclusive.
    """

    clearing_type: str
    clearing_partner: Optional[str]
    # Validator produced a confident, verified label: the caller stamps
    # ``is_verified=True`` and treats the row as parsed.
    corrected: bool
    # Validator could not conclusively resolve a contradiction: the caller sets
    # the row's ``extraction_status`` to ``needs_review``.
    needs_review: bool
    action: str  # "pass" | "demote" | "promote" | "consistency" | "review"
    rationale: str


def validate_clearing(
    *,
    clearing_type: str,
    clearing_partner: Optional[str],
    confidence: float,
    signals: ClearingSignals,
) -> ClearingDecision:
    """Validate (and minimally correct) Gemini's clearing proposal against the
    deterministic signals. Pure + DB-free so it is unit-testable.

    Policy: correct ONLY on a conclusive signal, otherwise flag
    ``needs_review``. Order matters — a confirmed membership promotion wins
    over a capital-based consistency demotion for the same row.
    """
    ct = clearing_type

    # 1) HARD DEMOTION — self_clearing contradicted by the 15c3-1 capital floor.
    #    A firm below $250k that is NOT a confirmed clearing-agency member holds
    #    no customer assets and cannot self-clear. Capital must be PRESENT
    #    (``is_below_carrying_floor`` is False when NULL) so we never demote on
    #    missing data.
    if (
        ct == "self_clearing"
        and signals.is_below_carrying_floor
        and not signals.has_self_clearing_membership
    ):
        if signals.finra_introducing_partner:
            return ClearingDecision(
                clearing_type="fully_disclosed",
                clearing_partner=signals.finra_introducing_partner,
                corrected=True,
                needs_review=False,
                action="demote",
                rationale=(
                    f"self_clearing contradicted: required_min_capital < "
                    f"${CARRYING_CAPITAL_FLOOR:,} (15c3-1 non-carrying) and FINRA Form "
                    f"BD names introducing partner '{signals.finra_introducing_partner}'."
                ),
            )
        if signals.no_customer_accounts:
            return ClearingDecision(
                clearing_type="non_carrying",
                clearing_partner=None,
                corrected=True,
                needs_review=False,
                action="demote",
                rationale=(
                    f"self_clearing contradicted: below the ${CARRYING_CAPITAL_FLOOR:,} "
                    "carrying floor with no customer accounts -> non_carrying."
                ),
            )
        return ClearingDecision(
            clearing_type="unknown",
            clearing_partner=clearing_partner,
            corrected=False,
            needs_review=True,
            action="review",
            rationale=(
                f"self_clearing below the ${CARRYING_CAPITAL_FLOOR:,} carrying floor with "
                "no confirming membership and no FINRA partner -> needs_review."
            ),
        )

    # 2) MEMBERSHIP OVERRIDE — a confirmed DTC/OCC clearing member carries and
    #    settles in-house BY DEFINITION, so it cannot be fully_disclosed /
    #    non_carrying / unknown no matter what the filing (or a FINRA Form BD
    #    introducing arrangement) says. Membership is the authoritative carrying
    #    signal -> self_clearing; an already self_clearing/omnibus label is left
    #    untouched. This is the durable form of the JPM/Goldman/Citi
    #    reconciliation (a Form BD partner must not demote a confirmed carrier).
    if signals.has_self_clearing_membership and ct not in ("self_clearing", "omnibus"):
        return ClearingDecision(
            clearing_type="self_clearing",
            clearing_partner=None,
            corrected=True,
            needs_review=False,
            action="promote",
            rationale=(
                "confirmed "
                f"{', '.join(sorted(signals.memberships & _SELF_CLEARING_AGENCIES))} "
                "clearing-agency membership -> the firm carries/settles in-house; "
                "membership overrides the proposed non-carrying label."
            ),
        )

    # 3) CONSISTENCY — non_carrying contradicted by carrying-tier capital (a
    #    confirmed member would already have been promoted in rule 2). Not
    #    conclusive which way -> needs_review.
    if ct == "non_carrying" and signals.is_at_or_above_carrying_floor:
        return ClearingDecision(
            clearing_type="unknown",
            clearing_partner=clearing_partner,
            corrected=False,
            needs_review=True,
            action="consistency",
            rationale=(
                "non_carrying contradicted by carrying-tier capital or clearing-agency "
                "membership -> needs_review."
            ),
        )

    # 4) PASS-THROUGH — no conclusive contradiction; keep Gemini's label and let
    #    the normal confidence gate decide the row's status.
    return ClearingDecision(
        clearing_type=ct,
        clearing_partner=clearing_partner,
        corrected=False,
        needs_review=False,
        action="pass",
        rationale="No deterministic contradiction; Gemini label kept.",
    )


def indicates_no_customer_accounts(firm_operations_text: Optional[str]) -> bool:
    """True when the FINRA operations text reads like a non-carrying firm."""
    if not firm_operations_text:
        return False
    return bool(_NO_CUSTOMER_ACCOUNTS_RE.search(firm_operations_text))


def format_signals_for_prompt(signals: ClearingSignals) -> str:
    """Render the signals as a prompt block so Gemini reasons over the PDF AND
    the hard regulatory priors in a single call (the 'use Gemini as much as
    possible' goal). Kept human-readable on purpose."""
    lines = ["## Deterministic regulatory signals (treat as strong priors)"]
    if signals.required_min_capital is not None:
        if signals.required_min_capital >= CARRYING_CAPITAL_FLOOR:
            tier = (
                "CARRYING tier (>= $250,000): the firm may legitimately be "
                "self_clearing or omnibus."
            )
        else:
            tier = (
                "NON-CARRYING tier (< $250,000): under SEC Rule 15c3-1 the firm holds no "
                "customer funds/securities and CANNOT be self_clearing — it is "
                "fully_disclosed (if it introduces to a partner) or non_carrying (if it "
                "has no customer accounts)."
            )
        lines.append(
            f"- SEC 15c3-1 required minimum net capital: "
            f"${signals.required_min_capital:,.0f} -> {tier}"
        )
    else:
        lines.append("- SEC 15c3-1 required minimum net capital: unknown.")

    if signals.membership_checked:
        if signals.memberships:
            members = ", ".join(sorted(signals.memberships))
            if signals.has_self_clearing_membership:
                lines.append(
                    f"- Clearing-agency membership: ACTIVE member of {members} "
                    "(incl. DTC/OCC) -> strongly indicates self_clearing/omnibus "
                    "(the firm settles securities/options in-house)."
                )
            else:
                lines.append(
                    f"- Clearing-agency membership: ACTIVE member of {members} only. "
                    "NOTE: NSCC membership alone is typically Fund/SERV (mutual-fund "
                    "distribution), NOT securities self-clearing — do not infer "
                    "self_clearing from it without DTC/OCC."
                )
        else:
            lines.append(
                "- Clearing-agency membership: evaluated, NOT a member -> the firm does "
                "not self-clear through the depositories."
            )
    else:
        lines.append(
            "- Clearing-agency membership: not checked (unknown — do NOT infer from "
            "absence)."
        )

    if signals.finra_introducing_partner:
        lines.append(
            f"- FINRA Form BD Item 12 introducing partner: "
            f"'{signals.finra_introducing_partner}' -> the firm introduces customers to "
            "this clearing broker (fully_disclosed)."
        )
    else:
        lines.append("- FINRA Form BD Item 12 introducing partner: none on file.")
    return "\n".join(lines)


async def load_clearing_signals(db: AsyncSession, bd: BrokerDealer) -> ClearingSignals:
    """Assemble the deterministic signals for one BD from the DB + its row."""
    agencies = (
        await db.execute(
            select(ClearingAgencyMembership.agency).where(
                ClearingAgencyMembership.broker_dealer_id == bd.id,
                ClearingAgencyMembership.status == "active",
            )
        )
    ).scalars().all()
    memberships = frozenset(a for a in agencies if a)

    finra_partner = (
        await db.execute(
            select(IntroducingArrangement.business_name)
            .where(
                IntroducingArrangement.bd_id == bd.id,
                IntroducingArrangement.business_name.isnot(None),
            )
            .order_by(IntroducingArrangement.effective_date.desc().nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()

    return ClearingSignals(
        required_min_capital=(
            float(bd.required_min_capital)
            if bd.required_min_capital is not None
            else None
        ),
        memberships=memberships,
        membership_checked=bd.clearing_membership_checked_at is not None,
        finra_introducing_partner=finra_partner,
        no_customer_accounts=indicates_no_customer_accounts(bd.firm_operations_text),
    )


__all__ = [
    "CARRYING_CAPITAL_FLOOR",
    "ClearingDecision",
    "ClearingSignals",
    "format_signals_for_prompt",
    "indicates_no_customer_accounts",
    "load_clearing_signals",
    "validate_clearing",
]
