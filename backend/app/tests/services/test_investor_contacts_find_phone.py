"""Unit tests for ``InvestorContactService.find_phone_for_contact``.

Mirrors ``test_contacts_find_phone.py`` for the institutional-investor
side: PDL primary + Apollo fallback against ``InvestorContact`` rows.
The chain logic is identical to the BD service; these tests validate it
holds when the model class changes (catches a silent divergence on
future refactors).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import respx

from app.core.config import settings
from app.models.investor_contact import InvestorContact
from app.services.contact_discovery import pdl
from app.services.contacts import (
    ApolloLookupError,
    ContactEnrichmentUnavailableError,
    ContactNotFoundError,
    NoEmailForLookupError,
)
from app.services.investor_contacts import InvestorContactService


_APOLLO_MATCH_URL = InvestorContactService._APOLLO_MATCH_URL


class _FakeSession:
    """AsyncSession stand-in returning a single pre-seeded InvestorContact."""

    def __init__(self, row: InvestorContact | None) -> None:
        self._row = row
        self.commits: int = 0
        self.refreshes: int = 0

    async def get(self, _model: Any, _pk: Any) -> InvestorContact | None:
        return self._row

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _row: Any) -> None:
        self.refreshes += 1


def _seed_contact(
    *, email: str | None = "pm@fund.com", phone: str | None = None
) -> InvestorContact:
    contact = InvestorContact(
        investor_id=1,
        name="Jane Doe",
        title="Portfolio Manager",
        email=email,
        phone=phone,
        linkedin_url=None,
        source="provider",
        enriched_at=datetime.now(timezone.utc) - timedelta(days=10),
    )
    contact.id = 42
    return contact


@pytest.fixture
def patch_settings_with_pdl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "pdl_api_key", "test-pdl-key")
    monkeypatch.setattr(settings, "pdl_min_likelihood", 6)
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)


@pytest.fixture
def patch_apollo_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "pdl_api_key", None)


@pytest.mark.asyncio
@respx.mock
async def test_pdl_primary_writes_phones_jsonb(patch_settings_with_pdl: None) -> None:
    """PDL hit with phones -> phones JSONB array + scalar phone; Apollo never called."""
    pdl_route = respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 8,
                "data": {
                    "mobile_phone": "+15551112222",
                    "phone_numbers": ["+15551112222", "+15553334444"],
                },
            },
        )
    )
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(return_value=httpx.Response(200))

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = InvestorContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "+15551112222"
    assert result.phones is not None
    assert [p["type"] for p in result.phones] == ["mobile", "work"]
    assert pdl_route.called
    assert not apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_falls_back_to_apollo_on_pdl_miss(
    patch_settings_with_pdl: None,
) -> None:
    """PDL 404 -> Apollo runs and provides the phone; phones JSONB stays NULL
    (Apollo is single-value; synthesis fills in on read)."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"phone_numbers": [{"sanitized_number": "+15559998888"}]}},
        )
    )

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = InvestorContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "+15559998888"
    assert result.phones is None
    assert apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_pdl_match_without_phones_falls_back_to_apollo(
    patch_settings_with_pdl: None,
) -> None:
    """PDL returns a hit but no phones -> chain falls to Apollo."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 8,
                "data": {
                    "emails": [{"address": "pm@fund.com", "type": "professional"}],
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
    service = InvestorContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "+15556665555"
    assert apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_does_not_overwrite_existing_phone_with_null(
    patch_apollo_only: None,
) -> None:
    """Both providers (Apollo here -- PDL key absent) come up empty;
    existing scalar phone must not be regressed to NULL."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"phone_numbers": []}},
        )
    )

    contact = _seed_contact(phone="+15550001111")
    session = _FakeSession(contact)
    service = InvestorContactService()

    result = await service.find_phone_for_contact(session, 42)

    assert result.phone == "+15550001111"


@pytest.mark.asyncio
@respx.mock
async def test_raises_apollo_lookup_error_on_http_500(
    patch_apollo_only: None,
) -> None:
    """Apollo transient error -> ApolloLookupError (FE shows 502)."""
    respx.post(_APOLLO_MATCH_URL).mock(return_value=httpx.Response(500, text="oops"))

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = InvestorContactService()

    with pytest.raises(ApolloLookupError):
        await service.find_phone_for_contact(session, 42)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_raises_when_contact_has_no_email(patch_apollo_only: None) -> None:
    contact = _seed_contact(email=None)
    session = _FakeSession(contact)
    service = InvestorContactService()

    with pytest.raises(NoEmailForLookupError):
        await service.find_phone_for_contact(session, 42)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_raises_contact_not_found(patch_apollo_only: None) -> None:
    session = _FakeSession(None)
    service = InvestorContactService()

    with pytest.raises(ContactNotFoundError):
        await service.find_phone_for_contact(session, 999)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_raises_unavailable_when_neither_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", None)
    monkeypatch.setattr(settings, "apollo_api_key", None)

    contact = _seed_contact()
    session = _FakeSession(contact)
    service = InvestorContactService()

    with pytest.raises(ContactEnrichmentUnavailableError):
        await service.find_phone_for_contact(session, 42)
    assert session.commits == 0
