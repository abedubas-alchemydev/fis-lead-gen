"""Tests for the Apollo phone-reveal webhook handler.

Covers the security path (503 when unconfigured, 404 on wrong secret),
the payload-shape happy path (phones land on the matching row with the
right type/confidence), idempotency on Apollo's retries (sanitized-value
dedupe never double-writes), and graceful no-op on payloads that don't
match any row (Apollo's late callbacks for deleted rows).

Uses a hand-rolled fake AsyncSession so nothing hits a real DB. The
session understands enough of SQLAlchemy's ``select(...).where(col == val)``
shape to filter by ``apollo_person_id`` against pre-seeded fake rows; the
production query uses an indexed equality check so the simplified matcher
faithfully covers it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.advisor_contact import AdvisorContact
from app.models.discovered_email import DiscoveredEmail
from app.models.executive_contact import ExecutiveContact
from app.models.investor_contact import InvestorContact


WEBHOOK_SECRET = "supersecret-test-only"
WEBHOOK_URL = f"/api/v1/webhooks/apollo/{WEBHOOK_SECRET}/phone-reveal"


# ──────────────────────────── Fake session ────────────────────────────


class _Filter:
    """Capture ``select(Model).where(Model.col == value)`` enough to
    match against the in-memory row list without touching a real DB."""

    def __init__(self, model: type, col_name: str, value: Any) -> None:
        self.model = model
        self.col_name = col_name
        self.value = value


def _extract_filter(stmt: Any) -> _Filter | None:
    """SQLAlchemy's select returns a Select object whose
    ``whereclause`` is a BinaryExpression. We pull the model class from
    ``column_descriptions`` and the (col_name, value) tuple from the
    comparator. Anything more complex returns None — those tests don't
    exercise the handler."""
    try:
        desc = stmt.column_descriptions[0]
        model = desc["type"]
        where = stmt.whereclause
        left = where.left
        right = where.right
        col_name = left.key
        value = right.value
        return _Filter(model, col_name, value)
    except (AttributeError, IndexError, KeyError):
        return None


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    def __init__(
        self,
        advisor_rows: list[AdvisorContact] | None = None,
        executive_rows: list[ExecutiveContact] | None = None,
        investor_rows: list[InvestorContact] | None = None,
        discovered_email_rows: list[DiscoveredEmail] | None = None,
    ) -> None:
        self.tables: dict[type, list[Any]] = {
            AdvisorContact: list(advisor_rows or []),
            ExecutiveContact: list(executive_rows or []),
            InvestorContact: list(investor_rows or []),
            DiscoveredEmail: list(discovered_email_rows or []),
        }
        self.commit_count = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        flt = _extract_filter(stmt)
        if flt is None or flt.model not in self.tables:
            return _FakeResult([])
        matched = [
            r for r in self.tables[flt.model]
            if getattr(r, flt.col_name, None) == flt.value
        ]
        return _FakeResult(matched)

    async def commit(self) -> None:
        self.commit_count += 1


def _make_advisor_row(
    *,
    apollo_person_id: str | None = None,
    phone: str | None = None,
    phones: list[dict] | None = None,
    discovery_source: str | None = None,
) -> AdvisorContact:
    row = AdvisorContact()
    row.advisor_id = 1
    row.name = "Jane Doe"
    row.title = "Director"
    row.apollo_person_id = apollo_person_id
    row.phone = phone
    row.phones = phones
    row.discovery_source = discovery_source
    row.discovery_confidence = None
    row.source = "adv"
    return row


def _make_executive_row(*, apollo_person_id: str | None = None) -> ExecutiveContact:
    row = ExecutiveContact()
    row.bd_id = 99
    row.name = "Bob Smith"
    row.title = "CEO"
    row.apollo_person_id = apollo_person_id
    row.phone = None
    row.phones = None
    row.discovery_source = None
    row.discovery_confidence = None
    row.source = "provider"
    return row


def _make_investor_row(*, apollo_person_id: str | None = None) -> InvestorContact:
    row = InvestorContact()
    row.investor_id = 7
    row.name = "Carol Lee"
    row.title = "CIO"
    row.apollo_person_id = apollo_person_id
    row.phone = None
    row.phones = None
    row.discovery_source = None
    row.discovery_confidence = None
    row.source = "provider"
    return row


def _make_discovered_email_row(
    *,
    apollo_person_id: str | None = None,
    enriched_phone: str | None = None,
) -> DiscoveredEmail:
    row = DiscoveredEmail()
    row.id = 1
    row.email = "jane@example.com"
    row.apollo_person_id = apollo_person_id
    row.enriched_phone = enriched_phone
    return row


def _override_session(session: _FakeSession):
    async def _gen() -> AsyncGenerator[_FakeSession, None]:
        yield session
    return _gen


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# ──────────────────────────── Fixtures ────────────────────────────


@pytest.fixture
def configure_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_webhook_secret", WEBHOOK_SECRET)


@pytest.fixture
def with_session():
    """Context manager: install a session override, yield it, then clear."""
    sessions: list[_FakeSession] = []

    def _install(session: _FakeSession) -> _FakeSession:
        app.dependency_overrides[get_db_session] = _override_session(session)
        sessions.append(session)
        return session

    yield _install
    app.dependency_overrides.clear()


# ──────────────────────────── Security tests ────────────────────────────


@pytest.mark.asyncio
async def test_503_when_secret_not_configured(
    monkeypatch: pytest.MonkeyPatch, with_session
) -> None:
    monkeypatch.setattr(settings, "apollo_webhook_secret", None)
    with_session(_FakeSession())

    async with _client() as client:
        response = await client.post(
            "/api/v1/webhooks/apollo/anything/phone-reveal",
            json={"people": []},
        )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_404_on_wrong_secret(configure_secret: None, with_session) -> None:
    with_session(_FakeSession())

    async with _client() as client:
        response = await client.post(
            "/api/v1/webhooks/apollo/wrong-secret/phone-reveal",
            json={"people": []},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_200_on_correct_secret_with_empty_payload(
    configure_secret: None, with_session
) -> None:
    session = with_session(_FakeSession())

    async with _client() as client:
        response = await client.post(WEBHOOK_URL, json={"people": []})
    assert response.status_code == 200
    assert response.json() == {"rows_updated": 0, "phones_added": 0}
    assert session.commit_count == 0


# ──────────────────────────── Happy-path / merge tests ────────────────────────────


@pytest.mark.asyncio
async def test_phones_land_on_advisor_row(configure_secret: None, with_session) -> None:
    row = _make_advisor_row(apollo_person_id="apollo-123")
    session = with_session(_FakeSession(advisor_rows=[row]))

    payload = {
        "people": [
            {
                "id": "apollo-123",
                "phone_numbers": [
                    {
                        "sanitized_number": "+15551112222",
                        "raw_number": "+1 555-111-2222",
                        "type_cd": "mobile",
                        "confidence_cd": "high",
                    }
                ],
            }
        ]
    }
    async with _client() as client:
        response = await client.post(WEBHOOK_URL, json=payload)
    assert response.status_code == 200
    assert response.json() == {"rows_updated": 1, "phones_added": 1}
    assert row.phone == "+15551112222"
    assert row.phones is not None and len(row.phones) == 1
    assert row.phones[0]["value"] == "+15551112222"
    assert row.phones[0]["type"] == "mobile"
    assert row.phones[0]["source"] == "apollo_phone_reveal"
    assert row.discovery_source == "apollo_phone_reveal"
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_idempotent_on_apollo_retry(configure_secret: None, with_session) -> None:
    """Apollo retries failed deliveries; the same payload arriving twice
    must not duplicate the phone entry."""
    row = _make_advisor_row(apollo_person_id="apollo-123")
    session = with_session(_FakeSession(advisor_rows=[row]))

    payload = {
        "people": [
            {
                "id": "apollo-123",
                "phone_numbers": [
                    {"sanitized_number": "+15551112222", "type_cd": "mobile", "confidence_cd": "high"}
                ],
            }
        ]
    }
    async with _client() as client:
        await client.post(WEBHOOK_URL, json=payload)
        response2 = await client.post(WEBHOOK_URL, json=payload)
    assert response2.json() == {"rows_updated": 0, "phones_added": 0}
    assert row.phones is not None and len(row.phones) == 1
    # First call committed once; second commits 0 because nothing changed.
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_multiple_phones_append_and_pick_highest_confidence_scalar(
    configure_secret: None, with_session
) -> None:
    row = _make_advisor_row(apollo_person_id="apollo-123")
    with_session(_FakeSession(advisor_rows=[row]))

    payload = {
        "people": [
            {
                "id": "apollo-123",
                "phone_numbers": [
                    {"sanitized_number": "+15551112222", "type_cd": "mobile", "confidence_cd": "low"},
                    {"sanitized_number": "+15553334444", "type_cd": "work", "confidence_cd": "high"},
                ],
            }
        ]
    }
    async with _client() as client:
        response = await client.post(WEBHOOK_URL, json=payload)
    assert response.json() == {"rows_updated": 1, "phones_added": 2}
    assert row.phone == "+15553334444"
    assert len(row.phones) == 2


@pytest.mark.asyncio
async def test_executive_and_investor_rows_also_resolved(
    configure_secret: None, with_session
) -> None:
    """One Apollo person id can in theory match rows across all three tables
    (same officer extracted into multiple contexts). The handler walks all
    three on every payload, so each gets the phones."""
    exec_row = _make_executive_row(apollo_person_id="shared-id")
    inv_row = _make_investor_row(apollo_person_id="shared-id")
    with_session(
        _FakeSession(executive_rows=[exec_row], investor_rows=[inv_row])
    )

    payload = {
        "people": [
            {
                "id": "shared-id",
                "phone_numbers": [{"sanitized_number": "+15550000", "type_cd": "mobile"}],
            }
        ]
    }
    async with _client() as client:
        response = await client.post(WEBHOOK_URL, json=payload)
    assert response.json() == {"rows_updated": 2, "phones_added": 2}
    assert exec_row.phone == "+15550000"
    assert inv_row.phone == "+15550000"


@pytest.mark.asyncio
async def test_unknown_person_id_is_graceful(configure_secret: None, with_session) -> None:
    """A late Apollo callback for a row we've deleted (or that never
    landed in our DB) returns 200 with rows_updated=0 so Apollo doesn't
    retry. Apollo's retry budget is finite; loops of 5xx burn it."""
    with_session(_FakeSession())

    payload = {
        "people": [
            {
                "id": "no-such-person",
                "phone_numbers": [{"sanitized_number": "+15550000", "type_cd": "mobile"}],
            }
        ]
    }
    async with _client() as client:
        response = await client.post(WEBHOOK_URL, json=payload)
    assert response.status_code == 200
    assert response.json() == {"rows_updated": 0, "phones_added": 0}


