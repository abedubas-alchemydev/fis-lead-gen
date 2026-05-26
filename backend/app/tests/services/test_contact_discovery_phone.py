"""Per-provider phone-propagation lock-in tests.

The existing ``test_contact_discovery`` suite covers Apollo's phone path
(/people/match and /organizations/enrich both pass through phone). This
file fills the remaining gaps so a regression in any provider is loud:

* Hunter: response carries ``phone_number`` and we must surface it.
* Snov: by design, the Snov endpoints we hit don't return phone — we
  lock that "always None" contract so flipping it to a populated string
  is caught and triaged before shipping.
* End-to-end: ``DiscoveryResult.phone`` flows through the shared
  ``first_apollo_phone`` helper exactly the same way it does in the
  email-extractor enrichment flow.

All HTTP via respx.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.contact_discovery import apollo_match, hunter, snov
from app.services.contact_discovery._shared import first_apollo_phone
from app.services.contact_discovery.hunter import HunterProvider
from app.services.contact_discovery.snov import SnovProvider


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "hunter_api_key", "test-hunter-key")
    monkeypatch.setattr(settings, "snov_client_id", "test-snov-id")
    monkeypatch.setattr(settings, "snov_client_secret", "test-snov-secret")
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)


@pytest.fixture(autouse=True)
def _reset_snov_token() -> None:
    snov._reset_token_cache_for_tests()


# ──────────────────────────── Hunter ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_hunter_person_phone_surfaces_on_discovery_result(patch_settings: None) -> None:
    """Lock-in: Hunter's ``phone_number`` field MUST land on
    ``DiscoveryResult.phone`` so downstream ``ExecutiveContact.phone``
    gets populated."""
    respx.get(hunter.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "email": "bryan@example.com",
                    "score": 87,
                    "phone_number": "+15550100",
                }
            },
        )
    )

    provider = HunterProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is not None
    assert result.phone == "+15550100"


@pytest.mark.asyncio
@respx.mock
async def test_hunter_person_missing_phone_returns_none(patch_settings: None) -> None:
    """If Hunter omits ``phone_number`` the result's phone is None — never an
    empty string or the literal "null"."""
    respx.get(hunter.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"email": "bryan@example.com", "score": 87}},
        )
    )

    provider = HunterProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is not None
    assert result.phone is None


# ──────────────────────────── Snov ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_snov_person_phone_is_always_none_by_design(patch_settings: None) -> None:
    """Snov's /emails endpoints don't expose phone. Lock this so if a future
    plan/endpoint changes that, the test fails loudly and we wire it up
    instead of silently dropping data."""
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(snov.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"email": "bryan@example.com", "probability": 80}},
        )
    )

    provider = SnovProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is not None
    assert result.email == "bryan@example.com"
    assert result.phone is None


# ──────────────────────────── Shared Apollo helper ────────────────────────────


def test_first_apollo_phone_prefers_sanitized() -> None:
    assert first_apollo_phone(
        [{"sanitized_number": "+15551234567", "raw_number": "(555) 123-4567"}]
    ) == "+15551234567"


def test_first_apollo_phone_falls_back_to_raw_number() -> None:
    assert first_apollo_phone([{"raw_number": "(555) 123-4567"}]) == "(555) 123-4567"


def test_first_apollo_phone_handles_bare_string() -> None:
    assert first_apollo_phone(["+15550000000"]) == "+15550000000"


def test_first_apollo_phone_handles_empty_and_none() -> None:
    assert first_apollo_phone(None) is None
    assert first_apollo_phone([]) is None
    assert first_apollo_phone([{}]) is None
    assert first_apollo_phone([{"sanitized_number": ""}]) is None


@pytest.mark.asyncio
@respx.mock
async def test_apollo_match_uses_shared_phone_helper(patch_settings: None) -> None:
    """End-to-end: a ``raw_number`` only response (no ``sanitized_number``)
    must still surface a phone, proving ``apollo_match`` is going through
    the shared helper rather than reading the dict directly."""
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "bryan@example.com",
                    "email_status": "verified",
                    "phone_numbers": [{"raw_number": "(555) 999-0000"}],
                }
            },
        )
    )

    provider = apollo_match.ApolloMatchProvider()
    result = await provider.find_person("Bryan", "Halpert", "Example LLC", "example.com")

    assert result is not None
    assert result.phone == "(555) 999-0000"
