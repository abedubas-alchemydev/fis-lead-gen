"""Unit tests for the discovered-email enrichment provider chain.

Three layers, all DB-free / network-free:

* orchestrator -- gap-fill merge, first-match-wins, early-stop, and the
  outcome -> status mapping (enriched / no_match / error / not-configured),
  driven by fake providers so no HTTP fires.
* Hunter / Snov providers -- response parsing via respx (real ``/v2/people/find``
  and ``/v1/get-profile-by-email`` shapes).
* Web-scraper provider -- name parse + gating, with ``discover_web_fallback``
  stubbed so no crawl/search runs.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.core.config import settings
from app.models.discovered_email import DiscoveredEmail
from app.services.contact_discovery.base import DiscoveryResult as CdDiscoveryResult
from app.services.contact_discovery.base import EmailHit as CdEmailHit
from app.services.email_extractor.enrichment import hunter as hunter_mod
from app.services.email_extractor.enrichment import name_lookup as name_lookup_mod
from app.services.email_extractor.enrichment import orchestrator
from app.services.email_extractor.enrichment import pdl as pdl_mod
from app.services.email_extractor.enrichment import snov as snov_mod
from app.services.email_extractor.enrichment import web_scraper as web_scraper_mod
from app.services.email_extractor.enrichment.base import (
    NOT_CONFIGURED_MESSAGE,
    EmailEnrichmentProvider,
    EnrichmentData,
    EnrichmentError,
)


# ──────────────────────────── test doubles ────────────────────────────


class _FakeProvider(EmailEnrichmentProvider):
    """Configurable provider double. Records call count so the orchestrator's
    early-stop / skip behaviour can be asserted."""

    def __init__(
        self,
        name: str,
        data: EnrichmentData | None = None,
        *,
        configured: bool = True,
        error: bool = False,
    ) -> None:
        self.name = name
        self._data = data
        self._configured = configured
        self._error = error
        self.calls = 0

    def is_configured(self) -> bool:
        return self._configured

    async def enrich(self, email: str, needed: set[str] | None = None) -> EnrichmentData | None:
        self.calls += 1
        if self._error:
            raise EnrichmentError("boom")
        return self._data


class _FakeSession:
    """AsyncSession stand-in exposing only get/commit/refresh."""

    def __init__(self, row: DiscoveredEmail) -> None:
        self._row = row
        self.commits = 0

    async def get(self, _model: Any, _pk: Any) -> DiscoveredEmail:
        return self._row

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _row: Any) -> None:
        return None


def _seed_row() -> DiscoveredEmail:
    row = DiscoveredEmail()
    row.id = 1
    row.email = "jane@acme.com"
    return row


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    providers: dict[str, EmailEnrichmentProvider],
    chain: str,
) -> None:
    monkeypatch.setattr(orchestrator, "_PROVIDERS", providers)
    monkeypatch.setattr(settings, "email_enrichment_chain", chain, raising=False)


# ──────────────────────────── orchestrator ────────────────────────────


async def test_gap_fill_merges_across_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Later providers fill fields the earlier one left empty; the row ends up
    with the union."""
    apollo = _FakeProvider("apollo", EnrichmentData(name="Jane Doe", title="CCO"))
    hunter = _FakeProvider(
        "hunter",
        EnrichmentData(
            title="ignored",  # already set by apollo -> must NOT overwrite
            company="Acme Securities",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
            phone="+15551112222",
            email="jane.alt@gmail.com",
        ),
    )
    _wire(monkeypatch, {"apollo": apollo, "hunter": hunter}, "apollo,hunter")

    out = await orchestrator.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert out.enriched_name == "Jane Doe"
    assert out.enriched_title == "CCO"  # first-match-wins on a contested field
    assert out.enriched_company == "Acme Securities"
    assert out.enriched_linkedin_url == "https://www.linkedin.com/in/jane-doe"
    assert out.enriched_phone == "+15551112222"
    assert out.enriched_email == "jane.alt@gmail.com"
    assert out.enrichment_status == "enriched"
    assert apollo.calls == 1 and hunter.calls == 1


async def test_first_match_wins_on_contested_field(monkeypatch: pytest.MonkeyPatch) -> None:
    apollo = _FakeProvider("apollo", EnrichmentData(name="A"))
    hunter = _FakeProvider("hunter", EnrichmentData(name="B", company="Acme"))
    _wire(monkeypatch, {"apollo": apollo, "hunter": hunter}, "apollo,hunter")

    out = await orchestrator.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert out.enriched_name == "A"
    assert out.enriched_company == "Acme"


