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
