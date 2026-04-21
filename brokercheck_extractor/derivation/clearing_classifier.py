"""
Self-Clearing vs Fully Disclosed classifier.

Per the client spec, when the classifier cannot reach a confident verdict
the raw parsed clearing paragraph must be surfaced verbatim. The caller
gets the ClearingType AND the raw text — it's up to the UI layer to decide
how to display in the fallback case.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..schema.models import ClearingType, FirmProfile, IntroducingArrangement


# Well-known clearing/custody firms that when named in Introducing Arrangements
# indicate a Fully Disclosed relationship.
KNOWN_CLEARING_FIRMS = [
    "pershing",
    "fidelity national financial services",
    "fidelity brokerage services",
    "national financial services",
    "nfs llc",
    "rbc capital markets",
    "rbc clearing",
    "apex clearing",
    "raymond james",
    "southwest securities",
    "wedbush",
    "bny mellon",
    "bank of new york mellon",
    "interactive brokers",
    "jefferies",
    "goldman sachs",
    "jp morgan",
    "j.p. morgan",
    "morgan stanley",
    "bnp paribas",
    "cowen",
    "hilltop securities",
    "axos clearing",
    "cantor fitzgerald",
]

# Phrases in the Clearing Arrangements paragraph that indicate Self-Clearing
SELF_CLEARING_PATTERNS = [
    r"\bdoes\s+hold\s+or\s+maintain\s+funds\s+or\s+securities\b",
    r"\bprovides?\s+clearing\s+services?\s+for\s+other\s+broker",
    r"\bmaintains?\s+custody\s+of\b",
    r"\bhold\s+(?:customer\s+)?funds\s+or\s+securities\b",
]

# Phrases that indicate explicit non-self-clearing disclosures
NOT_SELF_CLEARING_PATTERNS = [
    r"\bdoes\s+not\s+hold\b",
    r"\bdoes\s+not\s+maintain\b",
    r"\bdoes\s+not\s+provide\s+clearing\b",
]


@dataclass
class ClearingVerdict:
    classification: ClearingType
    confidence: float         # 0.0 – 1.0
    evidence: str             # human-readable rationale
    raw_text: str             # the full clearing paragraph, always preserved


def classify_clearing(profile: FirmProfile) -> ClearingVerdict:
    """Decide Self-Clearing vs Fully Disclosed from the parsed FirmProfile."""
    ops = profile.operations
    raw = ops.clearing_raw_text or ops.clearing_statement or ""

    # --- Pass 1: is the firm self-clearing?
    hits = [p for p in SELF_CLEARING_PATTERNS if re.search(p, raw, re.IGNORECASE)]
    neg_hits = [p for p in NOT_SELF_CLEARING_PATTERNS if re.search(p, raw, re.IGNORECASE)]

    if hits and not neg_hits:
        return ClearingVerdict(
            classification=ClearingType.SELF_CLEARING,
            confidence=0.95,
            evidence=f"Self-clearing phrase matched: {hits[0]!r}",
            raw_text=raw,
        )

    # --- Pass 2: is there a named Introducing Arrangement pointing to a
    # known clearing firm?
    intro_hit = _find_introducing_clearing_firm(ops.introducing_arrangements)
    if intro_hit:
        return ClearingVerdict(
            classification=ClearingType.FULLY_DISCLOSED,
            confidence=0.9,
            evidence=f"Introducing arrangement names known clearing firm: {intro_hit!r}",
            raw_text=raw,
        )

    # --- Pass 3: explicit non-self-clearing language → Fully Disclosed
    if neg_hits:
        return ClearingVerdict(
            classification=ClearingType.FULLY_DISCLOSED,
            confidence=0.7,
            evidence=f"Explicit non-self-clearing language: {neg_hits[0]!r}",
            raw_text=raw,
        )

    # --- Fallback: unknown, caller must surface raw text
    return ClearingVerdict(
        classification=ClearingType.UNKNOWN,
        confidence=0.0,
        evidence="No confident match; surfacing raw clearing paragraph.",
        raw_text=raw,
    )


def _find_introducing_clearing_firm(arrangements: list[IntroducingArrangement]) -> Optional[str]:
    for arr in arrangements or []:
        name = (arr.business_name or "").lower()
        for known in KNOWN_CLEARING_FIRMS:
            if known in name:
                return arr.business_name
    return None


def apply_classification(profile: FirmProfile) -> ClearingVerdict:
    """Run the classifier and mutate `profile.operations.clearing_type` in place."""
    verdict = classify_clearing(profile)
    profile.operations.clearing_type = verdict.classification
    return verdict
