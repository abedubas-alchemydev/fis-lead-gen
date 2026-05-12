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

import calendar
from datetime import date

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

# Unexpected exception inside the extraction loop (catch-all for non-
# network failures: PDF unreadable, parse errors, unexpected shapes).
STATUS_PIPELINE_ERROR = "pipeline_error"

# Network/DNS/timeout failure before the extraction could read the
# source PDF. Distinguished from ``pipeline_error`` because these are
# transient and worth auto-retrying outside the normal cooldown.
STATUS_NETWORK_ERROR = "network_error"

# Ordered for stable iteration in tests and assertions.
ALL_EXTRACTION_STATUSES: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_PARSED,
    STATUS_NEEDS_REVIEW,
    STATUS_PROVIDER_ERROR,
    STATUS_MISSING_PDF,
    STATUS_PIPELINE_ERROR,
    STATUS_NETWORK_ERROR,
)

# Status values that mean "extraction did not complete because of a
# transient infrastructure problem, retry is worthwhile". The bulk
# gap-fill runner uses this set to bypass the 30-day cooldown — a DNS
# blip 12 days ago shouldn't gate a re-attempt for the next 18.
RETRYABLE_TRANSIENT_STATUSES: frozenset[str] = frozenset({STATUS_NETWORK_ERROR})


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


def is_plausible_report_date(report_date: date | None) -> bool:
    """``True`` when ``report_date`` looks like a legitimate fiscal
    period-end, ``False`` when it looks like a filing date.

    Heuristic: every real X-17A-5 (or amendment to a SOFC) reports the
    period as the last day of a month. Quarter-ends (03/31, 06/30, 09/30,
    12/31) cover ~97% of US broker-dealer fiscal years; non-Q
    month-ends (10/31, 01/31, etc.) cover the rest. Filing dates, by
    contrast, are mid-month and rarely land on a month-end by chance.

    Returns ``True`` (assume sane) when ``report_date`` is ``None`` —
    the caller's ``has_required_fields`` gate should reject NULL dates
    directly; this check is only meant to catch the wrong-date-extracted
    bug shape on otherwise-present rows.

    Background: issue #398. Gemini occasionally returns the filing date
    instead of the period-end (the two appear side-by-side on the EDGAR
    submission page). 17 such rows existed on staging before this
    validator went live, all carrying suspiciously small ``net_capital``
    and no ``total_assets`` — stub-filing artifacts.
    """
    if report_date is None:
        return True
    last_day = calendar.monthrange(report_date.year, report_date.month)[1]
    return report_date.day == last_day


def classify_financial_extraction_status(
    *,
    confidence_score: float | None,
    min_confidence: float,
    has_required_fields: bool = True,
    is_plausible_leverage: bool = True,
    is_plausible_date: bool = True,
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
        is_plausible_date: False when the report_date is not a
            month-end, suggesting the extractor returned a filing date
            instead of the fiscal period-end (issue #398). Defaults to
            True so existing callers behave unchanged.
    """
    if not has_required_fields:
        return STATUS_NEEDS_REVIEW
    if not is_plausible_leverage:
        return STATUS_NEEDS_REVIEW
    if not is_plausible_date:
        return STATUS_NEEDS_REVIEW
    if confidence_score is None or confidence_score < min_confidence:
        return STATUS_NEEDS_REVIEW
    return STATUS_PARSED


# Substrings (case-insensitive) inside an exception's str() that indicate a
# transient network-layer failure. Matches both the raw socket error name
# (``getaddrinfo``, ``gaierror``) and the human-readable phrases httpx /
# requests / urllib3 surface when DNS or connectivity fails. Errno 11001
# is the Windows winsock-host-not-found code; the literal "[Errno 11001]"
# is what the prior incident leaked into 182 staging rows.
_NETWORK_ERROR_FINGERPRINTS: tuple[str, ...] = (
    "getaddrinfo failed",
    "errno 11001",
    "name or service not known",
    "temporary failure in name resolution",
    "connection refused",
    "connection reset",
    "network is unreachable",
    "no route to host",
    "timed out",
    "timeout",
    "connection aborted",
    "connect call failed",
    "ssl: wrong_version_number",
)


def classify_pipeline_exception(exc: BaseException) -> tuple[str, str]:
    """Map an extraction-loop exception to an ``extraction_status`` and a
    user-safe note string.

    Returns a tuple of ``(status, sanitized_note)``:

    - ``(STATUS_NETWORK_ERROR, "Network failure prevented the FOCUS PDF
      fetch — pending retry.")`` when the exception text matches one of
      the transient network fingerprints above. These rows bypass the
      gap-fill cooldown so a single DNS blip doesn't park the BD for 30
      days.
    - ``(STATUS_PIPELINE_ERROR, "<exception class>: <truncated message>")``
      for any other exception. The raw text is still constrained (200
      chars) so a stack-trace style payload can't drown the row.

    Crucially, neither path leaks the raw Python exception string verbatim
    into ``extraction_notes`` the way the pre-PR error handler did. The
    UI tooltip rendered ``extraction_notes`` directly, so the prior
    behaviour ended up surfacing ``[Errno 11001] getaddrinfo failed`` to
    end users across 182 BDs (see #399).

    The exception itself is still logged in full via ``logger.exception``
    at the call site — this helper only governs what gets persisted.
    """
    message = str(exc).strip().lower()
    if any(needle in message for needle in _NETWORK_ERROR_FINGERPRINTS):
        return (
            STATUS_NETWORK_ERROR,
            "Network failure prevented the FOCUS PDF fetch — pending retry.",
        )
    cls = type(exc).__name__
    short = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if len(short) > 160:
        short = short[:157] + "..."
    note = f"Extraction failed ({cls}): {short}" if short else f"Extraction failed ({cls})"
    return STATUS_PIPELINE_ERROR, note
