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
from app.services.form4_apollo import looks_like_entity, match_form4_person


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
    """PDL hit -> phone+email+linkedin returned, Apollo never called."""
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
                    # PDL returns bare URLs; the provider layer prefixes
                    # ``https://`` before handing the DiscoveryResult up.
                    "linkedin_url": "linkedin.com/in/janedoe",
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
    assert match.linkedin_url == "https://linkedin.com/in/janedoe"
    assert pdl_route.called
    assert not apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_pdl_miss_falls_back_to_apollo(patch_pdl_and_apollo: None) -> None:
    """PDL 404 -> Apollo runs and provides the match (with LinkedIn)."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "jane@example.com",
                    "phone_numbers": [{"sanitized_number": "+15559998888"}],
                    "linkedin_url": "https://www.linkedin.com/in/janedoe",
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
    assert match.linkedin_url == "https://www.linkedin.com/in/janedoe"
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


@pytest.mark.parametrize(
    "name",
    [
        "SACHEM HEAD GP LLC",
        "VANGUARD GROUP INC",
        "BERKSHIRE HATHAWAY INC.",
        "BLACKROCK HOLDINGS LLC",
        "PERSHING SQUARE CAPITAL MANAGEMENT GP",
        "JANE SMITH FOUNDATION TRUST",
        "Renaissance Technologies LLC",
    ],
)
def test_looks_like_entity_catches_common_suffixes(name: str) -> None:
    """Entity-name regex catches the suffixes Form 4 10%-holders use."""
    assert looks_like_entity(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "COOK TIMOTHY D",
        "RADY ERNEST S",
        "DOE JANE",
        "GATES WILLIAM H III",
    ],
)
def test_looks_like_entity_passes_natural_persons(name: str) -> None:
    """Natural-person names are not falsely flagged as entities."""
    assert looks_like_entity(name) is False


@pytest.mark.asyncio
@respx.mock
async def test_entity_filer_skips_both_providers(patch_pdl_and_apollo: None) -> None:
    """Entity name short-circuits before PDL or Apollo is called.

    Routes are mocked to fail loudly so any call would show up as a
    request-count mismatch rather than a silent miss.
    """
    pdl_route = respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(500))
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(return_value=httpx.Response(500))

    match = await match_form4_person(
        full_name="SACHEM HEAD GP LLC",
        issuer_name="Apple Inc.",
    )

    assert match.matched is False
    assert match.skip_reason == "entity_filer"
    assert match.phone is None
    assert match.email is None
    assert match.linkedin_url is None
    assert not pdl_route.called
    assert not apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_person_name_still_calls_providers(patch_pdl_and_apollo: None) -> None:
    """Non-entity names continue to flow into the PDL/Apollo chain.

    Guard against an overly-aggressive entity regex accidentally
    short-circuiting real persons.
    """
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"email": "tim@apple.com", "phone_numbers": []}},
        )
    )

    match = await match_form4_person(
        full_name="COOK TIMOTHY D",
        issuer_name="Apple Inc.",
    )

    assert apollo_route.called
    assert match.skip_reason is None
    assert match.matched is True
    assert match.email == "tim@apple.com"


@pytest.mark.asyncio
@respx.mock
async def test_pdl_match_without_phone_falls_through_to_apollo(
    patch_pdl_and_apollo: None,
) -> None:
    """PDL matched (LinkedIn, no phone) -> Apollo IS now called to chase the
    phone, and PDL's LinkedIn is preserved.

    PDL almost never returns phones for Form 4 insiders, so a PDL match that
    lacks a phone no longer short-circuits: we fall through to Apollo (which,
    when the webhook is configured, requests an async phone-reveal). PDL's
    higher-quality email/LinkedIn still win the merge.
    """
    respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 8,
                "data": {"linkedin_url": "linkedin.com/in/janedoe"},
            },
        )
    )
    # Apollo returns a person record (id only here) — no sync phone.
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200, json={"person": {"id": "abc123", "phone_numbers": []}}
        )
    )

    match = await match_form4_person(
        full_name="DOE JANE",
        issuer_name="Example Corp",
    )

    assert match.matched is True
    assert match.phone is None  # arrives async via the reveal webhook
    assert match.linkedin_url == "https://linkedin.com/in/janedoe"  # PDL's, preserved
    assert match.apollo_person_id == "abc123"
    assert apollo_route.called  # changed: PDL-no-phone now chases Apollo


@pytest.mark.asyncio
@respx.mock
async def test_pdl_with_phone_short_circuits_apollo(
    patch_pdl_and_apollo: None,
) -> None:
    """PDL returns a phone -> done, Apollo not called (no reveal needed)."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 9,
                "data": {
                    "mobile_phone": "+15551112222",
                    "linkedin_url": "linkedin.com/in/janedoe",
                },
            },
        )
    )
    apollo_route = respx.post(_APOLLO_MATCH_URL).mock(return_value=httpx.Response(200))

    match = await match_form4_person(full_name="DOE JANE", issuer_name="Example Corp")

    assert match.phone == "+15551112222"
    assert match.apollo_person_id is None
    assert not apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_apollo_requests_phone_reveal_when_webhook_configured(
    patch_pdl_and_apollo: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With apollo_webhook_secret + public_base_url set, the Apollo call carries
    reveal_phone_number + the webhook_url; person.id is captured."""
    monkeypatch.setattr(settings, "apollo_webhook_secret", "s3cret")
    monkeypatch.setattr(settings, "public_base_url", "https://staging-dox.example/api/backend")
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))

    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured.update(_json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={"person": {"id": "pid-789", "email": "j@x.com", "phone_numbers": []}},
        )

    respx.post(_APOLLO_MATCH_URL).mock(side_effect=_capture)

    match = await match_form4_person(full_name="DOE JANE", issuer_name="Example Corp")

    assert captured.get("reveal_phone_number") is True
    assert captured.get("webhook_url") == (
        "https://staging-dox.example/api/backend/api/v1/webhooks/apollo/s3cret/phone-reveal"
    )
    assert match.apollo_person_id == "pid-789"
    assert match.email == "j@x.com"


@pytest.mark.asyncio
@respx.mock
async def test_apollo_no_reveal_when_webhook_unconfigured(
    patch_pdl_and_apollo: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the webhook secret / base url, no reveal flag is sent (dormant,
    same as the pre-reveal behavior)."""
    monkeypatch.setattr(settings, "apollo_webhook_secret", None)
    monkeypatch.setattr(settings, "public_base_url", None)
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))

    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured.update(_json.loads(request.content.decode()))
        return httpx.Response(200, json={"person": {"email": "j@x.com", "phone_numbers": []}})

    respx.post(_APOLLO_MATCH_URL).mock(side_effect=_capture)

    await match_form4_person(full_name="DOE JANE", issuer_name="Example Corp")

    assert "reveal_phone_number" not in captured
    assert "webhook_url" not in captured


@pytest.mark.asyncio
@respx.mock
async def test_apollo_response_without_linkedin_returns_none(
    patch_apollo_only: None,
) -> None:
    """Apollo person hit that omits ``linkedin_url`` -> ``match.linkedin_url is None``.

    Older Apollo accounts and free-tier responses sometimes drop the
    field entirely; the service must default to None rather than KeyError
    or treat the missing key as a falsy match.
    """
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "tim@apple.com",
                    "phone_numbers": [{"sanitized_number": "+15551234567"}],
                }
            },
        )
    )

    match = await match_form4_person(
        full_name="COOK TIMOTHY D",
        issuer_name="Apple Inc.",
    )

    assert match.matched is True
    assert match.email == "tim@apple.com"
    assert match.phone == "+15551234567"
    assert match.linkedin_url is None
