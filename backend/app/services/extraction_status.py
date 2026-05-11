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


# Upper bound for the total_assets / net_capital ratio on a sane
# broker-dealer balance sheet. Heavily leveraged dealers can hit ~200×;
# a 1000× ratio almost certainly means the extractor dropped an "in
# thousands" / "in millions" scale multiplier from the Computation of
# Net Capital section. Two production incidents (RBC ~33,000×,
# DriveWealth ~33,000×) both fell well above this threshold.
PLAUSIBLE_LEVERAGE_RATIO_MAX = 1000.0


def is_plausible_net_capital_scale(
    *,
    net_capital: float | None,
    total_assets: float | None,
) -> bool:
    """Cross-field sanity check: ``False`` when ``net_capital`` looks
    too small relative to ``total_assets``, suggesting the extractor
    dropped the scale multiplier on net_capital.

    Returns ``True`` (assume sane) when the determination cannot be
    made — i.e. either input is missing, zero, or negative. Callers
    that need to gate on those conditions should use the other checks
    in :func:`classify_financial_extraction_status` (``has_required_fields``).

    The threshold (1000×) is intentionally conservative: it sits well
    above the largest legitimate leverage seen on US broker-dealer
    balance sheets while still catching the two known production bug
    classes (RBC, DriveWealth — both ~33,000×).
    """
    if total_assets is None or net_capital is None:
        return True
    if net_capital <= 0 or total_assets <= 0:
        return True
    return (total_assets / net_capital) <= PLAUSIBLE_LEVERAGE_RATIO_MAX


def classify_financial_extraction_status(
    *,
    confidence_score: float | None,
    min_confidence: float,
    has_required_fields: bool = True,
    is_plausible_leverage: bool = True,
) -> str:
    """Return the ``extraction_status`` value for a financial extraction row.

    The rule mirrors the clearing pipeline's inline classifier in
    ``services/llm_parser.py``: a row that clears the confidence threshold,
    carries every required field, AND passes the leverage-plausibility
    cross-check is ``parsed``; anything else is ``needs_review``. Callers
    that hit a provider error before obtaining any payload should use
    :data:`STATUS_PROVIDER_ERROR` directly — this helper is only for the
    post-extraction classification.

    Args:
        confidence_score: Value returned by the LLM. ``None`` is treated as
            below-threshold so a missing score can never silently succeed.
        min_confidence: Threshold from ``settings.financial_extraction_min_confidence``.
        has_required_fields: False when the extraction is missing a field
            the caller considers mandatory (e.g. net_capital on the
            financial side).
        is_plausible_leverage: False when the cross-field sanity check
            (see :func:`is_plausible_net_capital_scale`) flags the
            net_capital/total_assets ratio as impossible. Defaults to
            True so existing callers that don't yet compute the check
            (or rows that lack total_assets) behave unchanged.
    """
    if not has_required_fields:
        return STATUS_NEEDS_REVIEW
    if not is_plausible_leverage:
        return STATUS_NEEDS_REVIEW
    if confidence_score is None or confidence_score < min_confidence:
        return STATUS_NEEDS_REVIEW
    return STATUS_PARSED
