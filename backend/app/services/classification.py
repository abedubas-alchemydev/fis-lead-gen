"""Revision 1 Logic Gates: Self-Clearing, Introducing, and Business-Type classification.

These logic gates apply automated classification to broker-dealer records based
on the firm operations text and types of business data from FINRA (Stream A).
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer


# ──────────────────────────────────────────────────────────────
# 1.2.1  Self-Clearing Logic Gate
# ──────────────────────────────────────────────────────────────
# IF the record states:
#   "This firm does not hold or maintain funds or securities or provide clearing services"
#   AND "This firm does not refer or introduce customers"
# THEN label as "true_self_clearing" (high-value target for outsourcing services).

_SELF_CLEARING_PATTERNS = [
    re.compile(
        r"does\s+not\s+hold\s+or\s+maintain\s+funds\s+or\s+securities",
        re.IGNORECASE,
    ),
    re.compile(
        r"does\s+not\s+(?:provide|offer)\s+clearing\s+services",
        re.IGNORECASE,
    ),
]

_NOT_INTRODUCING_PATTERN = re.compile(
    r"does\s+not\s+refer\s+or\s+introduce\s+customers",
    re.IGNORECASE,
)


def classify_self_clearing(firm_operations_text: str | None) -> bool:
    """Return True if the firm qualifies as True Self-Clearing per Revision 1.2.1."""
    if not firm_operations_text:
        return False
    text = firm_operations_text
    has_no_custody = any(pattern.search(text) for pattern in _SELF_CLEARING_PATTERNS)
    has_no_introducing = bool(_NOT_INTRODUCING_PATTERN.search(text))
    return has_no_custody and has_no_introducing


# ──────────────────────────────────────────────────────────────
# 1.2.2  Introducing Logic Gate
# ──────────────────────────────────────────────────────────────
# IF the record states:
#   "This firm does refer or introduce customers to other brokers and dealers"
# THEN label as "introducing" and pull the clearing partner from the associated table.

_INTRODUCING_PATTERN = re.compile(
    r"does\s+refer\s+or\s+introduce\s+customers\s+to\s+other\s+brokers",
    re.IGNORECASE,
)

# Pattern to extract clearing partner names from the operations text.
# Looks for patterns like "clearing through Apex" or "Pershing LLC" after
# the introducing declaration.
_CLEARING_PARTNER_PATTERN = re.compile(
    r"(?:clear(?:s|ing)\s+(?:through|with|via|by))\s+([A-Z][A-Za-z\s&,.]+?)(?:\.|,|\s+and\s|\s+for\s|$)",
    re.IGNORECASE,
)


def classify_introducing(firm_operations_text: str | None) -> bool:
    """Return True if the firm qualifies as Introducing per Revision 1.2.2."""
    if not firm_operations_text:
        return False
    return bool(_INTRODUCING_PATTERN.search(firm_operations_text))


def extract_clearing_partner_from_operations(firm_operations_text: str | None) -> str | None:
    """Try to extract the clearing partner name from the firm operations text."""
    if not firm_operations_text:
        return None
    match = _CLEARING_PARTNER_PATTERN.search(firm_operations_text)
    if match:
        partner = match.group(1).strip().rstrip(",.")
        return partner if partner else None
    return None


# ──────────────────────────────────────────────────────────────
# 1.2.3  Business-Type Flagging System
# ──────────────────────────────────────────────────────────────
# If sole types are "Private Placement Only" or "Investment Advisory",
# apply a Niche/Restricted flag so users can skip or qualify instantly.

_NICHE_TYPES = {
    "private placement",
    "private placement only",
    "private placements of securities",
    "investment advisory",
    "investment advisory services",
    "investment adviser",
}


def classify_niche_restricted(types_of_business: list[str] | None) -> bool:
    """Return True if the firm's business types are exclusively niche/restricted."""
    if not types_of_business:
        return False
    normalized = {t.strip().lower() for t in types_of_business if t.strip()}
    if not normalized:
        return False
    return normalized.issubset(_NICHE_TYPES)


# ──────────────────────────────────────────────────────────────
# Combined classification: apply all three gates to a broker-dealer
# ──────────────────────────────────────────────────────────────

def determine_clearing_classification(firm_operations_text: str | None) -> str:
    """Determine the clearing classification for a broker-dealer.

    Returns one of: "true_self_clearing", "introducing", "unknown".
    """
    if classify_self_clearing(firm_operations_text):
        return "true_self_clearing"
    if classify_introducing(firm_operations_text):
        return "introducing"
    return "unknown"


async def apply_classification_to_all(db: AsyncSession) -> int:
    """Apply all three classification gates to every broker-dealer in the database.

    Returns the number of records updated.
    """
    broker_dealers = (
        await db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))
    ).scalars().all()

    updated = 0
    for bd in broker_dealers:
        changed = False

        # Clearing classification
        new_classification = determine_clearing_classification(bd.firm_operations_text)
        if bd.clearing_classification != new_classification:
            bd.clearing_classification = new_classification
            changed = True

        # Niche/Restricted flag
        new_niche = classify_niche_restricted(bd.types_of_business)
        if bd.is_niche_restricted != new_niche:
            bd.is_niche_restricted = new_niche
            changed = True

        # If introducing and we can extract a clearing partner from operations text,
        # use it as a fallback when the PDF pipeline hasn't run yet.
        if (
            new_classification == "introducing"
            and bd.current_clearing_partner is None
            and bd.firm_operations_text
        ):
            partner = extract_clearing_partner_from_operations(bd.firm_operations_text)
            if partner:
                bd.current_clearing_partner = partner
                if bd.current_clearing_type is None:
                    bd.current_clearing_type = "fully_disclosed"
                changed = True

        if changed:
            updated += 1

    await db.flush()
    return updated
