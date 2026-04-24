"""Unit tests for the contact discovery chain.

Coverage:

* Per-provider happy path (person + org).
* Per-provider no-match (empty / missing body).
* Transient errors (500, 429) -> provider returns None, chain continues.
* Confidence below threshold -> result filtered out, chain continues.
* Orchestrator chain order (Apollo hit -> Hunter skipped).
* Cache hit returns cached row without touching any provider.
* Snov token refresh on 401.

All tests use respx for HTTP and a hand-rolled fake ``AsyncSession`` so nothing
hits a real database, Apollo, Hunter, or Snov.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from app.core.config import settings
from app.models.executive_contact import ExecutiveContact
from app.services.contact_discovery import apollo_match, hunter, snov
from app.services.contact_discovery.apollo_match import ApolloMatchProvider
from app.services.contact_discovery.hunter import HunterProvider
from app.services.contact_discovery.orchestrator import discover_contact
from app.services.contact_discovery.snov import SnovProvider


# ──────────────────────────── Fixtures ────────────────────────────


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "hunter_api_key", "test-hunter-key")
    monkeypatch.setattr(settings, "snov_client_id", "test-snov-id")
    monkeypatch.setattr(settings, "snov_client_secret", "test-snov-secret")
    monkeypatch.setattr(settings, "contact_discovery_chain", "apollo_match,hunter,snov")
    monkeypatch.setattr(settings, "contact_discovery_min_confidence", 60.0)
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)


@pytest.fixture(autouse=True)
def _reset_snov_token() -> None:
    """Guarantee each test starts with an empty Snov token cache."""
    snov._reset_token_cache_for_tests()


class _FakeResult:
    """Mimic SQLAlchemy's ``Result`` enough for ``scalars().first()``."""

    def __init__(self, row: ExecutiveContact | None) -> None:
        self._row = row

    def scalars(self) -> "_FakeResult":
        return self

    def first(self) -> ExecutiveContact | None:
        return self._row


class _FakeSession:
    """Tiny AsyncSession stand-in used by the orchestrator tests.

    Tracks ``session.add(...)`` calls so tests can assert which rows the
    orchestrator persisted. ``execute`` returns a pre-seeded cache row or
    None depending on what the test staged.
    """

    def __init__(self, cached: ExecutiveContact | None = None) -> None:
        self.cached = cached
        self.added: list[ExecutiveContact] = []
        self.execute_calls: int = 0

    async def execute(self, _stmt: object) -> _FakeResult:
        self.execute_calls += 1
        return _FakeResult(self.cached)

    def add(self, row: ExecutiveContact) -> None:
        self.added.append(row)


# ──────────────────────────── Apollo person ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_apollo_person_happy_path(patch_settings: None) -> None:
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "bryan@example.com",
                    "email_status": "verified",
                    "phone_numbers": [{"sanitized_number": "+15550100"}],
                    "linkedin_url": "https://linkedin.com/in/bryan",
                }
            },
        )
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is not None
    assert result.email == "bryan@example.com"
    assert result.phone == "+15550100"
    assert result.linkedin_url == "https://linkedin.com/in/bryan"
    assert result.confidence == 90.0
    assert result.provider == "apollo_match"


@pytest.mark.asyncio
@respx.mock
async def test_apollo_person_no_match(patch_settings: None) -> None:
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": None})
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("Ghost", "User", "Nowhere LLC", "nowhere.com")

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_apollo_person_transient_error_returns_none(patch_settings: None) -> None:
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(return_value=httpx.Response(500))

    provider = ApolloMatchProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_apollo_org_happy_path(patch_settings: None) -> None:
    respx.post(apollo_match.ORG_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "organization": {
                    "primary_phone": {"sanitized_number": "+15550111"},
                    "linkedin_url": "https://linkedin.com/company/example",
                }
            },
        )
    )

    provider = ApolloMatchProvider()
    result = await provider.find_org("Example LLC", "example.com")

    assert result is not None
    assert result.email is None
    assert result.phone == "+15550111"
    assert result.provider == "apollo_org"
    assert result.confidence == 55.0


# ──────────────────────────── Hunter ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_hunter_person_happy_path(patch_settings: None) -> None:
    respx.get(hunter.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "email": "bryan@example.com",
                    "score": 87,
                    "phone_number": "+15550100",
                    "linkedin_url": "https://linkedin.com/in/bryan",
                }
            },
        )
    )

    provider = HunterProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is not None
    assert result.confidence == 87.0
    assert result.email == "bryan@example.com"
    assert result.provider == "hunter"


@pytest.mark.asyncio
@respx.mock
async def test_hunter_person_rate_limited_returns_none(patch_settings: None) -> None:
    respx.get(hunter.EMAIL_FINDER_URL).mock(return_value=httpx.Response(429))

    provider = HunterProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_hunter_org_picks_generic_inbox(patch_settings: None) -> None:
    respx.get(hunter.DOMAIN_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "emails": [
                        {"value": "bryan@example.com", "type": "personal", "confidence": 90},
                        {"value": "info@example.com", "type": "generic", "confidence": 72},
                    ]
                }
            },
        )
    )

    provider = HunterProvider()
    result = await provider.find_org("Example LLC", "example.com")

    assert result is not None
    assert result.email == "info@example.com"
    assert result.provider == "hunter_domain"
    assert result.confidence == 72.0


# ──────────────────────────── Snov ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_snov_person_happy_path(patch_settings: None) -> None:
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "snov-token", "expires_in": 3600})
    )
    respx.post(snov.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"email": "bryan@example.com", "probability": 82}},
        )
    )

    provider = SnovProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is not None
    assert result.email == "bryan@example.com"
    assert result.confidence == 82.0