async def test_complete_match_stops_chain_early(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a provider fills every field, downstream providers aren't called."""
    full = EnrichmentData(
        name="J", title="T", company="C", linkedin_url="L", email="e@x.com", phone="p"
    )
    apollo = _FakeProvider("apollo", full)
    hunter = _FakeProvider("hunter", EnrichmentData(name="B"))
    _wire(monkeypatch, {"apollo": apollo, "hunter": hunter}, "apollo,hunter")

    out = await orchestrator.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert apollo.calls == 1
    assert hunter.calls == 0
    assert out.enrichment_status == "enriched"


async def test_no_provider_configured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    apollo = _FakeProvider("apollo", None, configured=False)
    _wire(monkeypatch, {"apollo": apollo}, "apollo")

    with pytest.raises(EnrichmentError) as excinfo:
        await orchestrator.enrich_discovered_email(_FakeSession(_seed_row()), 1)
    assert str(excinfo.value) == NOT_CONFIGURED_MESSAGE


async def test_unconfigured_provider_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfigured link in the chain is skipped (not called, not a miss)."""
    apollo = _FakeProvider("apollo", None, configured=False)
    hunter = _FakeProvider("hunter", EnrichmentData(name="J"))
    _wire(monkeypatch, {"apollo": apollo, "hunter": hunter}, "apollo,hunter")

    out = await orchestrator.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert apollo.calls == 0
    assert hunter.calls == 1
    assert out.enriched_name == "J"


async def test_all_clean_miss_sets_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    apollo = _FakeProvider("apollo", None)
    hunter = _FakeProvider("hunter", None)
    _wire(monkeypatch, {"apollo": apollo, "hunter": hunter}, "apollo,hunter")

    out = await orchestrator.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert out.enrichment_status == "no_match"


async def test_all_errored_sets_error(monkeypatch: pytest.MonkeyPatch) -> None:
    apollo = _FakeProvider("apollo", None, error=True)
    hunter = _FakeProvider("hunter", None, error=True)
    _wire(monkeypatch, {"apollo": apollo, "hunter": hunter}, "apollo,hunter")

    out = await orchestrator.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert out.enrichment_status == "error"


async def test_error_then_match_is_enriched(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing provider doesn't abort the chain; a later match still wins."""
    apollo = _FakeProvider("apollo", None, error=True)
    hunter = _FakeProvider("hunter", EnrichmentData(name="J"))
    _wire(monkeypatch, {"apollo": apollo, "hunter": hunter}, "apollo,hunter")

    out = await orchestrator.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert out.enriched_name == "J"
    assert out.enrichment_status == "enriched"


async def test_apollo_person_id_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    apollo = _FakeProvider("apollo", EnrichmentData(name="J", apollo_person_id="pid123"))
    _wire(monkeypatch, {"apollo": apollo}, "apollo")

    out = await orchestrator.enrich_discovered_email(_FakeSession(_seed_row()), 1)

    assert out.apollo_person_id == "pid123"


def test_any_provider_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(
        monkeypatch,
        {
            "apollo": _FakeProvider("apollo", None, configured=False),
            "hunter": _FakeProvider("hunter", None, configured=True),
        },
        "apollo,hunter",
    )
    assert orchestrator.any_provider_configured() is True

    _wire(monkeypatch, {"apollo": _FakeProvider("apollo", None, configured=False)}, "apollo")
    assert orchestrator.any_provider_configured() is False


# ─────────────────────── Apollo credit breaker ────────────────────────


@respx.mock
async def test_apollo_credit_exhaustion_trips_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 422 'insufficient credits' opens a per-process breaker so is_configured()
    reports False -- the orchestrator then skips Apollo for the rest of a bulk run
    instead of firing one dead call per row at a depleted account."""
    from app.services.email_extractor.enrichment import apollo as apollo_mod

    monkeypatch.setattr(settings, "apollo_api_key", "k", raising=False)
    apollo_mod._reset_credit_breaker_for_tests()
    provider = apollo_mod.ApolloEnrichProvider()
    assert provider.is_configured() is True

    respx.post(apollo_mod.APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(422, text='{"error":"You have insufficient credits!"}')
    )
    with pytest.raises(EnrichmentError):
        await provider.enrich("jane@acme.com")

    assert provider.is_configured() is False  # breaker open -> orchestrator skips Apollo
    apollo_mod._reset_credit_breaker_for_tests()
    assert provider.is_configured() is True


@respx.mock
async def test_apollo_non_credit_422_leaves_breaker_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-credit 422 (e.g. a bad parameter) raises but must NOT open the credit
    breaker -- only account-wide credit exhaustion should disable Apollo."""
    from app.services.email_extractor.enrichment import apollo as apollo_mod

    monkeypatch.setattr(settings, "apollo_api_key", "k", raising=False)
    apollo_mod._reset_credit_breaker_for_tests()
    respx.post(apollo_mod.APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(422, text='{"error":"invalid email address"}')
    )
    with pytest.raises(EnrichmentError):
        await apollo_mod.ApolloEnrichProvider().enrich("nope")

    assert apollo_mod.ApolloEnrichProvider().is_configured() is True  # stayed closed
    apollo_mod._reset_credit_breaker_for_tests()


# ──────────────────────────── Hunter provider ────────────────────────────


@respx.mock
async def test_hunter_parses_combined_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "hunter_api_key", "k", raising=False)
    respx.get(hunter_mod.PEOPLE_FIND_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "person": {
                        "name": {"fullName": "Jane Doe", "givenName": "Jane", "familyName": "Doe"},
                        "employment": {
                            "title": "Compliance Officer",
                            "name": "Acme Securities",
                            "seniority": "executive",
                        },
                        "linkedin": {"handle": "in/jane-doe"},
                        "phone": "+15551112222",
                        "email": "jane.personal@gmail.com",
                    },
                    "company": {"name": "Acme Holdings"},
                }
            },
        )
    )

    data = await hunter_mod.HunterEnrichProvider().enrich("jane@acme.com")

    assert data is not None
    assert data.name == "Jane Doe"
    assert data.title == "Compliance Officer"
    assert data.company == "Acme Securities"
    assert data.linkedin_url == "https://www.linkedin.com/in/jane-doe"
    assert data.phone == "+15551112222"
    # personal email differs from the discovered address -> kept
    assert data.email == "jane.personal@gmail.com"


