"""Unit tests for ``apollo_enrichment.enrich_discovered_email`` phone capture.

Covers the 20260515_0041 migration: Apollo's ``/people/match`` returns a
``phone_numbers`` array and we persist the first usable number into
``DiscoveredEmail.enriched_phone``. Prior to that fix, the entire array
was discarded.

All HTTP via respx, no real Apollo calls. Session is hand-rolled to mirror
the slice of ``AsyncSession`` that ``apollo_enrichment`` actually touches
(``get``, ``commit``, ``refresh``).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.core.config import settings
from app.models.discovered_email import DiscoveredEmail
from app.services.email_extractor import apollo_enrichment


_APOLLO_MATCH_URL = apollo_enrichment.APOLLO_MATCH_URL


class _FakeSession:
    """AsyncSession stand-in that returns a single pre-seeded DiscoveredEmail."""

    def __init__(self, row: DiscoveredEmail) -> None:
        self._row = row
        self.commits: int = 0
        self.refreshes: int = 0

    async def get(self, _model: Any, _pk: Any) -> DiscoveredEmail:
        return self._row

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _row: Any) -> None:
        self.refreshes += 1


def _seed_row() -> DiscoveredEmail:
    """A bare DiscoveredEmail with just the fields the enricher reads."""
    row = DiscoveredEmail()
    row.id = 1
    row.email = "jane@example.com"
    return row


@pytest.fixture(autouse=True)
def _isolate_to_apollo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the chain to Apollo only.

    The enricher now walks a provider chain (Apollo -> Hunter -> Snov -> web
    scraper); these tests exercise the Apollo path, so clear the other
    providers' credentials/flags up front. Without this a local ``.env`` that
    happens to carry Hunter/Snov keys would pull them into the chain and make
    real (respx-blocked) calls.
    """
    monkeypatch.setattr(settings, "hunter_api_key", "", raising=False)
    monkeypatch.setattr(settings, "snov_client_id", "", raising=False)
    monkeypatch.setattr(settings, "snov_client_secret", "", raising=False)
    monkeypatch.setattr(settings, "web_fallback_enabled", False, raising=False)


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")


@pytest.mark.asyncio
@respx.mock
async def test_enrich_extracts_phone_from_apollo_response(patch_settings: None) -> None:
    """Apollo returns ``phone_numbers[0].sanitized_number`` — that string lands
    on ``enriched_phone`` and the row is marked enriched."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "name": "Jane Doe",
                    "title": "Compliance Officer",
                    "phone_numbers": [{"sanitized_number": "+15551234567"}],
                }
            },
        )
    )

    row = _seed_row()
    session = _FakeSession(row)

    result = await apollo_enrichment.enrich_discovered_email(session, 1)

    assert result.enriched_phone == "+15551234567"
    assert result.enrichment_status == "enriched"
    assert result.enriched_name == "Jane Doe"


@pytest.mark.asyncio
@respx.mock
async def test_enrich_handles_missing_phone_numbers(patch_settings: None) -> None:
    """If the response has no ``phone_numbers`` key, ``enriched_phone`` is null
    but other fields still populate and status is still ``enriched``."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"name": "Jane Doe", "title": "Compliance Officer"}},
        )
    )

    row = _seed_row()
    session = _FakeSession(row)

    result = await apollo_enrichment.enrich_discovered_email(session, 1)

    assert result.enriched_phone is None
    assert result.enriched_name == "Jane Doe"
    assert result.enrichment_status == "enriched"


@pytest.mark.asyncio
@respx.mock
async def test_enrich_handles_empty_phone_numbers_array(patch_settings: None) -> None:
    """Empty list is the more common Apollo shape for no-phone matches."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"name": "Jane Doe", "phone_numbers": []}},
        )
    )

    row = _seed_row()
    session = _FakeSession(row)

    result = await apollo_enrichment.enrich_discovered_email(session, 1)

    assert result.enriched_phone is None
    assert result.enrichment_status == "enriched"


@pytest.mark.asyncio
@respx.mock
async def test_enrich_falls_back_to_raw_number_when_no_sanitized(patch_settings: None) -> None:
    """Some Apollo responses lack ``sanitized_number``; fall back to
    ``raw_number`` so we don't drop a usable phone on the floor."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "name": "Jane Doe",
                    "phone_numbers": [{"raw_number": "(555) 123-4567"}],
                }
            },
        )
    )

    row = _seed_row()
    session = _FakeSession(row)

    result = await apollo_enrichment.enrich_discovered_email(session, 1)

    assert result.enriched_phone == "(555) 123-4567"
    assert result.enrichment_status == "enriched"