@pytest.mark.asyncio
@respx.mock
async def test_snov_token_refresh_on_401(patch_settings: None) -> None:
    """After a 401 the provider re-hits OAuth and retries the search exactly once."""
    oauth_route = respx.post(snov.OAUTH_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "stale-token", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "fresh-token", "expires_in": 3600}),
        ]
    )
    search_route = respx.post(snov.EMAIL_FINDER_URL).mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json={"data": {"email": "bryan@example.com", "probability": 91}}),
        ]
    )

    provider = SnovProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is not None
    assert result.email == "bryan@example.com"
    assert result.confidence == 91.0
    assert oauth_route.call_count == 2
    assert search_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_snov_oauth_failure_returns_none(patch_settings: None) -> None:
    respx.post(snov.OAUTH_URL).mock(return_value=httpx.Response(500))

    provider = SnovProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is None


# ──────────────────────────── Orchestrator ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_orchestrator_apollo_hit_skips_hunter_and_snov(patch_settings: None) -> None:
    apollo_route = respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "bryan@example.com",
                    "email_status": "verified",
                    "phone_numbers": [],
                }
            },
        )
    )
    hunter_route = respx.get(hunter.EMAIL_FINDER_URL).mock(return_value=httpx.Response(200))
    snov_route = respx.post(snov.EMAIL_FINDER_URL).mock(return_value=httpx.Response(200))

    session = _FakeSession()
    entity = {
        "type": "person",
        "first_name": "Bryan",
        "last_name": "Halpert",
        "org_name": "Example LLC",
        "title": "CEO",
        "domain": "example.com",
    }
    row = await discover_contact(entity, bd_id=18344, session=session)

    assert row is not None
    assert row.email == "bryan@example.com"
    assert row.discovery_source == "apollo_match"
    assert row.discovery_confidence == Decimal("90.00")
    assert session.added == [row]
    assert apollo_route.called
    assert not hunter_route.called
    assert not snov_route.called


@pytest.mark.asyncio
@respx.mock
async def test_orchestrator_below_threshold_tries_next(patch_settings: None) -> None:
    """Apollo's 45 ('guessed') is below the 60 threshold, so Hunter should be tried."""
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "bryan@example.com",
                    "email_status": "guessed",
                    "phone_numbers": [],
                }
            },
        )
    )
    hunter_route = respx.get(hunter.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"email": "bryan.real@example.com", "score": 82}},
        )
    )

    session = _FakeSession()
    entity = {
        "type": "person",
        "first_name": "Bryan",
        "last_name": "Halpert",
        "org_name": "Example LLC",
        "title": "CEO",
        "domain": "example.com",
    }
    row = await discover_contact(entity, bd_id=18344, session=session)

    assert row is not None
    assert row.email == "bryan.real@example.com"
    assert row.discovery_source == "hunter"
    assert row.discovery_confidence == Decimal("82.00")
    assert hunter_route.called


@pytest.mark.asyncio
@respx.mock
async def test_orchestrator_cache_hit_skips_all_providers(patch_settings: None) -> None:
    cached = ExecutiveContact(
        id=99,
        bd_id=18344,
        name="Bryan Halpert",
        title="CEO",
        email="cached@example.com",
        phone=None,
        linkedin_url=None,
        source="apollo",
        discovery_source="apollo_match",
        discovery_confidence=Decimal("90.00"),
        enriched_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    apollo_route = respx.post(apollo_match.PEOPLE_MATCH_URL).mock(return_value=httpx.Response(200))

    session = _FakeSession(cached=cached)
    entity = {
        "type": "person",
        "first_name": "Bryan",
        "last_name": "Halpert",
        "org_name": "Example LLC",
        "title": "CEO",
        "domain": "example.com",
    }
    row = await discover_contact(entity, bd_id=18344, session=session)

    assert row is cached
    assert session.added == []
    assert not apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_orchestrator_all_providers_miss_returns_none(patch_settings: None) -> None:
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(return_value=httpx.Response(404))
    respx.get(hunter.EMAIL_FINDER_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
    )
    respx.post(snov.EMAIL_FINDER_URL).mock(return_value=httpx.Response(200, json={"data": {}}))

    session = _FakeSession()
    entity = {
        "type": "person",
        "first_name": "Ghost",
        "last_name": "User",
        "org_name": "Nowhere LLC",
        "title": "CEO",
        "domain": "nowhere.com",
    }
    row = await discover_contact(entity, bd_id=18344, session=session)

    assert row is None
    assert session.added == []


@pytest.mark.asyncio
@respx.mock
async def test_orchestrator_organization_uses_find_org(patch_settings: None) -> None:
    """Organisation-type entities must route to ``find_org`` (not ``find_person``).

    This also covers the case where the person endpoint would have 404'd:
    we should never hit it when the entity is an organisation.
    """
    person_route = respx.post(apollo_match.PEOPLE_MATCH_URL).mock(return_value=httpx.Response(200))
    respx.post(apollo_match.ORG_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "organization": {
                    "primary_phone": {"sanitized_number": "+15550199"},
                    "linkedin_url": "https://linkedin.com/company/example",
                }
            },
        )
    )
    # Apollo org confidence is 55, below the 60 threshold -> chain continues to Hunter.
    hunter_route = respx.get(hunter.DOMAIN_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "emails": [{"value": "info@example.com", "type": "generic", "confidence": 72}]
                }
            },
        )
    )

    session = _FakeSession()
    entity = {
        "type": "organization",
        "org_name": "Example LLC",
        "title": "SOLE MEMBER",
        "domain": "example.com",
    }
    row = await discover_contact(entity, bd_id=18344, session=session)

    assert row is not None
    assert row.email == "info@example.com"
    assert row.discovery_source == "hunter_domain"
    assert not person_route.called
    assert hunter_route.called