@respx.mock
async def test_hunter_parses_flat_shape_and_suppresses_echo_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "hunter_api_key", "k", raising=False)
    respx.get(hunter_mod.PEOPLE_FIND_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "name": {"fullName": "John Roe"},
                    "position": "CFO",
                    "linkedin_url": "https://www.linkedin.com/in/johnroe",
                    "email": "jane@acme.com",  # echoes the input -> dropped
                }
            },
        )
    )

    data = await hunter_mod.HunterEnrichProvider().enrich("jane@acme.com")

    assert data is not None
    assert data.name == "John Roe"
    assert data.title == "CFO"
    assert data.linkedin_url == "https://www.linkedin.com/in/johnroe"
    assert data.email is None  # echoed input address is not a new email


@respx.mock
async def test_hunter_404_is_clean_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "hunter_api_key", "k", raising=False)
    respx.get(hunter_mod.PEOPLE_FIND_URL).mock(return_value=httpx.Response(404, json={}))

    assert await hunter_mod.HunterEnrichProvider().enrich("jane@acme.com") is None


@respx.mock
async def test_hunter_5xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "hunter_api_key", "k", raising=False)
    respx.get(hunter_mod.PEOPLE_FIND_URL).mock(return_value=httpx.Response(500, text="boom"))

    with pytest.raises(EnrichmentError):
        await hunter_mod.HunterEnrichProvider().enrich("jane@acme.com")


async def test_hunter_unconfigured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "hunter_api_key", "", raising=False)
    assert await hunter_mod.HunterEnrichProvider().enrich("jane@acme.com") is None


# ──────────────────────────── Snov provider ────────────────────────────


