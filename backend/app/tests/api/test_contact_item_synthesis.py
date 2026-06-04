"""Read-time synthesis of ``emails`` / ``phones`` arrays on contact item schemas.

Legacy contact rows (FOCUS PDF, Phase-1 Apollo company search, single-value
Hunter / Snov hits) write only the scalar ``email`` / ``phone`` columns and
leave the new JSONB array columns NULL. The Pydantic ``*ContactItem``
schemas project that scalar into a 1-element array on read so the API
always emits a uniform list shape, no matter which source wrote the row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.schemas.broker_dealer import ExecutiveContactItem
from app.schemas.contact_hits import EmailHit, PhoneHit
from app.schemas.institutional_investor import InvestorContactItem
from app.schemas.investment_advisor import AdvisorContactItem


_NOW = datetime(2026, 5, 21, tzinfo=timezone.utc)


def _exec_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "bd_id": 100,
        "name": "Jane Doe",
        "title": "CEO",
        "email": None,
        "phone": None,
        "linkedin_url": None,
        "source": "provider",
        "discovery_source": None,
        "discovery_confidence": None,
        "enriched_at": _NOW,
        "unknown_reason": None,
        "emails": None,
        "phones": None,
    }
    base.update(overrides)
    return base


def _advisor_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "advisor_id": 100,
        "name": "Jane Doe",
        "title": "Chief Compliance Officer",
        "email": None,
        "phone": None,
        "linkedin_url": None,
        "source": "provider",
        "discovery_source": None,
        "discovery_confidence": None,
        "enriched_at": _NOW,
        "emails": None,
        "phones": None,
    }
    base.update(overrides)
    return base


def _investor_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "investor_id": 100,
        "name": "Jane Doe",
        "title": "Portfolio Manager",
        "email": None,
        "phone": None,
        "linkedin_url": None,
        "source": "provider",
        "discovery_source": None,
        "discovery_confidence": None,
        "enriched_at": _NOW,
        "emails": None,
        "phones": None,
    }
    base.update(overrides)
    return base


# ── ExecutiveContactItem ─────────────────────────────────────────────


def test_executive_empty_array_synthesizes_from_scalar() -> None:
    item = ExecutiveContactItem.model_validate(
        _exec_payload(
            email="jane@example.com",
            phone="+15551234567",
            discovery_source="apollo_match",
            discovery_confidence=85.0,
            source="apollo",
        )
    )
    assert item.emails == [
        EmailHit(value="jane@example.com", type="work", confidence=85.0, source="apollo_match")
    ]
    assert item.phones == [
        PhoneHit(value="+15551234567", type="work", confidence=85.0, source="apollo_match")
    ]


def test_executive_populated_array_not_overwritten() -> None:
    rich_emails = [
        {"value": "jane@firm.com", "type": "work", "confidence": 90.0, "source": "pdl"},
        {"value": "jane@gmail.com", "type": "personal", "confidence": 90.0, "source": "pdl"},
    ]
    rich_phones = [
        {"value": "+15550000001", "type": "mobile", "confidence": 90.0, "source": "pdl"},
        {"value": "+15550000002", "type": "work", "confidence": 90.0, "source": "pdl"},
    ]
    item = ExecutiveContactItem.model_validate(
        _exec_payload(
            email="jane@firm.com",
            phone="+15550000001",
            emails=rich_emails,
            phones=rich_phones,
        )
    )
    assert len(item.emails) == 2
    # Personal-first ordering (both hits share confidence, so type decides).
    assert item.emails[0].type == "personal"
    assert item.emails[1].type == "work"
    # Phones are not reordered — mobile stays where the source put it.
    assert len(item.phones) == 2
    assert item.phones[0].type == "mobile"


def test_executive_emails_ordered_personal_first_then_confidence() -> None:
    """Personal emails sort ahead of work; within a type higher confidence
    wins and unknown confidence sorts last. Applies on read regardless of the
    order the stored array was built in."""
    item = ExecutiveContactItem.model_validate(
        _exec_payload(
            emails=[
                {"value": "work-hi@firm.com", "type": "work", "confidence": 95.0, "source": "pdl"},
                {"value": "personal-lo@gmail.com", "type": "personal", "confidence": 60.0, "source": "pdl"},
                {"value": "work-none@firm.com", "type": "work", "confidence": None, "source": "hunter"},
                {"value": "personal-hi@gmail.com", "type": "personal", "confidence": 90.0, "source": "pdl"},
            ],
        )
    )
    assert [hit.value for hit in item.emails] == [
        "personal-hi@gmail.com",
        "personal-lo@gmail.com",
        "work-hi@firm.com",
        "work-none@firm.com",
    ]


def test_executive_no_scalar_no_array_returns_empty_lists() -> None:
    item = ExecutiveContactItem.model_validate(_exec_payload())
    assert item.emails == []
    assert item.phones == []


def test_executive_source_fallback_when_discovery_source_missing() -> None:
    item = ExecutiveContactItem.model_validate(
        _exec_payload(
            email="jane@example.com",
            discovery_source=None,
            source="focus_report",
        )
    )
    assert item.emails[0].source == "focus_report"
    assert item.phones == []


# ── InvestorContactItem ──────────────────────────────────────────────


def test_investor_empty_array_synthesizes_from_scalar() -> None:
    item = InvestorContactItem.model_validate(
        _investor_payload(
            email="pm@fund.com",
            phone="+15552223333",
            discovery_source="pdl",
            discovery_confidence=80.0,
            source="pdl",
        )
    )
    assert item.emails == [
        EmailHit(value="pm@fund.com", type="work", confidence=80.0, source="pdl")
    ]
    assert item.phones == [
        PhoneHit(value="+15552223333", type="work", confidence=80.0, source="pdl")
    ]


def test_investor_populated_array_not_overwritten() -> None:
    item = InvestorContactItem.model_validate(
        _investor_payload(
            email="pm@fund.com",
            emails=[
                {"value": "pm@fund.com", "type": "work", "confidence": 90.0, "source": "pdl"},
                {"value": "pm@personal.com", "type": "personal", "confidence": 90.0, "source": "pdl"},
            ],
        )
    )
    assert [hit.type for hit in item.emails] == ["personal", "work"]


def test_investor_no_scalar_no_array_returns_empty_lists() -> None:
    item = InvestorContactItem.model_validate(_investor_payload())
    assert item.emails == []
    assert item.phones == []


# ── AdvisorContactItem ───────────────────────────────────────────────


def test_advisor_empty_array_synthesizes_from_scalar() -> None:
    item = AdvisorContactItem.model_validate(
        _advisor_payload(
            email="ria@advisors.com",
            phone="+15554445555",
            source="hunter",
            discovery_source="hunter",
            discovery_confidence=70.0,
        )
    )
    assert item.emails[0] == EmailHit(
        value="ria@advisors.com", type="work", confidence=70.0, source="hunter"
    )
    assert item.phones[0] == PhoneHit(
        value="+15554445555", type="work", confidence=70.0, source="hunter"
    )


def test_advisor_populated_array_not_overwritten() -> None:
    item = AdvisorContactItem.model_validate(
        _advisor_payload(
            phones=[
                {"value": "+15550001111", "type": "mobile", "confidence": 90.0, "source": "pdl"},
                {"value": "+15550002222", "type": "work", "confidence": 90.0, "source": "pdl"},
            ],
        )
    )
    assert [hit.type for hit in item.phones] == ["mobile", "work"]


def test_advisor_no_scalar_no_array_returns_empty_lists() -> None:
    item = AdvisorContactItem.model_validate(_advisor_payload())
    assert item.emails == []
    assert item.phones == []
