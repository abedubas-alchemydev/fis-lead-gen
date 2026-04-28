"""Clearing-classification helpers.

The top-level decision (formerly the regex-based
``determine_clearing_classification``) was deprecated by the 2026-04-29
rewrite documented in
``plans/be-gemini-clearing-classifier-2026-04-29.md`` and the audit
``reports/clearing-classification-audit-2026-04-28.md``. The canonical
classifier now lives in ``services/clearing_classifier.py`` and is a
single LLM-based decision over both the FINRA ``firm_operations_text``
and the FOCUS report excerpt.

This module retains:
  - The thin sync deprecation stub ``determine_clearing_classification``
    (returns "needs_review" so legacy sync callers do not crash; a real
    label will be assigned on the next pipeline pass).
  - Helper functions that are still useful elsewhere:
    ``classify_self_clearing``, ``classify_introducing``,
    ``extract_clearing_partner_from_operations``,
    ``classify_niche_restricted``.
  - The orchestration entrypoint ``apply_classification_to_all`` that
    iterates broker_dealers, calls the LLM classifier, and persists
    the result with the review-queue threshold. Its signature is
    unchanged, so all existing call sites (pipeline, admin settings,
    initial_load script) continue to work.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.models.clearing_arrangement import ClearingArrangement
from app.services.clearing_classifier import (
    CANONICAL_VALUES,
    classify as classify_clearing,
)


logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Deprecated regex helpers (kept for downstream callers that still
# import them; the audit confirmed the top-level decision is broken).
# ──────────────────────────────────────────────────────────────

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
    """[Deprecated, retained] Detect the legacy 'no custody, no introducing' phrase pair.

    The regex semantics are inverted relative to Deshorn's canonical
    Self-Clearing definition. Use ``services/clearing_classifier.classify``
    instead. This helper is kept as a feature flag for downstream
    callers that want to inspect the FINRA wording directly.
    """
    if not firm_operations_text:
        return False
    text = firm_operations_text
    has_no_custody = any(pattern.search(text) for pattern in _SELF_CLEARING_PATTERNS)
    has_no_introducing = bool(_NOT_INTRODUCING_PATTERN.search(text))
    return has_no_custody and has_no_introducing


_INTRODUCING_PATTERN = re.compile(
    r"does\s+refer\s+or\s+introduce\s+customers\s+to\s+other\s+brokers",
    re.IGNORECASE,
)

_CLEARING_PARTNER_PATTERN = re.compile(
    r"(?:clear(?:s|ing)\s+(?:through|with|via|by))\s+([A-Z][A-Za-z\s&,.]+?)(?:\.|,|\s+and\s|\s+for\s|$)",
    re.IGNORECASE,
)


def classify_introducing(firm_operations_text: str | None) -> bool:
    """[Deprecated, retained] Detect the legacy 'does refer or introduce' phrase."""
    if not firm_operations_text:
        return False
    return bool(_INTRODUCING_PATTERN.search(firm_operations_text))


def extract_clearing_partner_from_operations(firm_operations_text: str | None) -> str | None:
    """Best-effort partner-name extraction from FINRA firm-operations text.

    Still useful as a fallback when neither the FOCUS PDF nor the LLM
    classifier surfaces a clearing partner. Independent of the
    deprecated top-level regex decision.
    """
    if not firm_operations_text:
        return None
    match = _CLEARING_PARTNER_PATTERN.search(firm_operations_text)
    if match:
        partner = match.group(1).strip().rstrip(",.")
        return partner if partner else None
    return None


_NICHE_TYPES = {
    "private placement",
    "private placement only",
    "private placements of securities",
    "investment advisory",
    "investment advisory services",
    "investment adviser",
}


def classify_niche_restricted(types_of_business: list | None) -> bool:
    """Return True if the firm's business types are exclusively niche/restricted.

    Independent of the clearing-classification rewrite -- this gate is
    based on the FINRA ``types_of_business`` array, not the operations
    text. Still used by the master-list "niche" badge.
    """
    if not types_of_business:
        return False
    normalized = {str(t).strip().lower() for t in types_of_business if str(t).strip()}
    if not normalized:
        return False
    return normalized.issubset(_NICHE_TYPES)


def determine_clearing_classification(firm_operations_text: str | None) -> str:
    """[DEPRECATED] Sync regex top-level decision.

    Replaced by ``services/clearing_classifier.classify`` (async, LLM-based,
    canonical four-value enum). The 2026-04-28 audit found this function's
    Self-Clearing gate semantically inverted and missing Omnibus detection.

    Returns ``"needs_review"`` so any sync caller still wired up (notably
    the broker-dealer single-firm refresh endpoint at
    ``api/v1/endpoints/broker_dealers.py:680``) marks the row for
    re-classification by the next pipeline pass instead of crashing.

    Do not call this from new code. The compile-time deprecation is
    surfaced via this docstring; runtime callers will see their rows
    flip to ``needs_review`` until the LLM classifier reruns.
    """
    # firm_operations_text intentionally unused -- the regex was inverted
    # and the LLM classifier reads the same text on the next pipeline pass.
    _ = firm_operations_text
    return "needs_review"


# ──────────────────────────────────────────────────────────────
# Orchestrator -- now LLM-backed via clearing_classifier.classify
# ──────────────────────────────────────────────────────────────

async def apply_classification_to_all(db: AsyncSession) -> int:
    """Classify every broker_dealer row using the LLM-based classifier.

    For each BD:
      1. Pull the FINRA ``firm_operations_text`` straight off the row.
      2. Pull the FOCUS report excerpt from the most recent
         ``ClearingArrangement.clearing_statement_text`` for that BD
         (cheap -- already populated by the FOCUS extraction pipeline).
      3. Call ``services.clearing_classifier.classify`` (Gemini default,
         OpenAI alt; sentinel on provider error).
      4. If confidence >= ``settings.clearing_classification_min_confidence``
         AND the value is one of the canonical labels (not ``unknown``),
         persist it. Otherwise persist ``"needs_review"`` so the row
         surfaces for manual review.
      5. Continue to maintain the niche-restricted flag (independent of
         clearing classification) and the partner-extraction fallback
         when ``current_clearing_partner`` is null.

    Returns the number of rows updated. Signature preserved so existing
    call sites (services/pipeline.py:167, api/v1/endpoints/settings.py,
    scripts/initial_load.py) keep working without modification.
    """
    broker_dealers = (
        await db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))
    ).scalars().all()

    # Fetch the most-recent clearing_statement_text per BD in one round-trip
    # so we don't issue N separate queries inside the per-firm loop.
    arrangements = (
        await db.execute(
            select(
                ClearingArrangement.bd_id,
                ClearingArrangement.clearing_statement_text,
                ClearingArrangement.filing_year,
                ClearingArrangement.id,
            ).order_by(
                ClearingArrangement.filing_year.desc().nullslast(),
                ClearingArrangement.id.desc(),
            )
        )
    ).all()
    latest_focus_text_by_bd: dict[int, str | None] = {}
    for bd_id, statement_text, _filing_year, _row_id in arrangements:
        if bd_id in latest_focus_text_by_bd:
            continue
        latest_focus_text_by_bd[bd_id] = statement_text

    updated = 0
    threshold = float(settings.clearing_classification_min_confidence)

    for bd in broker_dealers:
        changed = False

        focus_text = latest_focus_text_by_bd.get(bd.id)
        result = await classify_clearing(
            firm_operations_text=bd.firm_operations_text,
            focus_report_text=focus_text,
        )

        if (
            result.value in CANONICAL_VALUES
            and result.value != "unknown"
            and result.confidence >= threshold
        ):
            new_classification = result.value
        else:
            new_classification = "needs_review"
            if result.value not in CANONICAL_VALUES:
                logger.warning(
                    "Classifier returned non-canonical value '%s' for bd_id=%s; coercing to needs_review",
                    result.value,
                    bd.id,
                )

        if bd.clearing_classification != new_classification:
            bd.clearing_classification = new_classification
            changed = True

        new_niche = classify_niche_restricted(bd.types_of_business)
        if bd.is_niche_restricted != new_niche:
            bd.is_niche_restricted = new_niche
            changed = True

        # Partner-extraction fallback: when the LLM said fully_disclosed but
        # the FOCUS pipeline never populated a partner, try the regex helper
        # against the FINRA text. Independent of the top-level decision; just
        # a name-extractor.
        if (
            new_classification == "fully_disclosed"
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
