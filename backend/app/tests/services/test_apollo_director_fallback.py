"""Tests for the Apollo director LinkedIn fall-through.

When `apollo_director_linkedin_fallback` is on and a confident /people/match
comes back with no LinkedIn (the outside-director signature — Apollo
recognised them at the queried firm but returned the firm-projected record
with no LinkedIn), the provider fires a SECOND /people/match with name only
to surface the person's primary-employer record, and grafts that record's
LinkedIn onto the result — but only when Apollo's person `id` confirms it's
the same human.

respx distinguishes the two requests by body: the firm-anchored query
carries `organization_name`, the fallback query does not.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.contact_discovery import apollo_match
from app.services.contact_discovery.apollo_match import (
    ApolloMatchProvider,
    _is_same_person,
)


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-key")
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)
    monkeypatch.setattr(settings, "contact_discovery_min_confidence", 60.0)
    monkeypatch.setattr(settings, "apollo_webhook_secret", None)
    monkeypatch.setattr(settings, "public_base_url", None)
    monkeypatch.setattr(settings, "apollo_director_linkedin_fallback", True)


def _route_by_org(firm_resp: dict, fallback_resp: dict) -> list[dict]:
    """Mock /people/match to return firm_resp when the request body has
    organization_name, fallback_resp otherwise. Returns the list of captured
    request bodies for assertions."""
    captured: list[dict] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        captured.append(body)
        if "organization_name" in body:
            return httpx.Response(200, json=firm_resp)
        return httpx.Response(200, json=fallback_resp)

    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(side_effect=_handler)
    return captured


# ──────────────────────────── _is_same_person ────────────────────────────


def test_is_same_person_matches_on_apollo_id() -> None:
    assert _is_same_person({"id": "abc"}, {"id": "abc"}) is True


def test_is_same_person_rejects_different_apollo_id() -> None:
    assert _is_same_person({"id": "abc"}, {"id": "xyz"}) is False


def test_is_same_person_falls_back_to_name_when_no_id() -> None:
    assert _is_same_person(
        {"first_name": "Sarah", "last_name": "Raskin"},
        {"first_name": "sarah", "last_name": "RASKIN"},
    ) is True


def test_is_same_person_name_fallback_rejects_mismatch() -> None:
    assert _is_same_person(
        {"first_name": "Sarah", "last_name": "Raskin"},
        {"first_name": "Bob", "last_name": "Raskin"},
    ) is False


# ──────────────────────────── Fallback behaviour ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_fallback_recovers_linkedin_on_id_match(patch_settings: None) -> None:
    captured = _route_by_org(
        firm_resp={
            "person": {
                "id": "director-1",
                "email": "sarah.raskin@vanguard.com",
                # Real Vanguard directors come back verified (conf 90) — that's
                # why they clear the chain's min_confidence and land in the DB
                # in the first place. The firm-projected record just lacks the
                # LinkedIn URL.
                "email_status": "verified",
                "linkedin_url": None,
            }
        },
        fallback_resp={
            "person": {
                "id": "director-1",
                "email": "raskin@duke.edu",
                "linkedin_url": "https://linkedin.com/in/sarah-raskin",
            }
        },
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("Sarah", "Raskin", "Vanguard Group INC", "vanguard.com")

    assert result is not None
    assert result.linkedin_url == "https://linkedin.com/in/sarah-raskin"
    # Two requests fired: firm-anchored then name-only fallback.
    assert len(captured) == 2
    assert "organization_name" in captured[0]
    assert "organization_name" not in captured[1]


@pytest.mark.asyncio
@respx.mock
async def test_fallback_rejected_on_id_mismatch(patch_settings: None) -> None:
    """A same-name stranger (different Apollo id) must not graft their
    LinkedIn onto the row."""
    _route_by_org(
        firm_resp={
            "person": {
                "id": "director-1",
                "email": "j.smith@vanguard.com",
                "email_status": "verified",
                "linkedin_url": None,
            }
        },
        fallback_resp={
            "person": {
                "id": "someone-else-2",
                "linkedin_url": "https://linkedin.com/in/wrong-john-smith",
            }
        },
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("John", "Smith", "Vanguard Group INC", "vanguard.com")

    assert result is not None
    assert result.linkedin_url is None


@pytest.mark.asyncio
@respx.mock
async def test_no_fallback_when_setting_off(
    monkeypatch: pytest.MonkeyPatch, patch_settings: None
) -> None:
    monkeypatch.setattr(settings, "apollo_director_linkedin_fallback", False)
    captured = _route_by_org(
        firm_resp={"person": {"id": "d1", "email": "x@vanguard.com", "email_status": "verified", "linkedin_url": None}},
        fallback_resp={"person": {"id": "d1", "linkedin_url": "https://linkedin.com/in/x"}},
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("X", "Y", "Vanguard Group INC", "vanguard.com")

    assert result is not None
    assert result.linkedin_url is None
    assert len(captured) == 1  # no fallback request


@pytest.mark.asyncio
@respx.mock
async def test_no_fallback_when_firm_record_already_has_linkedin(
    patch_settings: None,
) -> None:
    captured = _route_by_org(
        firm_resp={
            "person": {
                "id": "emp-1",
                "email": "md@vanguard.com",
                "email_status": "verified",
                "linkedin_url": "https://linkedin.com/in/md",
            }
        },
        fallback_resp={"person": {"id": "emp-1", "linkedin_url": "https://linkedin.com/in/other"}},
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("Managing", "Director", "Vanguard Group INC", "vanguard.com")

    assert result is not None
    assert result.linkedin_url == "https://linkedin.com/in/md"
    assert len(captured) == 1  # already had LinkedIn — no fallback


@pytest.mark.asyncio
@respx.mock
async def test_no_fallback_when_match_below_confidence(patch_settings: None) -> None:
    """A weak match (no email -> confidence 0) shouldn't trigger a second
    paid call — we don't double-down on a low-confidence hit."""
    captured = _route_by_org(
        firm_resp={"person": {"id": "weak-1", "email": None, "linkedin_url": None}},
        fallback_resp={"person": {"id": "weak-1", "linkedin_url": "https://linkedin.com/in/weak"}},
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("Weak", "Match", "Vanguard Group INC", "vanguard.com")

    assert result is not None
    assert result.linkedin_url is None
    assert len(captured) == 1


@pytest.mark.asyncio
@respx.mock
async def test_fallback_returns_nothing_is_graceful(patch_settings: None) -> None:
    """Fallback /people/match finds no person -> original result unchanged."""
    _route_by_org(
        firm_resp={"person": {"id": "d1", "email": "x@vanguard.com", "email_status": "verified", "linkedin_url": None}},
        fallback_resp={"person": None},
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("Lonely", "Director", "Vanguard Group INC", "vanguard.com")

    assert result is not None
    assert result.linkedin_url is None
    # The firm-record's other fields still come through.
    assert result.email == "x@vanguard.com"