@pytest.mark.asyncio
@respx.mock
async def test_enrich_no_match_leaves_phone_null(patch_settings: None) -> None:
    """When Apollo returns 200 with no ``person``, status is ``no_match`` and
    no fields (including phone) are written."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json={})
    )

    row = _seed_row()
    session = _FakeSession(row)

    result = await apollo_enrichment.enrich_discovered_email(session, 1)

    assert result.enriched_phone is None
    assert result.enrichment_status == "no_match"


# ──────────────────── Reveal payload + email/person-id capture ────────────────────


@pytest.fixture
def patch_reveal_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apollo key + the async phone-reveal flow both configured."""
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "apollo_webhook_secret", "sekret")
    monkeypatch.setattr(
        settings, "public_base_url", "https://app.example.com/api/backend"
    )


@pytest.mark.asyncio
@respx.mock
async def test_enrich_requests_personal_email_reveal(patch_settings: None) -> None:
    """The match payload opts into reveal_personal_emails so Apollo surfaces a
    usable email even when the discovered address is a role inbox."""
    route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": {"name": "Jane Doe"}})
    )

    await apollo_enrichment.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    sent = json.loads(route.calls.last.request.content)
    assert sent["email"] == "jane@example.com"
    assert sent["reveal_personal_emails"] is True


@pytest.mark.asyncio
@respx.mock
async def test_enrich_omits_phone_reveal_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no webhook secret / base URL the request must not ask for a phone
    reveal -- no webhook would ever arrive, so it'd just burn an Apollo credit."""
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "apollo_webhook_secret", None)
    monkeypatch.setattr(settings, "public_base_url", None)
    route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": {"name": "Jane Doe"}})
    )

    await apollo_enrichment.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    sent = json.loads(route.calls.last.request.content)
    assert "reveal_phone_number" not in sent
    assert "webhook_url" not in sent


@pytest.mark.asyncio
@respx.mock
async def test_enrich_requests_phone_reveal_when_configured(
    patch_reveal_configured: None,
) -> None:
    """When the reveal flow is configured the payload carries
    reveal_phone_number + a webhook_url embedding the shared secret."""
    route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200, json={"person": {"id": "p1", "name": "Jane Doe"}}
        )
    )

    await apollo_enrichment.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    sent = json.loads(route.calls.last.request.content)
    assert sent["reveal_phone_number"] is True
    assert sent["webhook_url"] == (
        "https://app.example.com/api/backend/api/v1/webhooks/apollo/sekret/phone-reveal"
    )


@pytest.mark.asyncio
@respx.mock
async def test_enrich_captures_apollo_person_id(patch_settings: None) -> None:
    """person.id is stamped so the async phone-reveal webhook can correlate the
    revealed number back to this row."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"id": "587cf802f65125cad923a266", "name": "Jane Doe"}},
        )
    )

    result = await apollo_enrichment.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert result.apollo_person_id == "587cf802f65125cad923a266"


@pytest.mark.asyncio
@respx.mock
async def test_enrich_prefers_personal_email_over_work_email(patch_settings: None) -> None:
    """A revealed personal email wins over the matched work email (which is
    usually just the discovered address echoed back)."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "name": "Jane Doe",
                    "email": "jane@example.com",
                    "personal_emails": ["jane.personal@gmail.com"],
                }
            },
        )
    )

    result = await apollo_enrichment.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert result.enriched_email == "jane.personal@gmail.com"


@pytest.mark.asyncio
@respx.mock
async def test_enrich_falls_back_to_work_email_when_no_personal(patch_settings: None) -> None:
    """No personal_emails → store the matched work email on enriched_email."""
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200, json={"person": {"name": "Jane Doe", "email": "jane.doe@firm.com"}}
        )
    )

    result = await apollo_enrichment.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert result.enriched_email == "jane.doe@firm.com"
