"""Unit tests for the banks path of the shared contact gap-fill helpers +
the bank discovery-row builder.

The ``is_gap_row`` / ``apply_gap_fill`` merge helpers are provider-agnostic and
duck-typed (Protocol), so the exhaustive merge-branch coverage lives in
``test_advisor_gap_fill_contacts.py``. Here we assert the same helpers accept a
``BankContact`` row (the fourth contact table) and that ``_build_bank_row``
constructs a well-formed discovery row — source mapping, JSONB arrays, the
discovery attribution, the Apollo id, and NULL filing provenance.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.bank import BankContact
from app.services.contact_discovery.base import (
    DiscoveryResult,
    EmailHit,
    PhoneHit,
)
from app.services.contact_discovery.gap_fill_common import (
    apply_gap_fill,
    is_gap_row,
)
from app.services.contact_discovery.orchestrator import _build_bank_row


def _make_row(
    *,
    email: str | None = None,
    phone: str | None = None,
    linkedin_url: str | None = None,
    emails: list[dict] | None = None,
    phones: list[dict] | None = None,
    discovery_source: str | None = None,
    discovery_confidence: Decimal | None = None,
    apollo_person_id: str | None = None,
) -> BankContact:
    row = BankContact()
    row.bank_id = 1
    row.name = "Jane Doe"
    row.title = "President"
    row.email = email
    row.phone = phone
    row.linkedin_url = linkedin_url
    row.emails = emails
    row.phones = phones
    row.discovery_source = discovery_source
    row.discovery_confidence = discovery_confidence
    row.apollo_person_id = apollo_person_id
    row.source = "application_pdf"
    return row


def _make_result(
    *,
    provider: str = "apollo_match",
    confidence: float = 90.0,
    email: str | None = None,
    phone: str | None = None,
    linkedin_url: str | None = None,
    emails: list[EmailHit] | None = None,
    phones: list[PhoneHit] | None = None,
    apollo_person_id: str | None = None,
) -> DiscoveryResult:
    return DiscoveryResult(
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        confidence=confidence,
        provider=provider,
        raw={"_": provider},
        emails=emails or [],
        phones=phones or [],
        apollo_person_id=apollo_person_id,
    )


# ──────────────────────────── is_gap_row on BankContact ────────────────────────


def test_is_gap_row_false_when_all_channels_present() -> None:
    row = _make_row(
        email="jane@statebank.com",
        phone="+15551112222",
        linkedin_url="https://linkedin.com/in/jane",
    )
    assert is_gap_row(row) is False


def test_is_gap_row_true_when_pdf_row_has_no_channels() -> None:
    """A bare PDF-extracted row (name + title only) is always a gap — that is
    exactly what "Generate More Details" fills."""
    assert is_gap_row(_make_row()) is True


def test_is_gap_row_counts_jsonb_arrays_as_present() -> None:
    row = _make_row(
        emails=[{"value": "jane@statebank.com", "type": "work", "confidence": 90.0, "source": "pdl"}],
        phones=[{"value": "+15551112222", "type": "mobile", "confidence": 80.0, "source": "pdl"}],
        linkedin_url="https://linkedin.com/in/jane",
    )
    assert is_gap_row(row) is False


# ──────────────────────────── apply_gap_fill on BankContact ────────────────────


def test_apply_gap_fill_fills_null_channels_non_destructively() -> None:
    row = _make_row(email="jane@statebank.com")  # missing phone + linkedin
    merged = _make_result(
        linkedin_url="https://linkedin.com/in/jane",
        phones=[PhoneHit(value="+15553334444", type="work", confidence=90.0, source="apollo_match")],
        apollo_person_id="587cf802f65125cad923a266",
    )

    changed = apply_gap_fill(row, merged)

    assert changed is True
    assert row.linkedin_url == "https://linkedin.com/in/jane"
    assert row.phone == "+15553334444"
    # Existing email is preserved (never overwritten).
    assert row.email == "jane@statebank.com"
    # Names-only row picks up a real provider attribution + Apollo id.
    assert row.discovery_source == "apollo_match"
    assert row.discovery_confidence == Decimal("90.00")
    assert row.apollo_person_id == "587cf802f65125cad923a266"


def test_apply_gap_fill_noop_when_nothing_new() -> None:
    row = _make_row(
        email="jane@statebank.com",
        phone="+15551112222",
        linkedin_url="https://linkedin.com/in/jane",
        emails=[{"value": "jane@statebank.com", "type": "work", "confidence": 90.0, "source": "apollo_match"}],
        phones=[{"value": "+15551112222", "type": "work", "confidence": 90.0, "source": "apollo_match"}],
        discovery_source="apollo_match",
        discovery_confidence=Decimal("90.00"),
    )
    merged = _make_result(
        email="jane@statebank.com",
        phone="+15551112222",
        linkedin_url="https://linkedin.com/in/jane",
    )

    assert apply_gap_fill(row, merged) is False


# ──────────────────────────── _build_bank_row ──────────────────────────────────


def test_build_bank_row_apollo_maps_source_and_arrays() -> None:
    result = _make_result(
        provider="apollo_match",
        confidence=88.5,
        email="ceo@statebank.com",
        linkedin_url="https://linkedin.com/in/ceo",
        emails=[EmailHit(value="ceo@statebank.com", type="work", confidence=88.5, source="apollo_match")],
        apollo_person_id="deadbeef",
    )

    row = _build_bank_row(bank_id=99, name="Jane Doe", title="President", result=result)

    assert isinstance(row, BankContact)
    assert row.bank_id == 99
    assert row.name == "Jane Doe"
    # provider starting with "apollo" collapses to the human-facing "apollo".
    assert row.source == "apollo"
    assert row.discovery_source == "apollo_match"
    assert row.discovery_confidence == Decimal("88.50")
    assert row.email == "ceo@statebank.com"
    assert row.linkedin_url == "https://linkedin.com/in/ceo"
    assert row.emails == [
        {"value": "ceo@statebank.com", "type": "work", "confidence": 88.5, "source": "apollo_match"}
    ]
    assert row.apollo_person_id == "deadbeef"
    # Discovery rows carry no filing provenance.
    assert row.role_context is None
    assert row.source_url is None
    assert row.enriched_at is not None


def test_build_bank_row_non_apollo_keeps_provider_as_source() -> None:
    result = _make_result(provider="hunter", confidence=72.0, email="info@statebank.com")

    row = _build_bank_row(bank_id=1, name="State Bank", title="Organization", result=result)

    assert row.source == "hunter"
    assert row.discovery_source == "hunter"
    # No multi-value array hit -> emails stays NULL and the schema synthesizes
    # a 1-element list from the scalar on read.
    assert row.emails is None
    assert row.email == "info@statebank.com"
