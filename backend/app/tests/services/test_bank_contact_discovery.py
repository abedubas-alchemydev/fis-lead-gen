"""Unit tests for the banks path of the contact-discovery chain.

Mirror of the orchestrator-level tests in ``test_contact_discovery.py`` for
``discover_bank_contact`` (the banks sibling of ``discover_contact`` /
``discover_advisor_contact``): person happy path, cache hit skips providers,
all-miss returns None, and organisation entities route to ``find_org``.

Uses respx for HTTP and a hand-rolled fake ``AsyncSession`` so nothing hits a
real database, Apollo, Hunter, or Snov.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from app.core.config import settings
from app.models.bank import BankContact
from app.services.contact_discovery import apollo_match, hunter, snov
from app.services.contact_discovery.orchestrator import discover_bank_contact


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "hunter_api_key", "test-hunter-key")
    monkeypatch.setattr(settings, "snov_client_id", "test-snov-id")
    monkeypatch.setattr(settings, "snov_client_secret", "test-snov-secret")
    monkeypatch.setattr(settings, "contact_discovery_chain", "apollo_match,hunter,snov")
    monkeypatch.setattr(settings, "contact_discovery_min_confidence", 60.0)
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)
    monkeypatch.setattr(settings, "snov_request_timeout", 2.0)
    monkeypatch.setattr(snov, "_SNOV_POLL_INTERVAL_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _reset_snov_token() -> None:
    snov._reset_token_cache_for_tests()


class _FakeResult:
    def __init__(self, row: BankContact | None) -> None:
        self._row = row

    def scalars(self) -> "_FakeResult":
        return self

    def first(self) -> BankContact | None:
        return self._row


class _FakeBankSession:
    """Tiny AsyncSession stand-in for the bank discovery orchestrator tests."""

    def __init__(self, cached: BankContact | None = None) -> None:
        self.cached = cached
        self.added: list[BankContact] = []
        self.execute_calls: int = 0

    async def execute(self, _stmt: object) -> _FakeResult:
        self.execute_calls += 1
        return _FakeResult(self.cached)

    def add(self, row: BankContact) -> None:
        self.added.append(row)


@pytest.mark.asyncio
@respx.mock
async def test_discover_bank_person_happy_path(patch_settings: None) -> None:
    """A confident Apollo person hit persists a BankContact with channels; the
    filing-provenance columns (role_context / source_url) stay NULL on a
    discovery row."""
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "id": "587cf802f65125cad923a266",
                    "email": "ceo@statebank.com",
                    "email_status": "verified",
                    "phone_numbers": [],
                    "linkedin_url": "https://linkedin.com/in/ceo",
                }
            },
        )
    )
    respx.get(hunter.EMAIL_FINDER_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(snov.EMAIL_FINDER_URL).mock(return_value=httpx.Response(200, json={"data": {}}))

    session = _FakeBankSession()
    entity = {
        "type": "person",
        "first_name": "Jane",
        "last_name": "Doe",
        "org_name": "State Bank",
        "title": "President",
        "domain": "statebank.com",
    }
    row = await discover_bank_contact(entity, bank_id=42, session=session)  # type: ignore[arg-type]

    assert row is not None
    assert isinstance(row, BankContact)
    assert row.bank_id == 42
    assert row.email == "ceo@statebank.com"
    assert row.linkedin_url == "https://linkedin.com/in/ceo"
    assert row.discovery_source == "apollo_match"
    assert row.discovery_confidence == Decimal("90.00")
    assert row.apollo_person_id == "587cf802f65125cad923a266"
    # Discovery rows have no filing provenance.
    assert row.role_context is None
    assert row.source_url is None
    assert session.added == [row]


@pytest.mark.asyncio
@respx.mock
async def test_discover_bank_cache_hit_skips_all_providers(patch_settings: None) -> None:
    cached = BankContact(
        id=7,
        bank_id=42,
        name="Jane Doe",
        title="President",
        email="cached@statebank.com",
        source="apollo",
        discovery_source="apollo_match",
        discovery_confidence=Decimal("90.00"),
        enriched_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    apollo_route = respx.post(apollo_match.PEOPLE_MATCH_URL).mock(return_value=httpx.Response(200))

    session = _FakeBankSession(cached=cached)
    entity = {
        "type": "person",
        "first_name": "Jane",
        "last_name": "Doe",
        "org_name": "State Bank",
        "title": "President",
        "domain": "statebank.com",
    }
    row = await discover_bank_contact(entity, bank_id=42, session=session)  # type: ignore[arg-type]

    assert row is cached
    assert session.added == []
    assert not apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_discover_bank_all_providers_miss_returns_none(patch_settings: None) -> None:
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(return_value=httpx.Response(404))
    respx.get(hunter.EMAIL_FINDER_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
    )
    respx.post(snov.EMAIL_FINDER_URL).mock(return_value=httpx.Response(200, json={"data": {}}))

    session = _FakeBankSession()
    entity = {
        "type": "person",
        "first_name": "Ghost",
        "last_name": "User",
        "org_name": "Nowhere Bank",
        "title": "President",
        "domain": "nowhere.com",
    }
    row = await discover_bank_contact(entity, bank_id=42, session=session)  # type: ignore[arg-type]

    assert row is None
    assert session.added == []


@pytest.mark.asyncio
@respx.mock
async def test_discover_bank_organization_uses_find_org(patch_settings: None) -> None:
    """Org-type entities (the Phase-1 org→people seed for a bank with no
    contacts) must route to ``find_org``, never the person endpoint."""
    person_route = respx.post(apollo_match.PEOPLE_MATCH_URL).mock(return_value=httpx.Response(200))
    respx.post(apollo_match.ORG_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"organization": {"primary_phone": {"sanitized_number": "+15550199"}}},
        )
    )
    # Apollo org confidence 55 < 60 threshold; Hunter's generic 72 wins.
    hunter_route = respx.get(hunter.DOMAIN_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"emails": [{"value": "info@statebank.com", "type": "generic", "confidence": 72}]}},
        )
    )
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(snov.DOMAIN_SEARCH_URL).mock(return_value=httpx.Response(200, json={"emails": []}))

    session = _FakeBankSession()
    entity = {
        "type": "organization",
        "org_name": "State Bank",
        "domain": "statebank.com",
    }
    row = await discover_bank_contact(entity, bank_id=42, session=session)  # type: ignore[arg-type]

    assert row is not None
    assert row.email == "info@statebank.com"
    assert row.discovery_source == "hunter_domain"
    assert row.title == "Organization"
    assert not person_route.called
    assert hunter_route.called


class _SeqBankSession:
    """Fake session returning a scripted result per ``execute()`` call: the
    first (TTL-scoped cache lookup) misses, the second (full-key dedupe
    pre-check) finds the pre-existing row."""

    def __init__(self, results: list[BankContact | None]) -> None:
        self._results = list(results)
        self.added: list[BankContact] = []
        self.execute_calls = 0

    async def execute(self, _stmt: object) -> _FakeResult:
        idx = self.execute_calls
        self.execute_calls += 1
        row = self._results[idx] if idx < len(self._results) else None
        return _FakeResult(row)

    def add(self, row: BankContact) -> None:
        self.added.append(row)


@pytest.mark.asyncio
@respx.mock
async def test_discover_bank_org_reuses_row_past_cache_ttl(patch_settings: None) -> None:
    """Regression: a re-discovery past the 90-day cache TTL must reuse the
    pre-existing row (matched on the full ``uq_bank_contacts_dedupe`` key)
    instead of inserting a duplicate that would collide and fail the run.

    The TTL-scoped cache lookup misses the older row, but the full-key
    pre-check finds it — so no duplicate is added.
    """
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(return_value=httpx.Response(200))
    respx.post(apollo_match.ORG_ENRICH_URL).mock(
        return_value=httpx.Response(
            200, json={"organization": {"primary_phone": {"sanitized_number": "+15550199"}}}
        )
    )
    respx.get(hunter.DOMAIN_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"emails": [{"value": "info@statebank.com", "type": "generic", "confidence": 72}]}},
        )
    )
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(snov.DOMAIN_SEARCH_URL).mock(return_value=httpx.Response(200, json={"emails": []}))

    # An older discovery row (enriched_at well past the TTL) carrying the SAME
    # dedupe key the fresh org discovery will produce (name / title / source).
    existing = BankContact(
        id=11,
        bank_id=42,
        name="State Bank",
        title="Organization",
        source="hunter_domain",
        email="info@statebank.com",
        discovery_source="hunter_domain",
        discovery_confidence=Decimal("72.00"),
        enriched_at=datetime.now(timezone.utc) - timedelta(days=200),
    )
    session = _SeqBankSession([None, existing])  # cache miss, then dedupe hit
    entity = {"type": "organization", "org_name": "State Bank", "domain": "statebank.com"}

    row = await discover_bank_contact(entity, bank_id=42, session=session)  # type: ignore[arg-type]

    assert row is existing  # reused the pre-existing row
    assert session.added == []  # no duplicate inserted
    assert session.execute_calls == 2  # TTL cache miss, then full-key dedupe pre-check