@respx.mock
async def test_snov_parses_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "snov_client_id", "cid", raising=False)
    monkeypatch.setattr(settings, "snov_client_secret", "sec", raising=False)
    snov_mod._reset_token_cache_for_tests()
    respx.post(snov_mod.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(snov_mod.PROFILE_BY_EMAIL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "Liz Hamer",
                "firstName": "Liz",
                "lastName": "Hamer",
                "currentJobs": [
                    {"position": "Regional Creative Director", "companyName": "Globex"}
                ],
                "social": [
                    {"link": "https://www.linkedin.com/in/lizihamer/", "type": "linkedIn"}
                ],
            },
        )
    )

    data = await snov_mod.SnovEnrichProvider().enrich("liz@globex.com")

    assert data is not None
    assert data.name == "Liz Hamer"
    assert data.title == "Regional Creative Director"
    assert data.company == "Globex"
    assert data.linkedin_url == "https://www.linkedin.com/in/lizihamer/"
    assert data.phone is None  # this endpoint returns no phone


@respx.mock
async def test_snov_no_profile_is_clean_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "snov_client_id", "cid", raising=False)
    monkeypatch.setattr(settings, "snov_client_secret", "sec", raising=False)
    snov_mod._reset_token_cache_for_tests()
    respx.post(snov_mod.OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(snov_mod.PROFILE_BY_EMAIL_URL).mock(
        return_value=httpx.Response(200, json={"success": False})
    )

    assert await snov_mod.SnovEnrichProvider().enrich("nobody@globex.com") is None


async def test_snov_unconfigured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "snov_client_id", "", raising=False)
    monkeypatch.setattr(settings, "snov_client_secret", "", raising=False)
    assert await snov_mod.SnovEnrichProvider().enrich("liz@globex.com") is None


# ──────────────────────────── PDL provider ────────────────────────────


@respx.mock
async def test_pdl_parses_person_and_prefers_personal_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDL fills the two fields the chain most often still misses after Apollo:
    a phone (mobile preferred) and a personal email. Name is title-cased."""
    monkeypatch.setattr(settings, "pdl_api_key", "k", raising=False)
    monkeypatch.setattr(settings, "pdl_min_likelihood", 6, raising=False)
    respx.post(pdl_mod.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 8,
                "data": {
                    "full_name": "jane doe",  # PDL lowercases -> we title-case
                    "job_title": "Compliance Officer",
                    "job_company_name": "Acme Securities",
                    "linkedin_url": "linkedin.com/in/janedoe",  # no scheme
                    "work_email": "jane@acme.com",  # echoes discovered -> dropped
                    "emails": [
                        {"address": "jane@acme.com", "type": "professional"},
                        {"address": "jane.personal@gmail.com", "type": "personal"},
                    ],
                    "mobile_phone": "+15551112222",
                    "phone_numbers": ["+15559998888"],
                },
            },
        )
    )

    data = await pdl_mod.PdlEnrichProvider().enrich("jane@acme.com")

    assert data is not None
    assert data.name == "Jane Doe"
    assert data.title == "Compliance Officer"
    assert data.company == "Acme Securities"
    assert data.linkedin_url == "https://linkedin.com/in/janedoe"
    assert data.email == "jane.personal@gmail.com"  # personal wins; echo dropped
    assert data.phone == "+15551112222"  # mobile leads phone_numbers[]


@respx.mock
async def test_pdl_falls_back_to_phone_numbers_when_no_mobile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", "k", raising=False)
    respx.post(pdl_mod.PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "likelihood": 7,
                "data": {"full_name": "john roe", "phone_numbers": ["+15553334444"]},
            },
        )
    )

    data = await pdl_mod.PdlEnrichProvider().enrich("john@roe.com")

    assert data is not None
    assert data.phone == "+15553334444"
    assert data.email is None  # no differing address to contribute


@respx.mock
async def test_pdl_404_is_clean_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", "k", raising=False)
    respx.post(pdl_mod.PERSON_ENRICH_URL).mock(return_value=httpx.Response(404, json={}))

    assert await pdl_mod.PdlEnrichProvider().enrich("nobody@acme.com") is None


@respx.mock
async def test_pdl_5xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", "k", raising=False)
    respx.post(pdl_mod.PERSON_ENRICH_URL).mock(return_value=httpx.Response(500, text="boom"))

    with pytest.raises(EnrichmentError):
        await pdl_mod.PdlEnrichProvider().enrich("jane@acme.com")


async def test_pdl_unconfigured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pdl_api_key", "", raising=False)
    assert await pdl_mod.PdlEnrichProvider().enrich("jane@acme.com") is None


# ──────────────────────────── Name-lookup provider ────────────────────────────


class _FakeFinder:
    """Stand-in for a contact_discovery name+domain provider (find_person)."""

    def __init__(self, result: Any = None, *, error: bool = False) -> None:
        self._result = result
        self._error = error

    async def find_person(self, first: str, last: str, org: str, domain: str) -> Any:
        if self._error:
            raise RuntimeError("boom")
        return self._result


async def test_name_lookup_forward_fills_phone_and_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reverse-email pass missed the channel; a name+domain broker hit fills
    the phone + personal email, and the name parsed from the address is attached
    only because a broker corroborated the person."""
    monkeypatch.setattr(settings, "apollo_api_key", "k", raising=False)
    monkeypatch.setattr(settings, "pdl_api_key", "", raising=False)
    monkeypatch.setattr(settings, "hunter_api_key", "", raising=False)
    hit = CdDiscoveryResult(
        email="jonathan.lemco@vanguard.com",  # echoes the discovered address -> dropped
        phone="+15551112222",
        linkedin_url="https://www.linkedin.com/in/jlemco",
        confidence=85.0,
        provider="apollo_match",
        raw={},
        emails=[CdEmailHit(value="j.lemco@gmail.com", type="personal", confidence=85.0, source="apollo_match")],
        apollo_person_id="pid-9",
    )
    monkeypatch.setattr(name_lookup_mod, "ApolloMatchProvider", lambda: _FakeFinder(hit))

    data = await name_lookup_mod.NameLookupEnrichProvider().enrich("jonathan.lemco@vanguard.com")

    assert data is not None
    assert data.name == "Jonathan Lemco"
    assert data.phone == "+15551112222"
    assert data.email == "j.lemco@gmail.com"  # personal, differs from discovered
    assert data.linkedin_url == "https://www.linkedin.com/in/jlemco"
    assert data.apollo_person_id == "pid-9"