@pytest.mark.asyncio
async def test_payload_without_people_key_is_no_op(
    configure_secret: None, with_session
) -> None:
    """Apollo can send delivery envelopes without ``people`` (status-only
    pings). Don't 400 — that triggers a retry."""
    with_session(_FakeSession())

    async with _client() as client:
        response = await client.post(WEBHOOK_URL, json={"status": "pending"})
    assert response.status_code == 200
    assert response.json() == {"rows_updated": 0, "phones_added": 0}


@pytest.mark.asyncio
async def test_400_on_non_object_payload(configure_secret: None, with_session) -> None:
    with_session(_FakeSession())

    async with _client() as client:
        response = await client.post(WEBHOOK_URL, json=["not", "an", "object"])
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_unknown_type_cd_defaults_to_work(
    configure_secret: None, with_session
) -> None:
    row = _make_advisor_row(apollo_person_id="apollo-123")
    with_session(_FakeSession(advisor_rows=[row]))

    payload = {
        "people": [
            {
                "id": "apollo-123",
                "phone_numbers": [
                    {"sanitized_number": "+15551112222", "type_cd": "satellite"}
                ],
            }
        ]
    }
    async with _client() as client:
        await client.post(WEBHOOK_URL, json=payload)
    assert row.phones[0]["type"] == "work"


