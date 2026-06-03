"""Tests for the Apollo phone-reveal opt-in on ``/people/match``.

When both ``apollo_webhook_secret`` and ``public_base_url`` are configured,
``ApolloMatchProvider.find_person`` must send ``reveal_phone_number=true``
plus an absolute ``webhook_url`` so Apollo can call us back with phones.
When either is unset, the provider must NOT send those fields — the
feature stays dormant and a probe of the unconfigured webhook URL would
404 anyway.

The provider also extracts ``person.id`` from the sync response into the
returned ``DiscoveryResult.apollo_person_id`` so the orchestrator can
persist it on the row for later webhook correlation.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.contact_discovery import apollo_match
from app.services.contact_discovery.apollo_match import ApolloMatchProvider


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-key")
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)
    # Default: webhook NOT configured. Individual tests override these.
    monkeypatch.setattr(settings, "apollo_webhook_secret", None)
    monkeypatch.setattr(settings, "public_base_url", None)


@pytest.mark.asyncio
@respx.mock
async def test_reveal_flag_not_sent_when_secret_missing(patch_settings: None) -> None:
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode()))
        return httpx.Response(200, json={"person": {"id": "p1"}})

    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(side_effect=_capture)

    provider = ApolloMatchProvider()
    await provider.find_person("Jane", "Doe", "Acme LLC", "acme.com")

    assert "reveal_phone_number" not in captured
    assert "webhook_url" not in captured


@pytest.mark.asyncio
@respx.mock
async def test_reveal_flag_not_sent_when_base_url_missing(
    monkeypatch: pytest.MonkeyPatch, patch_settings: None
) -> None:
    monkeypatch.setattr(settings, "apollo_webhook_secret", "supersecret")
    # public_base_url left as None — should still be dormant
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode()))
        return httpx.Response(200, json={"person": {"id": "p1"}})

    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(side_effect=_capture)

    provider = ApolloMatchProvider()
    await provider.find_person("Jane", "Doe", "Acme LLC", "acme.com")

    assert "reveal_phone_number" not in captured
    assert "webhook_url" not in captured


@pytest.mark.asyncio
@respx.mock
async def test_reveal_flag_and_url_sent_when_both_configured(
    monkeypatch: pytest.MonkeyPatch, patch_settings: None
) -> None:
    monkeypatch.setattr(settings, "apollo_webhook_secret", "supersecret")
    monkeypatch.setattr(
        settings, "public_base_url", "https://api.staging.example.com"
    )
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode()))
        return httpx.Response(200, json={"person": {"id": "p1"}})

    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(side_effect=_capture)

    provider = ApolloMatchProvider()
    await provider.find_person("Jane", "Doe", "Acme LLC", "acme.com")

    assert captured["reveal_phone_number"] is True
    assert (
        captured["webhook_url"]
        == "https://api.staging.example.com/api/v1/webhooks/apollo/supersecret/phone-reveal"
    )


@pytest.mark.asyncio
@respx.mock
async def test_base_url_trailing_slash_is_normalised(
    monkeypatch: pytest.MonkeyPatch, patch_settings: None
) -> None:
    """An ops mistake (trailing slash on PUBLIC_BASE_URL) must not produce
    ``https://api//api/v1/...`` — Apollo would reject the URL."""
    monkeypatch.setattr(settings, "apollo_webhook_secret", "supersecret")
    monkeypatch.setattr(
        settings, "public_base_url", "https://api.staging.example.com/"
    )
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode()))
        return httpx.Response(200, json={"person": {"id": "p1"}})

    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(side_effect=_capture)

    provider = ApolloMatchProvider()
    await provider.find_person("Jane", "Doe", "Acme LLC", "acme.com")

    assert (
        captured["webhook_url"]
        == "https://api.staging.example.com/api/v1/webhooks/apollo/supersecret/phone-reveal"
    )


@pytest.mark.asyncio
@respx.mock
async def test_apollo_person_id_extracted_from_sync_response(
    patch_settings: None,
) -> None:
    """The sync /people/match response's ``person.id`` lands on the
    returned DiscoveryResult so the orchestrator can persist it."""
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "id": "587cf802f65125cad923a266",
                    "email": "jane@acme.com",
                    "email_status": "verified",
                    "linkedin_url": "https://linkedin.com/in/jane",
                }
            },
        )
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("Jane", "Doe", "Acme LLC", "acme.com")

    assert result is not None
    assert result.apollo_person_id == "587cf802f65125cad923a266"


@pytest.mark.asyncio
@respx.mock
async def test_apollo_person_id_is_none_when_response_lacks_id(
    patch_settings: None,
) -> None:
    """A defensive path — Apollo's docs say ``id`` is always present, but if
    a future API change drops it, we must not blow up the INSERT."""
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "jane@acme.com",
                    "email_status": "verified",
                }
            },
        )
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("Jane", "Doe", "Acme LLC", "acme.com")

    assert result is not None
    assert result.apollo_person_id is None


@pytest.mark.asyncio
@respx.mock
async def test_apollo_person_id_truncated_to_64_chars(patch_settings: None) -> None:
    """The column is ``String(64)``; an unexpectedly-long id from Apollo
    must be truncated client-side so the INSERT doesn't fail."""
    long_id = "a" * 200
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"id": long_id, "email": "j@a.com", "email_status": "verified"}},
        )
    )

    provider = ApolloMatchProvider()
    result = await provider.find_person("Jane", "Doe", "Acme LLC", "acme.com")

    assert result is not None
    assert result.apollo_person_id is not None
    assert len(result.apollo_person_id) == 64
