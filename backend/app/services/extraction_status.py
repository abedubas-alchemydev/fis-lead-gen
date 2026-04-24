"""Shared extraction_status vocabulary.

Both the clearing and financial pipelines tag each persisted row with an
``extraction_status`` string so low-confidence / provider-error / partial
results land in a review queue instead of silently succeeding. This module
exposes the allowed values as module-level constants so the service code and
tests can import from a single source of truth.

Clearing persists richer variants today (``missing_pdf``, ``pipeline_error``)
because it has a wider NULL contract on ``clearing_arrangements``. Financial
can only persist rows that already have ``net_capital`` and ``report_date``
(both NOT NULL), so its write path only uses ``STATUS_PARSED`` and
``STATUS_NEEDS_REVIEW`` today. The full set is surfaced here so either side
can grow into the other statuses without re-introducing a string literal.
"""

from __future__ import annotations

# Column default. Never written by application code today — the DB
# server_default stamps every insert that omits the column. Retained here so
# tests can import it instead of hard-coding the literal.
STATUS_PENDING = "pending"

# Successful extraction that passed the confidence threshold and produced the
# fields the consuming pipeline requires.
STATUS_PARSED = "parsed"

# Extraction landed but needs human attention: below the confidence threshold
# or missing fields the pipeline considers mandatory for the downstream
# surface (e.g. a clearing row with no partner when type != self_clearing).
STATUS_NEEDS_REVIEW = "needs_review"

# LLM provider rejected the request or returned an unusable payload.
STATUS_PROVIDER_ERROR = "provider_error"

# The filing has no resolvable X-17A-5 PDF on EDGAR.
STATUS_MISSING_PDF = "missing_pdf"

# Unexpected exception inside the extraction loop.
STATUS_PIPELINE_ERROR = "pipeline_error"

# Ordered for stable iteration in tests and assertions.
ALL_EXTRACTION_STATUSES: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_PARSED,
    STATUS_NEEDS_REVIEW,
    STATUS_PROVIDER_ERROR,
    STATUS_MISSING_PDF,
    STATUS_PIPELINE_ERROR,
)


def classify_financial_extraction_status(
    *,
    confidence_score: float | None,
    min_confidence: float,
    has_required_fields: bool = True,
) -> str:
    """Return the ``extraction_status`` value for a financial extraction row.

    The rule mirrors the clearing pipeline's inline classifier in
    ``services/llm_parser.py``: a row that clears the confidence threshold
    and carries every required field is ``parsed``; anything else is
    ``needs_review``. Callers that hit a provider error before obtaining any
    payload should use :data:`STATUS_PROVIDER_ERROR` directly — this helper
    is only for the post-extraction classification.

    Args:
        confidence_score: Value returned by the LLM. ``None`` is treated as
            below-threshold so a missing score can never silently succeed.
        min_confidence: Threshold from ``settings.financial_extraction_min_confidence``.
        has_required_fields: False when the extraction is missing a field
            the caller considers mandatory (e.g. net_capital on the
            financial side).
    """
    if not has_required_fields:
        return STATUS_NEEDS_REVIEW
    if confidence_score is None or confidence_score < min_confidence:
        return STATUS_NEEDS_REVIEW
    return STATUS_PARSED