@pytest.mark.asyncio
async def test_prefers_sanitized_over_raw_number(
    configure_secret: None, with_session
) -> None:
    row = _make_advisor_row(apollo_person_id="apollo-123")
    with_session(_FakeSession(advisor_rows=[row]))

    payload = {
        "people": [
            {
                "id": "apollo-123",
                "phone_numbers": [
                    {
                        "sanitized_number": "+15551112222",
                        "raw_number": "+1 (555) 111-2222",
                        "type_cd": "mobile",
                    }
                ],
            }
        ]
    }
    async with _client() as client:
        await client.post(WEBHOOK_URL, json=payload)
    assert row.phones[0]["value"] == "+15551112222"


@pytest.mark.asyncio
async def test_falls_back_to_raw_when_no_sanitized(
    configure_secret: None, with_session
) -> None:
    row = _make_advisor_row(apollo_person_id="apollo-123")
    with_session(_FakeSession(advisor_rows=[row]))

    payload = {
        "people": [
            {
                "id": "apollo-123",
                "phone_numbers": [{"raw_number": "+1 555-111-2222", "type_cd": "mobile"}],
            }
        ]
    }
    async with _client() as client:
        await client.post(WEBHOOK_URL, json=payload)
    assert row.phones[0]["value"] == "+1 555-111-2222"


