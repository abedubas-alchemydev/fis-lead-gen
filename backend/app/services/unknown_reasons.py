"""Derive a typed ``UnknownReason`` for fields that came back NULL.

The master list shows ``Unknown`` for clearing partner / financial metrics
/ executive contact whenever the extraction pipeline couldn't produce a
confident value. The pipeline already records *why* (``extraction_status``
plus a free-text ``extraction_notes`` narrative); this module maps that
storage shape into the seven typed categories the FE keys off:

  - ``firm_does_not_disclose``    — fully_disclosed exemption, no partner named
  - ``no_filing_available``       — no X-17A-5 PDF found for the firm
  - ``low_confidence_extraction`` — LLM confidence below threshold
  - ``pdf_unparseable``           — pipeline error processing the PDF
  - ``provider_error``            — Gemini/OpenAI returned an unusable payload
  - ``not_yet_extracted``         — pipeline hasn't reached this firm yet
  - ``data_not_present``          — source was parsed but explicitly omits the field

Review-queue semantics are preserved: low-confidence / missing-partner /
provider-error rows still land in the failures panel via
``BrokerDealerRepository.list_recent_clearing_failures`` — this helper only
*classifies* the reason for API consumers; it never mutates state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models.clearing_arrangement import ClearingArrangement
from app.models.executive_contact import ExecutiveContact
from app.models.financial_metric import FinancialMetric
from app.schemas.unknown_reason import UnknownReason
from app.services.extraction_status import (
    STATUS_MISSING_PDF,
    STATUS_NEEDS_REVIEW,
    STATUS_PARSED,
    STATUS_PENDING,
    STATUS_PIPELINE_ERROR,
    STATUS_PROVIDER_ERROR,
)

UnknownReasonCategory = Literal[
    "firm_does_not_disclose",
    "no_filing_available",
    "low_confidence_extraction",
    "pdf_unparseable",
    "provider_error",
    "not_yet_extracted",
    "data_not_present",
]


@dataclass(frozen=True)
class UnknownReasonResult:
    """Plain-data view of an UnknownReason.

    Kept separate from the Pydantic schema so service code can construct it
    without importing from ``app.schemas`` (which would create an import
    cycle on the LLM-extraction side that already imports this module). The
    endpoint layer maps it to ``app.schemas.unknown_reason.UnknownReason``.
    """

    category: UnknownReasonCategory
    note: str | None = None
    extracted_at: datetime | None = None
    confidence: float | None = None


# Footnote-74-style language the LLM emits when the firm explicitly disclaims
# holding customer funds/securities under the SEC's fully-disclosed exemption.
# The match is intentionally narrow — only fires when the text uses the
# exemption-flavored verbiage, not on every needs_review row that mentions
# the word "fully".
_EXEMPTION_PATTERNS = (
    re.compile(r"does not\s+(?:directly|indirectly)?\s*receive[, ]+hold", re.IGNORECASE),
    re.compile(r"exemption report", re.IGNORECASE),
    re.compile(r"fully[-_ ]disclosed[\w ,]*exempt", re.IGNORECASE),
    re.compile(r"footnote\s*74", re.IGNORECASE),
    re.compile(r"\(k\)\s*\(2\)\s*\(ii\)", re.IGNORECASE),
)


def _looks_like_disclosed_exemption(notes: str | None) -> bool:
    if not notes:
        return False
    return any(pattern.search(notes) for pattern in _EXEMPTION_PATTERNS)


def _maybe_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def derive_clearing_unknown_reason(
    arrangement: ClearingArrangement | None,
) -> UnknownReasonResult | None:
    """Return the unknown_reason for a clearing arrangement, or None.

    A clearing row carries a typed reason whenever ``clearing_partner`` is
    NULL — the moment the column has a value, the cell renders normally and
    the FE skips the tooltip entirely. That keeps the contract simple:
    *value present ⇒ no reason; value missing ⇒ reason explains why.*
    """
    if arrangement is None:
        return UnknownReasonResult(category="not_yet_extracted")

    if arrangement.clearing_partner:
        return None

    status = arrangement.extraction_status or STATUS_PENDING
    notes = arrangement.extraction_notes
    confidence = _maybe_float(arrangement.extraction_confidence)
    extracted_at = arrangement.extracted_at

    if status == STATUS_PENDING:
        return UnknownReasonResult(
            category="not_yet_extracted",
            note=notes,
            extracted_at=extracted_at,
            confidence=confidence,
        )
    if status == STATUS_MISSING_PDF:
        return UnknownReasonResult(
            category="no_filing_available",
            note=notes,
            extracted_at=extracted_at,
            confidence=confidence,
        )
    if status == STATUS_PROVIDER_ERROR:
        return UnknownReasonResult(
            category="provider_error",
            note=notes,
            extracted_at=extracted_at,
            confidence=confidence,
        )
    if status == STATUS_PIPELINE_ERROR:
        return UnknownReasonResult(
            category="pdf_unparseable",
            note=notes,
            extracted_at=extracted_at,
            confidence=confidence,
        )
    if status == STATUS_NEEDS_REVIEW:
        category: UnknownReasonCategory = (
            "firm_does_not_disclose"
            if _looks_like_disclosed_exemption(notes)
            else "low_confidence_extraction"
        )
        return UnknownReasonResult(
            category=category,
            note=notes,
            extracted_at=extracted_at,
            confidence=confidence,
        )
    if status == STATUS_PARSED:
        # Row was parsed, the source said nothing about a clearing partner.
        return UnknownReasonResult(
            category="data_not_present",
            note=notes,
            extracted_at=extracted_at,
            confidence=confidence,
        )

    # Unknown future status string — surface as not_yet_extracted so the FE
    # never renders a stale or misleading category.
    return UnknownReasonResult(
        category="not_yet_extracted",
        note=notes,
        extracted_at=extracted_at,
        confidence=confidence,
    )


def derive_financial_unknown_reason(
    metric: FinancialMetric | None,
) -> UnknownReasonResult | None:
    """Return the unknown_reason for the rolled-up financial summary.

    ``FinancialMetric`` only carries ``extraction_status`` (no notes, no
    confidence) because both ``net_capital`` and ``report_date`` are NOT NULL
    — a row exists ⇒ the extraction landed those fields. The reason is
    therefore mostly about whether a row exists at all.
    """
    if metric is None:
        return UnknownReasonResult(category="not_yet_extracted")

    status = metric.extraction_status or STATUS_PENDING

    if status == STATUS_PARSED:
        return None
    if status == STATUS_PROVIDER_ERROR:
        return UnknownReasonResult(category="provider_error")
    if status == STATUS_PIPELINE_ERROR:
        return UnknownReasonResult(category="pdf_unparseable")
    if status == STATUS_MISSING_PDF:
        return UnknownReasonResult(category="no_filing_available")
    if status == STATUS_NEEDS_REVIEW:
        return UnknownReasonResult(category="low_confidence_extraction")
    return UnknownReasonResult(category="not_yet_extracted")


def derive_executive_contact_unknown_reason(
    contacts: list[ExecutiveContact],
) -> UnknownReasonResult | None:
    """Return the unknown_reason when no executive contacts have been found.

    ``executive_contacts`` has no ``extraction_status`` column — discovery is
    Apollo/Hunter/Snov-driven and either lands a row or doesn't. Any row in
    the list ⇒ no unknown_reason. Empty list ⇒ ``not_yet_extracted`` so the
    FE can render an info tooltip explaining why the cell is blank.
    """
    if contacts:
        return None
    return UnknownReasonResult(category="not_yet_extracted")


def to_unknown_reason(result: UnknownReasonResult | None) -> UnknownReason | None:
    """Map the service-layer dataclass to the response DTO.

    Pass-through on ``None`` so callers can chain this through a derive_*
    helper without a manual guard. Used by the list repository and the
    profile endpoint to convert ``UnknownReasonResult`` into the
    ``UnknownReason`` Pydantic model returned to the FE.
    """
    if result is None:
        return None
    return UnknownReason(
        category=result.category,
        note=result.note,
        extracted_at=result.extracted_at,
        confidence=result.confidence,
    )
