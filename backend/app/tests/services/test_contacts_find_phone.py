"""Unit tests for ``ExecutiveContactService.find_phone_for_contact``.

The "Find phone" button on the broker-dealer People panel calls a per-row
endpoint that hits Apollo ``/people/match`` with the contact's email and
writes the returned phone (if any) back onto the ExecutiveContact row —
without overwriting name/title/company/etc. (those may come from a
higher-trust FOCUS source) and without nulling an existing phone if
Apollo returned nothing.

All HTTP via ``respx``; session is a tiny stand-in that supports ``get``,
``commit``, and ``refresh`` — the slice ``find_phone_for_contact`` uses.
Pattern mirrors ``test_email_extractor/test_apollo_enrichment_phone.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import respx

from app.core.config import settings
from app.models.executive_contact import ExecutiveContact
from app.services.contacts import (
    ApolloLookupError,
    ContactNotFoundError,
    ExecutiveContactService,
    NoEmailForLookupError,
)


_APOLLO_MATCH_URL = ExecutiveContactService._APOLLO_MATCH_URL


class _FakeSession:
    """AsyncSession stand-in returning a single pre-seeded ExecutiveContact."""

    def __init__(self, row: ExecutiveContact | None) -> None:
        self._row = row
        self.commits: int = 0
        self.refreshes: int = 0

    async def get(self, _model: Any, _pk: Any) -> ExecutiveContact | None:
        return self._row

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _row: Any) -> None:
        self.refreshes += 1


def _seed_contact(*, email: str | None = "jane@example.com", phone: str | None = None) -> ExecutiveContact:
    """Build an in-memory ExecutiveContact with only the fields the lookup reads/writes."""
    contact = ExecutiveContact(
        bd_id=1,
        name="Jane Doe",
        title="CFO",
        email=email,
        phone=phone,
        linkedin_url=None,
        source="apollo",
        enriched_at=datetime.now(timezone.utc) - timedelta(days=10),
    )
    contact.id = 42
    return contact


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")


@pytest.mark.asyncio
@respx.mock
async def test_find_phone_writes_apollo_phone_to_contact(patch_settings: None) -> None:
    """Happy path: Apollo returns a phone, the contact row gets it written."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "name": "Jane Doe",
                    "phone_numbers": [{"sanitized_number": "+15551234567"}],
                }
            },
        )
    )

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "+15551234567"
    assert session.commits == 1


@pytest.mark.asyncio
@respx.mock
async def test_find_phone_leaves_phone_null_when_apollo_has_none(patch_settings: None) -> None:
    """Apollo returns no ``phone_numbers`` -> contact.phone stays None,
    but ``enriched_at`` advances so the UI can show "tried, nothing found"."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"name": "Jane Doe", "phone_numbers": []}},
        )
    )

    contact = _seed_contact()
    original_enriched_at = contact.enriched_at
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone is None
    assert result.enriched_at is not None
    assert result.enriched_at > original_enriched_at
    assert session.commits == 1


@pytest.mark.asyncio
@respx.mock
async def test_find_phone_does_not_overwrite_existing_phone_with_null(
    patch_settings: None,
) -> None:
    """If the contact already has a phone (e.g. from FOCUS extraction) and
    Apollo returns no phone, we must not blow away the existing value."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"name": "Jane Doe", "phone_numbers": []}},
        )
    )

    contact = _seed_contact(phone="+15559998888")
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "+15559998888"


@pytest.mark.asyncio
@respx.mock
async def test_find_phone_raises_when_contact_has_no_email(patch_settings: None) -> None:
    """A FINRA-only officer with no email anchor can't be looked up."""
    contact = _seed_contact(email=None)
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    with pytest.raises(NoEmailForLookupError):
        await service.find_phone_for_contact(session, 42)

    assert session.commits == 0


@pytest.mark.asyncio
@respx.mock
async def test_find_phone_raises_apollo_lookup_error_on_http_500(patch_settings: None) -> None:
    """Apollo 500 -> ApolloLookupError, no DB write."""
    respx.post(_APOLLO_MATCH_URL).mock(return_value=httpx.Response(500, text="oops"))

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    with pytest.raises(ApolloLookupError):
        await service.find_phone_for_contact(session, 42)

    assert session.commits == 0


@pytest.mark.asyncio
async def test_find_phone_raises_contact_not_found(patch_settings: None) -> None:
    """Missing contact id -> ContactNotFoundError (mapped to 404 by the endpoint)."""
    session = _FakeSession(None)
    service = ExecutiveContactService()

    with pytest.raises(ContactNotFoundError):
        await service.find_phone_for_contact(session, 999)

    assert session.commits == 0


@pytest.mark.asyncio
@respx.mock
async def test_find_phone_falls_back_to_raw_number(patch_settings: None) -> None:
    """If Apollo returns only ``raw_number`` (no sanitized), still write it
    so we don't drop a usable phone — the shared helper handles this."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"phone_numbers": [{"raw_number": "(555) 123-4567"}]}},
        )
    )

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "(555) 123-4567"
