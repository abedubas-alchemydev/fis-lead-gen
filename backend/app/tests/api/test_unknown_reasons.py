"""Contract tests for the typed UnknownReason envelope.

The pipeline persists *why* an extraction couldn't produce a confident
value via ``extraction_status`` plus a free-text ``extraction_notes``
narrative. These tests pin down the mapping from that storage shape into
the seven categories the FE info-icon tooltip keys off, and verify the
Pydantic round-trip the API contract relies on.

No DB. The derive helpers operate on plain Python objects (the ORM models
expose attributes by name, which is all the helpers need), so each test
constructs a ``SimpleNamespace`` in place of a real ``ClearingArrangement``
/ ``FinancialMetric`` / ``ExecutiveContact`` row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.schemas.unknown_reason import UnknownReason
from app.services.extraction_status import (
    STATUS_MISSING_PDF,
    STATUS_NEEDS_REVIEW,
    STATUS_PARSED,
    STATUS_PENDING,
    STATUS_PIPELINE_ERROR,
    STATUS_PROVIDER_ERROR,
)
from app.services.unknown_reasons import (
    CLEARING_CLUSTER_FIELDS,
    FINANCIAL_CLUSTER_FIELDS,
    UnknownReasonResult,
    clearing_trigger_fields,
    derive_clearing_unknown_reason,
    derive_executive_contact_unknown_reason,
    derive_financial_unknown_reason,
    financial_trigger_fields,
    to_unknown_reason,
    with_trigger_fields,
)


def _arrangement(
    *,
    clearing_partner: str | None = None,
    extraction_status: str = STATUS_PENDING,
    extraction_notes: str | None = None,
    extraction_confidence: float | None = None,
    extracted_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        clearing_partner=clearing_partner,
        extraction_status=extraction_status,
        extraction_notes=extraction_notes,
        extraction_confidence=extraction_confidence,
        extracted_at=extracted_at,
    )


def _metric(*, extraction_status: str = STATUS_PARSED) -> SimpleNamespace:
    return SimpleNamespace(extraction_status=extraction_status)


# ── Clearing arrangement ────────────────────────────────────────────────


def test_clearing_partner_present_returns_none() -> None:
    """A row with a named partner must never carry an unknown_reason."""
    result = derive_clearing_unknown_reason(
        _arrangement(
            clearing_partner="Pershing LLC",
            extraction_status=STATUS_PARSED,
            extraction_confidence=0.95,
        )
    )

    assert result is None


def test_clearing_no_row_returns_not_yet_extracted() -> None:
    """``None`` arrangement => pipeline hasn't reached the firm yet."""
    result = derive_clearing_unknown_reason(None)

    assert result is not None
    assert result.category == "not_yet_extracted"
    assert result.note is None


