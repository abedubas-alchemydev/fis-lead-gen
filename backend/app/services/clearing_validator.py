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

# Memberships whose presence positively indicates the firm clears/settles
# in-house (i.e. it is genuinely self-clearing / omnibus).
_SELF_CLEARING_AGENCIES = frozenset({"DTC", "NSCC", "OCC"})

# Boilerplate that indicates the firm carries no customer accounts at all
# (M&A advisory, private placement, (k)(1)/(k)(2)(i) exempt).
_NO_CUSTOMER_ACCOUNTS_RE = re.compile(
    r"does\s+not\s+(?:carry|hold\s+or\s+maintain|hold|maintain)",
    re.IGNORECASE,
)

# Audit / advisory firms that are NOT clearing brokers but whose names sit on
# the X-17A-5 (the independent auditor signs the audited report) and collide
# with registered broker-dealer affiliates — e.g. the auditor "Deloitte &
# Touche" vs. the registered BD "Deloitte Corporate Finance LLC". An
# introducing firm never clears through its auditor, so such a name as a
# *clearing partner* is spurious. Lookarounds (not ``\b``) so a token touching
# punctuation still matches; whole-token only so we don't clip real broker
# names. Covers the Big Four + the next-tier audit firms that register BD arms.
_AUDIT_FIRM_PARTNER_RE = re.compile(
    r"(?<!\w)(?:"
    r"deloitte|touche|ernst|kpmg|pricewaterhouse(?:coopers)?|pwc|"
    r"grant\s+thornton|marcum|mazars|baker\s+tilly|bdo|rsm|crowe"
    r")(?!\w)",
    re.IGNORECASE,
)


def looks_like_audit_firm(partner: Optional[str]) -> bool:
    """True when a *clearing partner* name is actually an audit/advisory firm.

    These never clear trades; their appearance as a clearing partner means the
    extractor latched onto the X-17A-5's independent auditor (or an advisory
    affiliate) instead of a real carrying broker.
    """
    if not partner:
        return False
    return bool(_AUDIT_FIRM_PARTNER_RE.search(partner))


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
    action: str  # "pass" | "demote" | "promote" | "consistency" | "partner_guard" | "review"
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

    # 2) PROMOTION — a confirmed self-clearer Gemini under-called. Only a
    #    positive membership (DTC/NSCC/OCC) is conclusive enough to promote;
    #    capital-only contradictions fall through to consistency review.
    if (
        ct in ("unknown", "non_carrying")
        and signals.has_self_clearing_membership
        and not signals.finra_introducing_partner
    ):
        return ClearingDecision(
            clearing_type="self_clearing",
            clearing_partner=None,
            corrected=True,
            needs_review=False,
            action="promote",
            rationale=(
                "promoted to self_clearing: active "
                f"{', '.join(sorted(signals.memberships & _SELF_CLEARING_AGENCIES))} "
                "membership and no FINRA introducing partner."
            ),
        )

    # 3) CONSISTENCY — non_carrying contradicted by carrying-tier capital or a
    #    clearing-agency membership we couldn't promote on (e.g. a FINRA partner
    #    is also present). Not conclusive which way -> needs_review.
    if ct == "non_carrying" and (
        signals.is_at_or_above_carrying_floor or signals.has_self_clearing_membership
    ):
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

    # 4) AUDITOR-AS-PARTNER GUARD — a fully_disclosed/omnibus label whose named
    #    clearing partner is actually an audit/advisory firm (e.g. the X-17A-5's
    #    independent auditor "Deloitte & Touche" normalized to the registered BD
    #    "Deloitte Corporate Finance LLC"). An introducing firm never clears
    #    through its auditor, so the partner is spurious; resolve the real label
    #    from the hard signals, strongest first, and only flag review when none
    #    of them speak.
    if ct in ("fully_disclosed", "omnibus") and looks_like_audit_firm(clearing_partner):
        if signals.has_self_clearing_membership:
            return ClearingDecision(
                clearing_type="self_clearing",
                clearing_partner=None,
                corrected=True,
                needs_review=False,
                action="partner_guard",
                rationale=(
                    f"clearing partner '{clearing_partner}' is an audit/advisory firm, "
                    "not a clearing broker (likely the X-17A-5 independent auditor); "
                    "active "
                    f"{', '.join(sorted(signals.memberships & _SELF_CLEARING_AGENCIES))} "
                    "membership confirms in-house clearing -> self_clearing."
                ),
            )
        if signals.finra_introducing_partner:
            return ClearingDecision(
                clearing_type="fully_disclosed",
                clearing_partner=signals.finra_introducing_partner,
                corrected=True,
                needs_review=False,
                action="partner_guard",
                rationale=(
                    f"clearing partner '{clearing_partner}' is an audit/advisory firm, "
                    "not a clearing broker; FINRA Form BD names the real introducing "
                    f"partner '{signals.finra_introducing_partner}' -> fully_disclosed."
                ),
            )
        if signals.is_at_or_above_carrying_floor:
            return ClearingDecision(
                clearing_type="self_clearing",
                clearing_partner=None,
                corrected=True,
                needs_review=False,
                action="partner_guard",
                rationale=(
                    f"clearing partner '{clearing_partner}' is an audit/advisory firm, "
                    "not a clearing broker (likely the X-17A-5 independent auditor); "
                    "carrying-tier capital and no real introducing partner -> "
                    "self_clearing."
                ),
            )
        return ClearingDecision(
            clearing_type="unknown",
            clearing_partner=None,
            corrected=False,
            needs_review=True,
            action="review",
            rationale=(
                f"clearing partner '{clearing_partner}' is an audit/advisory firm, not a "
                "clearing broker (likely the X-17A-5 independent auditor), and no carrying "
                "signal or FINRA partner resolves the real label -> needs_review."
            ),
        )

    # 5) PASS-THROUGH — no conclusive contradiction; keep Gemini's label and let
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
            lines.append(
                f"- Clearing-agency membership: ACTIVE member of "
                f"{', '.join(sorted(signals.memberships))} -> strongly indicates "
                "self_clearing/omnibus (the firm settles in-house)."
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
    "looks_like_audit_firm",
    "validate_clearing",
]
