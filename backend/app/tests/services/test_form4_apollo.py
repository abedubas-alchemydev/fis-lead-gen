"""Unit tests for ``form4_apollo.match_form4_person``.

The PR 4 change wires PDL as the primary provider with Apollo as the
fallback. These tests validate that:

- PDL hit short-circuits and Apollo is never called
- PDL miss / 5xx falls through to Apollo silently
- The Apollo-only path (no PDL key) matches pre-PR4 behavior exactly
- Single-word names skip PDL (no last name) and try Apollo with the raw name
- Both unconfigured returns matched=False without any HTTP call
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.contact_discovery import pdl
from app.services.form4_apollo import match_form4_person


_APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"


@pytest.fixture
def patch_pdl_and_apollo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", "test-pdl-key")
    monkeypatch.setattr(settings, "pdl_min_likelihood", 6)
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)


@pytest.fixture
def patch_apollo_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", None)
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")


@pytest.mark.asyncio
@respx.mock
async def test_pdl_primary_returns_match_skips_apollo(
    patch_pdl_and_apollo: None,
) -> None:
    """PDL hit -> phone+email returned, Apollo never called."""
    pdl_route = respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 8,
                "data": {
                    "work_email": "jane@example.com",
                    "emails": [
                        {"address": "jane@example.com", "type": "professional"}
                    ],
                    "mobile_phone": "+15551234567",
                },
            },
        )
    )
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(return_value=httpx.Response(200))

    match = await match_form4_person(
        full_name="DOE JANE",
        issuer_name="Example Corp",
    )

    assert match.matched is True
    assert match.email == "jane@example.com"
    assert match.phone == "+15551234567"
    assert pdl_route.called
    assert not apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_pdl_miss_falls_back_to_apollo(patch_pdl_and_apollo: None) -> None:
    """PDL 404 -> Apollo runs and provides the match."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "jane@example.com",
                    "phone_numbers": [{"sanitized_number": "+15559998888"}],
                }
            },
        )
    )

    match = await match_form4_person(
        full_name="DOE JANE",
        issuer_name="Example Corp",
    )

    assert match.matched is True
    assert match.email == "jane@example.com"
    assert match.phone == "+15559998888"
    assert apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_pdl_5xx_falls_back_to_apollo(patch_pdl_and_apollo: None) -> None:
    """PDL hard error -> chain continues silently to Apollo."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(503))
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "jane@example.com",
                    "phone_numbers": [],
                }
            },
        )
    )

    match = await match_form4_person(
        full_name="DOE JANE",
        issuer_name="Example Corp",
    )

    assert match.matched is True
    assert match.email == "jane@example.com"
    assert apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_both_miss_returns_no_match(patch_pdl_and_apollo: None) -> None:
    """PDL 404 + Apollo no-person -> matched=False."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": None}),
    )

    match = await match_form4_person(
        full_name="DOE JANE",
        issuer_name="Example Corp",
    )

    assert match.matched is False
    assert match.email is None
    assert match.phone is None


@pytest.mark.asyncio
@respx.mock
async def test_apollo_only_path_unchanged(patch_apollo_only: None) -> None:
    """When PDL isn't configured, behavior matches the pre-PR4 Apollo-only path."""
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "ernie@americanassets.com",
                    "phone_numbers": [],
                }
            },
        )
    )

    match = await match_form4_person(
        full_name="RADY ERNEST S",
        issuer_name="American Assets Trust, Inc.",
    )

    assert match.matched is True
    assert match.email == "ernie@americanassets.com"
    assert apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_single_word_name_skips_pdl_tries_apollo(
    patch_pdl_and_apollo: None,
) -> None:
    """Single-word name (no last_name) -> PDL skipped (find_person needs both),
    Apollo tried with raw ``name=`` payload."""
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"email": "ops@cascadeinvest.com", "phone_numbers": []}},
        )
    )

    match = await match_form4_person(
        full_name="CASCADE",
        issuer_name="Republic Services, Inc.",
    )

    assert apollo_route.called


@pytest.mark.asyncio
async def test_both_unconfigured_returns_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No PDL key + no Apollo key -> matched=False without HTTP."""
    monkeypatch.setattr(settings, "pdl_api_key", None)
    monkeypatch.setattr(settings, "apollo_api_key", None)

    match = await match_form4_person(
        full_name="DOE JANE",
        issuer_name="Example Corp",
    )

    assert match.matched is False
    assert match.email is None
    assert match.phone is None


@pytest.mark.asyncio
async def test_empty_name_returns_no_match() -> None:
    """Empty name -> matched=False without any HTTP."""
    match = await match_form4_person(
        full_name="",
        issuer_name="Example Corp",
    )

    assert match.matched is False
