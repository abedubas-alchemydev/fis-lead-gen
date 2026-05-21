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
from app.services.contact_discovery import pdl
from app.services.contacts import (
    ApolloLookupError,
    ContactEnrichmentUnavailableError,
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


# ── PDL-primary path (PR 2 — find-phone PDL switch) ──────────────────


@pytest.fixture
def patch_settings_with_pdl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both PDL and Apollo configured so the new chain (PDL primary,
    Apollo fallback) runs end-to-end."""
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "pdl_api_key", "test-pdl-key")
    monkeypatch.setattr(settings, "pdl_min_likelihood", 6)
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)


@pytest.mark.asyncio
@respx.mock
async def test_find_phone_pdl_primary_writes_phones_jsonb(
    patch_settings_with_pdl: None,
) -> None:
    """PDL returns multi-value phones -> phones JSONB array + scalar phone
    are both written; Apollo is never called."""
    pdl_route = respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 8,
                "data": {
                    "emails": [{"address": "jane@example.com", "type": "professional"}],
                    "mobile_phone": "+15551112222",
                    "phone_numbers": ["+15551112222", "+15553334444"],
                },
            },
        )
    )
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(return_value=httpx.Response(200))

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "+15551112222"  # mobile leads the scalar
    assert result.phones is not None
    assert [p["type"] for p in result.phones] == ["mobile", "work"]
    assert [p["value"] for p in result.phones] == ["+15551112222", "+15553334444"]
    assert pdl_route.called
    assert not apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_find_phone_falls_back_to_apollo_on_pdl_miss(
    patch_settings_with_pdl: None,
) -> None:
    """PDL 404 (no match) -> Apollo runs; scalar phone updated from Apollo."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"phone_numbers": [{"sanitized_number": "+15559998888"}]}},
        )
    )

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "+15559998888"
    # Apollo doesn't populate the JSONB array (single-value provider);
    # read-time synthesis will project the scalar into a 1-element list.
    assert result.phones is None
    assert apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_find_phone_falls_back_to_apollo_on_pdl_5xx(
    patch_settings_with_pdl: None,
) -> None:
    """PDL hard error -> chain continues to Apollo silently (graceful degradation)."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(503))
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"phone_numbers": [{"sanitized_number": "+15557776666"}]}},
        )
    )

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "+15557776666"
    assert apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_find_phone_pdl_match_without_phones_falls_back_to_apollo(
    patch_settings_with_pdl: None,
) -> None:
    """PDL returns a hit but with no phone data -> chain falls to Apollo."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 8,
                "data": {
                    "emails": [{"address": "jane@example.com", "type": "professional"}],
                    # no mobile_phone, no phone_numbers
                },
            },
        )
    )
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"phone_numbers": [{"sanitized_number": "+15556665555"}]}},
        )
    )

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "+15556665555"
    assert apollo_route.called


@pytest.mark.asyncio
async def test_find_phone_raises_unavailable_when_neither_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither PDL nor Apollo configured -> ContactEnrichmentUnavailableError."""
    monkeypatch.setattr(settings, "pdl_api_key", None)
    monkeypatch.setattr(settings, "apollo_api_key", None)

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = ExecutiveContactService()

    with pytest.raises(ContactEnrichmentUnavailableError):
        await service.find_phone_for_contact(session, 42)
    assert session.commits == 0
