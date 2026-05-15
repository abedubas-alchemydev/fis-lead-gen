"""Person-phone extraction schema tests.

The PDF extractors (FINRA BrokerCheck + SEC X-17A-5) return Pydantic
models that the Gemini SDK populates from PDF content under
``response_schema``. If a field disappears from the schema, the SDK
silently drops it from the structured output — which is how phone
extraction would regress without anyone noticing.

These tests lock the schema contract: any code change that removes
``Officer.phone`` or ``PrimaryContact.phone`` fails fast.
"""

from __future__ import annotations

from brokercheck_extractor.schema.models import (
    FirmProfile,
    FocusReport,
    Officer,
    PrimaryContact,
)


def test_primary_contact_accepts_phone() -> None:
    """SEC X-17A-5 facing-page phone is already in the prompt — schema must
    accept and round-trip it."""
    contact = PrimaryContact(
        full_name="Jane Doe", title="FINOP", email="jane@example.com", phone="+15551234567"
    )
    assert contact.phone == "+15551234567"

    dumped = contact.model_dump()
    restored = PrimaryContact.model_validate(dumped)
    assert restored.phone == "+15551234567"


def test_primary_contact_phone_defaults_to_none() -> None:
    contact = PrimaryContact(full_name="Jane Doe")
    assert contact.phone is None


def test_focus_report_carries_phone_through() -> None:
    """Round-trip the full ``FocusReport`` shape the LLM produces, with phone
    nested inside ``contact``. JSON serialization must preserve it so the
    downstream consumers see the same value the LLM returned."""
    report = FocusReport(
        sec_file_number="8-12345",
        firm_name="Acme Securities",
        contact=PrimaryContact(full_name="Jane Doe", phone="+15551234567"),
    )
    assert report.contact.phone == "+15551234567"

    payload = report.model_dump_json()
    restored = FocusReport.model_validate_json(payload)
    assert restored.contact.phone == "+15551234567"


def test_officer_accepts_phone() -> None:
    """FINRA BrokerCheck PDFs almost never include a per-officer phone, but
    we surface the field so a future filing that DOES include one isn't
    silently dropped by the LLM structured output."""
    officer = Officer(name="JOHN SMITH", position="CEO", phone="(555) 555-0100")
    assert officer.phone == "(555) 555-0100"

    restored = Officer.model_validate(officer.model_dump())
    assert restored.phone == "(555) 555-0100"


def test_officer_phone_defaults_to_none() -> None:
    officer = Officer(name="JOHN SMITH")
    assert officer.phone is None


def test_firm_profile_propagates_officer_phone() -> None:
    profile = FirmProfile(
        crd_number="5393",
        officers=[
            Officer(name="JOHN SMITH", position="CEO", phone="+15551234567"),
            Officer(name="JANE DOE", position="CFO"),  # no phone
        ],
    )

    assert profile.officers[0].phone == "+15551234567"
    assert profile.officers[1].phone is None

    restored = FirmProfile.model_validate_json(profile.model_dump_json())
    assert restored.officers[0].phone == "+15551234567"
    assert restored.officers[1].phone is None


def test_firm_profile_business_phone_round_trip() -> None:
    """FINRA Main Office Business Telephone Number lives on FirmProfile so
    the firm-level phone isn't conflated with officer phones."""
    profile = FirmProfile(crd_number="5393", business_phone="817-859-5000")
    assert profile.business_phone == "817-859-5000"

    restored = FirmProfile.model_validate_json(profile.model_dump_json())
    assert restored.business_phone == "817-859-5000"


def test_firm_profile_business_phone_defaults_to_none() -> None:
    profile = FirmProfile(crd_number="5393")
    assert profile.business_phone is None