async def test_name_lookup_rejects_generic_and_unsplittable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "k", raising=False)
    provider = name_lookup_mod.NameLookupEnrichProvider()
    # single-token local -> can't derive a person
    assert await provider.enrich("info@vanguard.com") is None
    # two-token but firm-wide -> rejected before any broker call
    assert await provider.enrich("investor.relations@vanguard.com") is None


async def test_name_lookup_all_miss_does_not_fabricate_a_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No broker corroborated the person -> return None (never invent a name from
    the address alone)."""
    monkeypatch.setattr(settings, "apollo_api_key", "k", raising=False)
    monkeypatch.setattr(settings, "pdl_api_key", "", raising=False)
    monkeypatch.setattr(settings, "hunter_api_key", "", raising=False)
    monkeypatch.setattr(name_lookup_mod, "ApolloMatchProvider", lambda: _FakeFinder(None))

    assert await name_lookup_mod.NameLookupEnrichProvider().enrich("jane.doe@acme.com") is None


async def test_name_lookup_all_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "k", raising=False)
    monkeypatch.setattr(settings, "pdl_api_key", "", raising=False)
    monkeypatch.setattr(settings, "hunter_api_key", "", raising=False)
    monkeypatch.setattr(name_lookup_mod, "ApolloMatchProvider", lambda: _FakeFinder(error=True))

    with pytest.raises(EnrichmentError):
        await name_lookup_mod.NameLookupEnrichProvider().enrich("jane.doe@acme.com")


async def test_name_lookup_unconfigured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apollo_api_key", "", raising=False)
    monkeypatch.setattr(settings, "pdl_api_key", "", raising=False)
    monkeypatch.setattr(settings, "hunter_api_key", "", raising=False)
    assert await name_lookup_mod.NameLookupEnrichProvider().enrich("jane.doe@acme.com") is None


async def test_name_lookup_skips_when_no_fillable_field_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If only title/company are still missing (fields this link can't supply),
    it does no broker work at all."""
    monkeypatch.setattr(settings, "apollo_api_key", "k", raising=False)
    built = {"n": 0}

    def _factory() -> _FakeFinder:
        built["n"] += 1
        return _FakeFinder(None)

    monkeypatch.setattr(name_lookup_mod, "ApolloMatchProvider", _factory)

    out = await name_lookup_mod.NameLookupEnrichProvider().enrich(
        "jane.doe@acme.com", needed={"title", "company"}
    )

    assert out is None
    assert built["n"] == 0


