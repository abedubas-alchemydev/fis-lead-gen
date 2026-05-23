"""Orchestrator chain tests for the People Data Labs provider.

Validates that PDL slots into the apollo/hunter/snov fan-out when
``settings.contact_discovery_chain`` starts with ``pdl``:

- PDL wins -> the row's ``emails`` / ``phones`` JSONB columns reflect
  PDL's multi-value lists, merged with anything the other providers
  also returned. Under the merge model every other provider is still
  invoked in parallel.
- PDL below the confidence threshold -> PDL doesn't contribute; Apollo's
  contribution wins (other providers fanned out in parallel).
- PDL hard error -> PDL doesn't contribute; Apollo's contribution wins.
- ``find_org`` returns None instantly for organisation entities, so PDL
  makes no HTTP call and Apollo's ``find_org`` handles org-level hits.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.core.config import settings
from app.models.executive_contact import ExecutiveContact
from app.models.investor_contact import InvestorContact
from app.services.contact_discovery import apollo_match, hunter, pdl, snov
from app.services.contact_discovery.orchestrator import (
    discover_contact,
    discover_investor_contact,
)


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", "test-pdl-key")
    monkeypatch.setattr(settings, "pdl_min_likelihood", 6)
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "hunter_api_key", "test-hunter-key")
    monkeypatch.setattr(settings, "snov_client_id", "test-snov-id")
    monkeypatch.setattr(settings, "snov_client_secret", "test-snov-secret")
    monkeypatch.setattr(
        settings, "contact_discovery_chain", "pdl,apollo_match,hunter,snov"
    )
    monkeypatch.setattr(settings, "contact_discovery_min_confidence", 60.0)
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)


@pytest.fixture(autouse=True)
def _reset_snov_token() -> None:
    snov._reset_token_cache_for_tests()


class _FakeResult:
    def __init__(self, row: ExecutiveContact | None) -> None:
        self._row = row

    def scalars(self) -> "_FakeResult":
        return self

    def first(self) -> ExecutiveContact | None:
        return self._row


class _FakeSession:
    def __init__(self, cached: ExecutiveContact | None = None) -> None:
        self.cached = cached
        self.added: list[ExecutiveContact] = []

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self.cached)

    def add(self, row: ExecutiveContact) -> None:
        self.added.append(row)


def _person_entity() -> dict[str, Any]:
    return {
        "type": "person",
        "first_name": "Jane",
        "last_name": "Doe",
        "org_name": "Acme Corp",
        "title": "CFO",
        "domain": "acme.com",
    }


def _org_entity() -> dict[str, Any]:
    return {
        "type": "organization",
        "first_name": None,
        "last_name": None,
        "org_name": "Acme Corp",
        "title": None,
        "domain": "acme.com",
    }


@pytest.mark.asyncio
@respx.mock
async def test_pdl_hit_still_calls_apollo_hunter_snov(patch_settings: None) -> None:
    """All four providers fan out in parallel. PDL is the highest-confidence
    contributor here (likelihood=8 -> 80) so it wins the ``discovery_source``
    label and the merged ``emails`` / ``phones`` lists mirror PDL's
    multi-value payload (the other providers return None on the no-data
    responses below)."""
    pdl_route = respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 8,
                "data": {
                    "emails": [
                        {"address": "jane@acme.com", "type": "professional"},
                        {"address": "jane@gmail.com", "type": "personal"},
                    ],
                    "mobile_phone": "+15551112222",
                    "phone_numbers": ["+15551112222", "+15553334444"],
                },
            },
        )
    )
    apollo_route = respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": None})
    )
    hunter_route = respx.get(hunter.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    snov_route = respx.post(snov.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(200, json={"data": {}})
    )

    session = _FakeSession()
    row = await discover_contact(_person_entity(), bd_id=1, session=session)

    assert row is not None
    assert row.discovery_source == "pdl"
    assert row.email == "jane@acme.com"
    assert row.phone == "+15551112222"
    assert row.emails is not None and len(row.emails) == 2
    assert row.phones is not None
    assert [p["type"] for p in row.phones] == ["mobile", "work"]
    # Merge model: every chain provider is fanned out in parallel.
    assert pdl_route.called
    assert apollo_route.called
    assert hunter_route.called
    assert snov_route.called


@pytest.mark.asyncio
@respx.mock
async def test_pdl_below_threshold_does_not_contribute_to_merge(
    patch_settings: None,
) -> None:
    """likelihood=5 -> confidence=50 < 60 threshold -> PDL doesn't
    contribute to the merge. Apollo's verified-90 wins; Hunter and Snov
    are also fanned out in parallel but return nothing here."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 5,
                "data": {
                    "emails": [{"address": "jane@acme.com", "type": "professional"}],
                },
            },
        )
    )
    apollo_route = respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "jane@acme.com",
                    "email_status": "verified",
                    "phone_numbers": [],
                }
            },
        )
    )
    respx.get(hunter.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(snov.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(200, json={"data": {}})
    )

    session = _FakeSession()
    row = await discover_contact(_person_entity(), bd_id=1, session=session)

    assert row is not None
    assert row.discovery_source == "apollo_match"
    assert apollo_route.called
    # Apollo only contributed a scalar email and its scalar phone was None.
    # The merge lifts that single email into a 1-element list, so the JSONB
    # column is populated under the merge model (even though Apollo doesn't
    # populate ``emails``/``phones`` natively).
    assert row.emails is not None and len(row.emails) == 1
    assert row.emails[0]["value"] == "jane@acme.com"
    assert row.emails[0]["source"] == "apollo_match"
    # No phone contributed by anyone; phones column stays NULL.
    assert row.phones is None


@pytest.mark.asyncio
@respx.mock
async def test_pdl_5xx_does_not_contribute_to_merge(patch_settings: None) -> None:
    """PDL transient error -> PDL contributes nothing to the merge. Apollo's
    verified-90 wins (graceful degradation under the merge model)."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(return_value=httpx.Response(500))
    apollo_route = respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "jane@acme.com",
                    "email_status": "verified",
                    "phone_numbers": [],
                }
            },
        )
    )
    respx.get(hunter.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(snov.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(200, json={"data": {}})
    )

    session = _FakeSession()
    row = await discover_contact(_person_entity(), bd_id=1, session=session)

    assert row is not None
    assert row.discovery_source == "apollo_match"
    assert apollo_route.called


@pytest.mark.asyncio
@respx.mock
async def test_pdl_skipped_for_organization_entity(
    patch_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PDL.find_org returns None without HTTP -> Apollo's find_org carries
    the merge for org-level hits. Hunter and Snov's domain endpoints are
    also fanned out in parallel under the merge model."""
    # apollo_org returns confidence=55 which is below the default 60 floor;
    # drop the threshold so this test exercises the org-level merge.
    monkeypatch.setattr(settings, "contact_discovery_min_confidence", 50.0)
    pdl_route = respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(200)
    )
    apollo_org_route = respx.post(apollo_match.ORG_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "organization": {
                    "primary_phone": {"sanitized_number": "+15550000000"},
                    "linkedin_url": "https://linkedin.com/company/acme",
                }
            },
        )
    )
    respx.get(hunter.DOMAIN_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"data": {"emails": []}})
    )
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(snov.DOMAIN_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"emails": []})
    )

    session = _FakeSession()
    row = await discover_contact(_org_entity(), bd_id=1, session=session)

    assert row is not None
    assert row.discovery_source == "apollo_org"
    assert apollo_org_route.called
    # PDL's find_org short-circuits to None without any HTTP call.
    assert not pdl_route.called