# ──────────────────── Discovered-email (email-extractor) tests ────────────────────


@pytest.mark.asyncio
async def test_phone_lands_on_discovered_email_row(
    configure_secret: None, with_session
) -> None:
    """A discovered_email row enriched via the email-extractor flow gets its
    scalar ``enriched_phone`` filled when the reveal callback lands."""
    row = _make_discovered_email_row(apollo_person_id="apollo-de-1")
    session = with_session(_FakeSession(discovered_email_rows=[row]))

    payload = {
        "people": [
            {
                "id": "apollo-de-1",
                "phone_numbers": [
                    {"sanitized_number": "+15551112222", "type_cd": "mobile", "confidence_cd": "high"}
                ],
            }
        ]
    }
    async with _client() as client:
        response = await client.post(WEBHOOK_URL, json=payload)
    assert response.json() == {"rows_updated": 1, "phones_added": 1}
    assert row.enriched_phone == "+15551112222"
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_discovered_email_phone_is_set_if_null_only(
    configure_secret: None, with_session
) -> None:
    """Existing ``enriched_phone`` isn't overwritten (set-if-null), so an
    Apollo retry or a second reveal is a no-op rather than a clobber."""
    row = _make_discovered_email_row(
        apollo_person_id="apollo-de-1", enriched_phone="+19998887777"
    )
    session = with_session(_FakeSession(discovered_email_rows=[row]))

    payload = {
        "people": [
            {
                "id": "apollo-de-1",
                "phone_numbers": [{"sanitized_number": "+15551112222", "type_cd": "mobile"}],
            }
        ]
    }
    async with _client() as client:
        response = await client.post(WEBHOOK_URL, json=payload)
    assert response.json() == {"rows_updated": 0, "phones_added": 0}
    assert row.enriched_phone == "+19998887777"
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_discovered_email_picks_highest_confidence_phone(
    configure_secret: None, with_session
) -> None:
    """With multiple revealed numbers, the scalar takes the highest-confidence
    one (same tiebreak the contact tables use)."""
    row = _make_discovered_email_row(apollo_person_id="apollo-de-1")
    with_session(_FakeSession(discovered_email_rows=[row]))

    payload = {
        "people": [
            {
                "id": "apollo-de-1",
                "phone_numbers": [
                    {"sanitized_number": "+15551112222", "type_cd": "mobile", "confidence_cd": "low"},
                    {"sanitized_number": "+15553334444", "type_cd": "work", "confidence_cd": "high"},
                ],
            }
        ]
    }
    async with _client() as client:
        response = await client.post(WEBHOOK_URL, json=payload)
    assert response.json() == {"rows_updated": 1, "phones_added": 1}
    assert row.enriched_phone == "+15553334444"