def test_clearing_needs_review_with_exemption_notes_is_disclosed() -> None:
    """needs_review + Footnote-74-style narrative => firm_does_not_disclose."""
    notes = (
        "The firm explicitly states in its Exemption Report that it does not "
        "directly or indirectly receive, hold, or otherwise owe funds or "
        "securities for or to customers."
    )

    result = derive_clearing_unknown_reason(
        _arrangement(
            clearing_partner=None,
            extraction_status=STATUS_NEEDS_REVIEW,
            extraction_notes=notes,
            extraction_confidence=0.45,
            extracted_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
    )

    assert result is not None
    assert result.category == "firm_does_not_disclose"
    assert result.note == notes
    assert result.confidence == pytest.approx(0.45)
    assert result.extracted_at == datetime(2026, 4, 1, tzinfo=timezone.utc)


def test_clearing_needs_review_without_exemption_is_low_confidence() -> None:
    """needs_review + generic uncertain narrative => low_confidence."""
    notes = "LLM uncertain; multiple clearing relationships referenced inconclusively."

    result = derive_clearing_unknown_reason(
        _arrangement(
            clearing_partner=None,
            extraction_status=STATUS_NEEDS_REVIEW,
            extraction_notes=notes,
            extraction_confidence=0.30,
        )
    )

    assert result is not None
    assert result.category == "low_confidence_extraction"
    assert result.note == notes


def test_clearing_missing_pdf_status_maps_to_no_filing_available() -> None:
    result = derive_clearing_unknown_reason(
        _arrangement(clearing_partner=None, extraction_status=STATUS_MISSING_PDF)
    )

    assert result is not None
    assert result.category == "no_filing_available"


def test_clearing_provider_error_passes_through() -> None:
    result = derive_clearing_unknown_reason(
        _arrangement(
            clearing_partner=None,
            extraction_status=STATUS_PROVIDER_ERROR,
            extraction_notes="Gemini returned 429 after 3 retries.",
        )
    )

    assert result is not None
    assert result.category == "provider_error"
    assert result.note is not None and "Gemini" in result.note


def test_clearing_pipeline_error_maps_to_pdf_unparseable() -> None:
    result = derive_clearing_unknown_reason(
        _arrangement(clearing_partner=None, extraction_status=STATUS_PIPELINE_ERROR)
    )

    assert result is not None
    assert result.category == "pdf_unparseable"


def test_clearing_parsed_but_partner_null_means_data_not_present() -> None:
    """Source filing was parsed, but explicitly omits any clearing partner."""
    result = derive_clearing_unknown_reason(
        _arrangement(
            clearing_partner=None,
            extraction_status=STATUS_PARSED,
            extraction_confidence=0.92,
        )
    )

    assert result is not None
    assert result.category == "data_not_present"
    assert result.confidence == pytest.approx(0.92)


def test_clearing_pending_status_maps_to_not_yet_extracted() -> None:
    result = derive_clearing_unknown_reason(
        _arrangement(clearing_partner=None, extraction_status=STATUS_PENDING)
    )

    assert result is not None
    assert result.category == "not_yet_extracted"


# ── Financial metric ────────────────────────────────────────────────────


def test_financial_no_row_means_not_yet_extracted() -> None:
    """No financial_metric row => pipeline hasn't reached the firm."""
    result = derive_financial_unknown_reason(None)

    assert result is not None
    assert result.category == "not_yet_extracted"


def test_financial_parsed_returns_none() -> None:
    """A parsed financial row carries net_capital + report_date — no reason."""
    result = derive_financial_unknown_reason(_metric(extraction_status=STATUS_PARSED))

    assert result is None


def test_financial_needs_review_means_low_confidence() -> None:
    result = derive_financial_unknown_reason(_metric(extraction_status=STATUS_NEEDS_REVIEW))

    assert result is not None
    assert result.category == "low_confidence_extraction"


def test_financial_provider_error_passes_through() -> None:
    result = derive_financial_unknown_reason(_metric(extraction_status=STATUS_PROVIDER_ERROR))

    assert result is not None
    assert result.category == "provider_error"


# ── Executive contacts ──────────────────────────────────────────────────


def test_exec_contacts_with_rows_returns_none() -> None:
    result = derive_executive_contact_unknown_reason(
        [SimpleNamespace(name="Jane Doe", title="CEO")]
    )

    assert result is None


def test_exec_contacts_empty_list_means_not_yet_extracted() -> None:
    result = derive_executive_contact_unknown_reason([])

    assert result is not None
    assert result.category == "not_yet_extracted"


# ── DTO round-trip ──────────────────────────────────────────────────────


def test_to_unknown_reason_passes_through_none() -> None:
    """``None`` in => ``None`` out so callers can chain without a guard."""
    assert to_unknown_reason(None) is None


def test_unknown_reason_round_trip_serializes_all_fields() -> None:
    """Pydantic round-trip preserves category, note, extracted_at, confidence."""
    extracted_at = datetime(2026, 4, 30, 12, 34, 56, tzinfo=timezone.utc)
    result = UnknownReasonResult(
        category="firm_does_not_disclose",
        note="Footnote 74 exemption.",
        extracted_at=extracted_at,
        confidence=0.42,
    )

    schema = to_unknown_reason(result)
    assert schema is not None
    assert schema.category == "firm_does_not_disclose"
    assert schema.note == "Footnote 74 exemption."
    assert schema.extracted_at == extracted_at
    assert schema.confidence == pytest.approx(0.42)

    payload = schema.model_dump(mode="json")
    reparsed = UnknownReason.model_validate(payload)
    assert reparsed == schema


def test_unknown_reason_rejects_invalid_category() -> None:
    """The Literal must reject any future drift in category strings."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UnknownReason.model_validate({"category": "totally_made_up"})


# ── Cluster trigger fields ──────────────────────────────────────────────


def test_clearing_cluster_fields_contract() -> None:
    """The clearing cluster covers partner + type, in trigger-priority order."""
    assert CLEARING_CLUSTER_FIELDS == (
        "current_clearing_partner",
        "current_clearing_type",
    )


def test_financial_cluster_fields_contract() -> None:
    """The financial cluster covers all four FE financial-health tiles."""
    assert FINANCIAL_CLUSTER_FIELDS == (
        "latest_net_capital",
        "latest_excess_net_capital",
        "yoy_growth",
        "health_status",
    )


def test_clearing_trigger_fields_returns_partner_when_partner_null() -> None:
    item = SimpleNamespace(
        current_clearing_partner=None,
        current_clearing_type="introducing",
    )
    assert clearing_trigger_fields(item) == ("current_clearing_partner",)


def test_clearing_trigger_fields_returns_type_when_only_type_null() -> None:
    """Partner present but type missing must still surface a reason."""
    item = SimpleNamespace(
        current_clearing_partner="Pershing LLC",
        current_clearing_type=None,
    )
    assert clearing_trigger_fields(item) == ("current_clearing_type",)


def test_clearing_trigger_fields_lists_both_when_both_null() -> None:
    item = SimpleNamespace(
        current_clearing_partner=None,
        current_clearing_type=None,
    )
    assert clearing_trigger_fields(item) == (
        "current_clearing_partner",
        "current_clearing_type",
    )


def test_clearing_trigger_fields_returns_empty_when_cluster_populated() -> None:
    item = SimpleNamespace(
        current_clearing_partner="Pershing LLC",
        current_clearing_type="introducing",
    )
    assert clearing_trigger_fields(item) == ()


def test_financial_trigger_fields_lists_every_null_in_declared_order() -> None:
    item = SimpleNamespace(
        latest_net_capital=1000.0,
        latest_excess_net_capital=None,
        yoy_growth=None,
        health_status=None,
    )
    assert financial_trigger_fields(item) == (
        "latest_excess_net_capital",
        "yoy_growth",
        "health_status",
    )


def test_financial_trigger_fields_fires_on_health_status_only() -> None:
    """A firm with all numeric financials but a null health_status still
    needs an unknown_reason — the FE Financial Health column relies on it."""
    item = SimpleNamespace(
        latest_net_capital=1000.0,
        latest_excess_net_capital=500.0,
        yoy_growth=0.12,
        health_status=None,
    )
    assert financial_trigger_fields(item) == ("health_status",)


def test_financial_trigger_fields_fires_on_yoy_growth_only() -> None:
    """Regression: prod row id=18793 (Robert W. Baird) had only yoy_growth
    null; the BE was previously dropping the tooltip entirely."""
    item = SimpleNamespace(
        latest_net_capital=1000.0,
        latest_excess_net_capital=500.0,
        yoy_growth=None,
        health_status="healthy",
    )
    assert financial_trigger_fields(item) == ("yoy_growth",)


def test_financial_trigger_fields_returns_empty_when_cluster_populated() -> None:
    item = SimpleNamespace(
        latest_net_capital=1000.0,
        latest_excess_net_capital=500.0,
        yoy_growth=0.12,
        health_status="healthy",
    )
    assert financial_trigger_fields(item) == ()


# ── with_trigger_fields ─────────────────────────────────────────────────


def test_with_trigger_fields_returns_none_for_empty_fields() -> None:
    """Empty fields ⇒ cluster fully populated ⇒ no tooltip needed."""
    assert with_trigger_fields(None, ()) is None
    assert (
        with_trigger_fields(UnknownReasonResult(category="not_yet_extracted"), ()) is None
    )


def test_with_trigger_fields_prepends_marker_to_existing_note() -> None:
    base = UnknownReasonResult(
        category="low_confidence_extraction",
        note="LLM uncertain about the partner.",
        confidence=0.3,
    )
    annotated = with_trigger_fields(base, ("current_clearing_partner",))
    assert annotated is not None
    assert annotated.note == (
        "[Triggered by missing: current_clearing_partner] "
        "LLM uncertain about the partner."
    )
    assert annotated.category == base.category
    assert annotated.confidence == base.confidence


def test_with_trigger_fields_writes_marker_when_note_was_none() -> None:
    base = UnknownReasonResult(category="not_yet_extracted")
    annotated = with_trigger_fields(base, ("health_status",))
    assert annotated is not None
    assert annotated.note == "[Triggered by missing: health_status]"


def test_with_trigger_fields_joins_multiple_fields_with_comma() -> None:
    base = UnknownReasonResult(category="not_yet_extracted")
    annotated = with_trigger_fields(base, ("yoy_growth", "health_status"))
    assert annotated is not None
    assert annotated.note == "[Triggered by missing: yoy_growth, health_status]"


def test_with_trigger_fields_synthesizes_data_not_present_when_result_is_none() -> None:
    """Regression: when ``derive_*_unknown_reason`` returns ``None`` (parsed
    metric / arrangement) but the cluster still has missing fields, the FE
    must still get a tooltip naming the missing column. Previously the
    None-passthrough silently dropped the tooltip on partial-null rows."""
    annotated = with_trigger_fields(None, ("yoy_growth",))
    assert annotated is not None
    assert annotated.category == "data_not_present"
    assert annotated.note == "[Triggered by missing: yoy_growth]"
    assert annotated.extracted_at is None
    assert annotated.confidence is None