# ──────────────────────────── Web-scraper provider ────────────────────────────


async def test_web_scraper_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "web_fallback_enabled", False, raising=False)
    assert await web_scraper_mod.WebScraperEnrichProvider().enrich("jane.doe@acme.com") is None


async def test_web_scraper_rejects_generic_and_unsplittable_locals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "web_fallback_enabled", True, raising=False)
    provider = web_scraper_mod.WebScraperEnrichProvider()
    # single-token / role inbox -> can't derive a person
    assert await provider.enrich("info@acme.com") is None
    # two-token but firm-wide -> rejected before any crawl
    assert await provider.enrich("investor.relations@acme.com") is None


async def test_web_scraper_maps_web_fallback_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.contact_discovery.base import DiscoveryResult, PhoneHit

    monkeypatch.setattr(settings, "web_fallback_enabled", True, raising=False)

    async def _fake_discover(
        *, domain: str, org_name: str, people: list[Any], crawler: Any = None, linkedin: Any = None
    ) -> dict[int, DiscoveryResult]:
        return {
            0: DiscoveryResult(
                email=None,
                phone=None,
                linkedin_url="https://www.linkedin.com/in/jane-doe",
                confidence=80.0,
                provider="web_fallback",
                raw={},
                emails=[],
                phones=[
                    PhoneHit(value="(555) 123-4567", type="work", confidence=70.0, source="web_fallback")
                ],
            )
        }

    monkeypatch.setattr(web_scraper_mod, "discover_web_fallback", _fake_discover)

    data = await web_scraper_mod.WebScraperEnrichProvider().enrich("jane.doe@acme.com")

    assert data is not None
    assert data.name == "Jane Doe"
    assert data.linkedin_url == "https://www.linkedin.com/in/jane-doe"
    assert data.phone == "(555) 123-4567"


async def test_web_scraper_skips_serpapi_search_when_linkedin_not_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When LinkedIn is already filled upstream, the SerpAPI-backed search is
    stubbed off (protects scarce SerpAPI quota); the crawl still runs for the
    missing phone."""
    monkeypatch.setattr(settings, "web_fallback_enabled", True, raising=False)
    captured: dict[str, Any] = {}

    async def _fake_discover(*, domain: str, org_name: str, people: list[Any], crawler: Any = None, linkedin: Any = None) -> dict[int, Any]:
        captured["crawler"] = crawler
        captured["linkedin"] = linkedin
        return {}

    monkeypatch.setattr(web_scraper_mod, "discover_web_fallback", _fake_discover)

    await web_scraper_mod.WebScraperEnrichProvider().enrich("jane.doe@acme.com", needed={"phone"})

    assert captured["crawler"] is None  # real crawler built downstream
    assert isinstance(captured["linkedin"], web_scraper_mod._NullLinkedIn)
    assert await captured["linkedin"].find_person("a", "b", "c", "d") is None


async def test_web_scraper_skips_crawl_when_only_linkedin_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "web_fallback_enabled", True, raising=False)
    captured: dict[str, Any] = {}

    async def _fake_discover(*, domain: str, org_name: str, people: list[Any], crawler: Any = None, linkedin: Any = None) -> dict[int, Any]:
        captured["crawler"] = crawler
        captured["linkedin"] = linkedin
        return {}

    monkeypatch.setattr(web_scraper_mod, "discover_web_fallback", _fake_discover)

    await web_scraper_mod.WebScraperEnrichProvider().enrich("jane.doe@acme.com", needed={"linkedin_url"})

    assert captured["linkedin"] is None  # real provider built downstream
    assert isinstance(captured["crawler"], web_scraper_mod._NullCrawler)


async def test_web_scraper_noop_when_no_web_sourced_field_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """name/title/company aren't web-sourced; if only those are missing the
    provider does no work at all."""
    monkeypatch.setattr(settings, "web_fallback_enabled", True, raising=False)
    calls = {"n": 0}

    async def _fake_discover(**_kwargs: Any) -> dict[int, Any]:
        calls["n"] += 1
        return {}

    monkeypatch.setattr(web_scraper_mod, "discover_web_fallback", _fake_discover)

    out = await web_scraper_mod.WebScraperEnrichProvider().enrich(
        "jane.doe@acme.com", needed={"name", "title", "company"}
    )

    assert out is None
    assert calls["n"] == 0