@pytest.mark.asyncio
@respx.mock
async def test_pdl_hit_persists_investor_contact(patch_settings: None) -> None:
    """The investor sibling (discover_investor_contact) shares the same chain
    walk and persists an InvestorContact row with the JSONB arrays.

    Under the merge model the other providers are still fanned out in
    parallel but contribute nothing (no-data responses below), so the
    persisted row mirrors PDL's multi-value payload."""
    respx.post(pdl.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 8,
                "data": {
                    "emails": [{"address": "pm@fund.com", "type": "professional"}],
                    "mobile_phone": "+15552223333",
                },
            },
        )
    )
    respx.post(apollo_match.PEOPLE_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": None})
    )
    respx.get(hunter.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    respx.post(snov.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(snov.EMAIL_FINDER_URL).mock(
        return_value=httpx.Response(200, json={"data": {}})
    )

    session = _FakeSession()
    row = await discover_investor_contact(
        _person_entity(), investor_id=42, session=session
    )

    assert row is not None
    assert isinstance(row, InvestorContact)
    assert row.investor_id == 42
    assert row.email == "pm@fund.com"
    assert row.phone == "+15552223333"
    assert row.discovery_source == "pdl"
    assert row.emails is not None and len(row.emails) == 1
    assert row.phones is not None and row.phones[0]["type"] == "mobile"
