"""Unit tests for the People Data Labs (PDL) contact-discovery provider.

Covers the parsing + confidence-mapping pieces in isolation. Mirrors the
respx-based pattern used by ``test_contact_discovery.py``:

- happy path: likelihood -> confidence mapping
- multi-value emails (work + personal classification)
- multi-value phones (mobile leads, deduped against phone_numbers[])
- domain vs org_name match anchor selection
- 404 (PDL's documented no-match), 5xx, network failure
- missing api_key short-circuits to None
- ``find_org`` always returns None (PDL is person-only in this chain)
- ``enrich_by_email`` helper (used by the find-phone path)
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.contact_discovery import pdl
from app.services.contact_discovery.base import EmailHit, PhoneHit
from app.services.contact_discovery.pdl import PdlProvider


@pytest.fixture
def patch_pdl_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", "test-pdl-key")
    monkeypatch.setattr(settings, "pdl_min_likelihood", 6)
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)


@pytest.mark.asyncio
@respx.mock
async def test_pdl_person_happy_path(patch_pdl_settings: None) -> None:
    """likelihood=8 -> confidence=80; scalars + lists populated."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 200,
                "likelihood": 8,
                "data": {
                    "full_name": "Jane Doe",
                    "linkedin_url": "linkedin.com/in/janedoe",
                    "work_email": "jane@acme.com",
                    "emails": [
                        {"address": "jane@acme.com", "type": "professional"},
                    ],
                    "phone_numbers": ["+15551112222"],
                    "mobile_phone": "+15551112222",
                },
            },
        )
    )

    result = await PdlProvider().find_person("Jane", "Doe", "Acme", "acme.com")

    assert result is not None
    assert result.email == "jane@acme.com"
    assert result.phone == "+15551112222"
    # linkedin returned without scheme; provider should prepend https://
    assert result.linkedin_url == "https://linkedin.com/in/janedoe"
    assert result.confidence == 80.0
    assert result.provider == "pdl"
    assert result.emails == [
        EmailHit(value="jane@acme.com", type="work", confidence=80.0, source="pdl"),
    ]
    # mobile leads; phone_numbers[] entry is deduped against mobile_phone
    assert result.phones == [
        PhoneHit(value="+15551112222", type="mobile", confidence=80.0, source="pdl"),
    ]


@pytest.mark.asyncio
@respx.mock
async def test_pdl_person_multi_email_with_mixed_types(patch_pdl_settings: None) -> None:
    """professional / personal / school types collapse to {work, personal}."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 7,
                "data": {
                    "emails": [
                        {"address": "jane@acme.com", "type": "professional"},
                        {"address": "jane@gmail.com", "type": "personal"},
                        {"address": "jane@uni.edu", "type": "school"},
                    ],
                },
            },
        )
    )

    result = await PdlProvider().find_person("Jane", "Doe", "Acme", "acme.com")

    assert result is not None
    assert [e.type for e in result.emails] == ["work", "personal", "work"]
    assert [e.value for e in result.emails] == [
        "jane@acme.com",
        "jane@gmail.com",
        "jane@uni.edu",
    ]
    # Scalar = first work email
    assert result.email == "jane@acme.com"


@pytest.mark.asyncio
@respx.mock
async def test_pdl_person_multi_phone_mobile_leads_deduped(patch_pdl_settings: None) -> None:
    """mobile_phone leads as type=mobile; remaining phone_numbers[] fall in as work."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 9,
                "data": {
                    "mobile_phone": "+15551112222",
                    "phone_numbers": ["+15551112222", "+15553334444"],
                },
            },
        )
    )

    result = await PdlProvider().find_person("Jane", "Doe", "Acme", "acme.com")

    assert result is not None
    assert [p.value for p in result.phones] == ["+15551112222", "+15553334444"]
    assert [p.type for p in result.phones] == ["mobile", "work"]
    assert result.phone == "+15551112222"  # mobile beats work for the scalar


@pytest.mark.asyncio
@respx.mock
async def test_pdl_person_personal_emails_and_work_email_backfill(
    patch_pdl_settings: None,
) -> None:
    """When emails[] is sparse, parse personal_emails + work_email too."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 7,
                "data": {
                    "work_email": "jane@acme.com",
                    "personal_emails": ["jane@gmail.com"],
                },
            },
        )
    )

    result = await PdlProvider().find_person("Jane", "Doe", "Acme", "acme.com")

    assert result is not None
    types = sorted(e.type for e in result.emails)
    assert types == ["personal", "work"]


@pytest.mark.asyncio
async def test_pdl_person_no_api_key_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", None)
    result = await PdlProvider().find_person("Jane", "Doe", "Acme", "acme.com")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_pdl_person_404_returns_none(patch_pdl_settings: None) -> None:
    """PDL's documented "no confident match" code."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))
    result = await PdlProvider().find_person("Jane", "Doe", "Acme", "acme.com")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_pdl_person_5xx_returns_none(patch_pdl_settings: None) -> None:
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(503))
    result = await PdlProvider().find_person("Jane", "Doe", "Acme", "acme.com")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_pdl_person_network_error_returns_none(patch_pdl_settings: None) -> None:
    respx.post(pdl.PERSON_ENRICH_URL).mock(side_effect=httpx.ConnectError("boom"))
    result = await PdlProvider().find_person("Jane", "Doe", "Acme", "acme.com")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_pdl_person_uses_domain_when_present(patch_pdl_settings: None) -> None:
    """Domain anchors the match (higher hit rate than org name per PDL docs)."""
    route = respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))
    await PdlProvider().find_person("Jane", "Doe", "Acme Corp", "acme.com")
    payload = json.loads(route.calls.last.request.content)
    assert payload["company"] == "acme.com"
    assert payload["min_likelihood"] == 6


@pytest.mark.asyncio
@respx.mock
async def test_pdl_person_falls_back_to_org_name_when_no_domain(
    patch_pdl_settings: None,
) -> None:
    route = respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404))
    await PdlProvider().find_person("Jane", "Doe", "Acme Corp", None)
    payload = json.loads(route.calls.last.request.content)
    assert payload["company"] == "Acme Corp"


@pytest.mark.asyncio
async def test_pdl_find_org_returns_none(patch_pdl_settings: None) -> None:
    """PDL is person-only here; org-level fallback stays with Apollo / Hunter."""
    result = await PdlProvider().find_org("Acme Corp", "acme.com")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_pdl_enrich_by_email_happy_path(patch_pdl_settings: None) -> None:
    """The find-phone helper re-anchors the same endpoint on email."""
    route = respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 8,
                "data": {
                    "emails": [{"address": "jane@acme.com", "type": "professional"}],
                    "mobile_phone": "+15551112222",
                },
            },
        )
    )

    result = await pdl.enrich_by_email("jane@acme.com")

    assert result is not None
    assert result.email == "jane@acme.com"
    assert result.phone == "+15551112222"
    payload = json.loads(route.calls.last.request.content)
    assert payload["email"] == "jane@acme.com"


@pytest.mark.asyncio
async def test_pdl_enrich_by_email_no_key_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", None)
    result = await pdl.enrich_by_email("jane@acme.com")
    assert result is None
