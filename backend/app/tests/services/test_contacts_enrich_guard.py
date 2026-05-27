"""Unit tests for the cooldown guard on ``ExecutiveContactService.enrich_contacts``.

Covers the cc-cli-02 fix: stop ``POST /broker-dealers/{id}/enrich`` from
re-firing on every detail-page visit for firms where Apollo previously
returned no result. The legacy 90-day guard reads off
``ExecutiveContact.enriched_at``, which never engages for empty-result
firms (no rows -> no timestamp). The new guard reads
``BrokerDealer.last_enrich_attempt_at``, which is stamped on every
Apollo-owned outcome (success + no-result) and skipped on transient
Apollo errors.

All tests use ``respx`` for HTTP and a hand-rolled fake ``AsyncSession``
so nothing hits a real DB / Apollo. Pattern mirrors
``test_contact_discovery.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import respx

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.services.contacts import ExecutiveContactService


APOLLO_MATCH_URL = ExecutiveContactService._APOLLO_MATCH_URL
APOLLO_ORG_URL = "https://api.apollo.io/api/v1/organizations/enrich"
PDL_PERSON_ENRICH_URL = "https://api.peopledatalabs.com/v5/person/enrich"


# ──────────────────────────── Fixtures ────────────────────────────


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the settings the cooldown guard reads.

    The default cooldown is 24h; tests that want a different window can
    override this fixture's value with another ``monkeypatch.setattr``.
    PDL auto-phone resolution is pinned OFF by default (via empty pdl key)
    so the legacy Apollo-only tests don't have to mock the PDL endpoint;
    the dedicated PDL tests override pdl_api_key to flip it on.
    """
    monkeypatch.setattr(settings, "contact_enrichment_provider", "apollo")
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "apollo_enrich_cooldown_hours", 24)
    monkeypatch.setattr(settings, "pdl_api_key", None)
    monkeypatch.setattr(settings, "contact_enrich_auto_pdl_phones", True)
    monkeypatch.setattr(settings, "pdl_min_likelihood", 6)
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)


def _make_bd(
    *,
    last_attempt: datetime | None = None,
    name: str = "ACME LLC",
    direct_owners: list[dict] | None = None,
    executive_officers: list[dict] | None = None,
    website: str | None = None,
) -> BrokerDealer:
    """Build an in-memory broker-dealer for the service under test.

    ``direct_owners`` / ``executive_officers`` populate the FINRA JSONB
    columns the per-officer Apollo fan-out reads. Leaving them at None
    forces the service down the org-enrich fallback path, which is what
    the cooldown-guard tests exercise.
    """
    bd = BrokerDealer(name=name, matched_source="edgar", is_deficient=False, status="active")
    bd.id = 1
    bd.last_enrich_attempt_at = last_attempt
    bd.direct_owners = direct_owners
    bd.executive_officers = executive_officers
    bd.website = website
    return bd


class _FakeResult:
    """Minimal Result stand-in supporting ``.scalars().all()``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    """Tiny AsyncSession stand-in for the cooldown-guard tests.

    Returns ``existing_contacts`` for any ``execute()`` call (selects and
    deletes both flow through; the service ignores the delete result).
    Tracks ``add_all`` / ``commit`` so tests can assert that the success
    and no-result paths actually persisted while the cooldown-skip and
    transient-error paths didn't.
    """

    def __init__(self, existing_contacts: list[ExecutiveContact] | None = None) -> None:
        self.existing_contacts = existing_contacts or []
        self.added: list[ExecutiveContact] = []
        self.commit_count = 0
        self.execute_calls = 0

    async def execute(self, _stmt: Any) -> _FakeResult:
        self.execute_calls += 1
        return _FakeResult(self.existing_contacts)

    def add_all(self, items: list[ExecutiveContact]) -> None:
        self.added.extend(items)

    async def commit(self) -> None:
        self.commit_count += 1


# ──────────────────────────── Tests ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_first_call_stamps_and_calls_apollo(patch_settings: None) -> None:
    """No prior attempt -> /people/match runs per officer, the BD timestamp gets stamped."""
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    match_route = respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "first_name": "Alice",
                    "last_name": "Doe",
                    "email": "alice@example.com",
                    "email_status": "verified",
                    "phone_numbers": [{"sanitized_number": "+15550100"}],
                    "linkedin_url": "https://linkedin.com/in/alice",
                }
            },
        )
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert match_route.called, "Apollo /people/match should fire on first call"
    assert bd.last_enrich_attempt_at is not None, "Stamp should be set on success"
    assert session.commit_count == 1
    assert len(session.added) == 1
    # Display name is title-cased from FINRA's parse so nameMatches() pairs the
    # contact with the FINRA officer card on the detail page.
    assert session.added[0].name == "Alice Doe"
    assert session.added[0].email == "alice@example.com"
    assert session.added[0].linkedin_url == "https://linkedin.com/in/alice"


@pytest.mark.asyncio
@respx.mock
async def test_within_cooldown_short_circuits(patch_settings: None) -> None:
    """A recent attempt -> Apollo is NOT hit, stamp does not move, no commit.

    This is the empty-result fix: the BD has no ExecutiveContact rows
    (Apollo returned nothing last time) but the cooldown timestamp is
    enough on its own to short-circuit the call.
    """
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    bd = _make_bd(
        last_attempt=one_hour_ago,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    match_route = respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": None})
    )

    service = ExecutiveContactService()
    result = await service.enrich_contacts(session, bd)

    assert not match_route.called, "Apollo must not be hit during cooldown"
    assert bd.last_enrich_attempt_at == one_hour_ago, "Stamp must not move"
    assert session.commit_count == 0, "No commit when cooldown short-circuits"
    assert result == []


@pytest.mark.asyncio
@respx.mock
async def test_past_cooldown_stamps_and_calls_apollo(patch_settings: None) -> None:
    """An attempt past the cooldown window -> Apollo runs, stamp moves forward."""
    twenty_five_hours_ago = datetime.now(timezone.utc) - timedelta(hours=25)
    bd = _make_bd(
        last_attempt=twenty_five_hours_ago,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    match_route = respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": None})
    )
    respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(200, json={"organization": None})
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert match_route.called, "Past-cooldown call should fire Apollo again"
    assert bd.last_enrich_attempt_at is not None
    assert bd.last_enrich_attempt_at > twenty_five_hours_ago, "Stamp should move forward"
    assert session.commit_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_no_result_still_stamps(patch_settings: None) -> None:
    """Apollo cleanly returns no people on either strategy -> stamp anyway.

    Without this stamp, the FE's useEffect would re-fire on every visit
    because no ExecutiveContact rows exist for the firm.
    """
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    match_route = respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": None})
    )
    org_route = respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(200, json={"organization": None})
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert match_route.called
    assert org_route.called, "Empty per-officer pass falls back to org enrich"
    assert bd.last_enrich_attempt_at is not None, "Empty-result must still stamp"
    assert session.commit_count == 1
    assert session.added == [], "No-result -> nothing added to session"


@pytest.mark.asyncio
@respx.mock
async def test_transient_error_does_not_stamp(patch_settings: None) -> None:
    """Apollo 5xx on every attempt -> do NOT stamp.

    Stamping on a transient failure would lock out the firm for 24h after
    a single 502, so the next visit must be allowed to retry.
    """
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    match_route = respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )
    respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert match_route.called
    assert bd.last_enrich_attempt_at is None, "Transient error must not stamp"
    assert session.commit_count == 0, "Transient error must not commit"


# ───────────────────── Per-officer /people/match coverage ─────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_per_officer_fanout_dedupes_and_skips_org_rows(
    patch_settings: None,
) -> None:
    """FINRA names are parsed, org-shaped rows are skipped, and duplicates
    (a person appearing in both direct_owners and executive_officers) only
    cause one /people/match call."""
    bd = _make_bd(
        last_attempt=None,
        # The first row is an org and must be skipped. The second row's
        # person matches the third row in executive_officers (dedupe on
        # lowercased first+last).
        direct_owners=[
            {"name": "CARLYLE INVESTMENT MANAGEMENT, L.L.C.", "title": "MEMBER"},
            {"name": "DOE, ALICE", "title": "MEMBER"},
        ],
        executive_officers=[
            {"name": "DOE, ALICE", "title": "CO-CEO"},
            {"name": "SMITH, BOB R.", "title": "CFO"},
        ],
    )
    session = _FakeSession()

    captured_payloads: list[dict] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(httpx.QueryParams())  # placeholder; real json below
        import json as _json
        captured_payloads[-1] = _json.loads(request.content.decode())
        last = captured_payloads[-1].get("last_name", "")
        return httpx.Response(
            200,
            json={
                "person": {
                    "email": f"{last.lower()}@example.com",
                    "email_status": "verified",
                    "linkedin_url": f"https://linkedin.com/in/{last.lower()}",
                    "phone_numbers": None,
                }
            },
        )

    respx.post(APOLLO_MATCH_URL).mock(side_effect=_capture)

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    # Two unique person officers — Doe and Smith. The org row was skipped
    # and the Doe duplicate collapsed.
    assert len(captured_payloads) == 2
    last_names = sorted(p["last_name"] for p in captured_payloads)
    assert last_names == ["Doe", "Smith"]

    assert len(session.added) == 2
    added_names = sorted(c.name for c in session.added)
    assert added_names == ["Alice Doe", "Bob Smith"]


@pytest.mark.asyncio
@respx.mock
async def test_no_person_officers_falls_back_to_org_enrich(
    patch_settings: None,
) -> None:
    """When FINRA lists only org-shaped owners (a wholly-owned subsidiary
    case), the per-officer fan-out is skipped entirely and the synthetic
    company-level row is created from /organizations/enrich."""
    bd = _make_bd(
        last_attempt=None,
        name="ACME LLC",
        direct_owners=[
            {"name": "PARENT HOLDINGS L.P.", "title": "MEMBER"},
        ],
    )
    session = _FakeSession()

    match_route = respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": None})
    )
    org_route = respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "organization": {
                    "name": "Acme LLC",
                    "linkedin_url": "https://linkedin.com/company/acme",
                    "primary_phone": {"sanitized_number": "+12120001000"},
                }
            },
        )
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert not match_route.called, "No person officers → skip /people/match entirely"
    assert org_route.called, "Should fall back to organizations/enrich"
    assert len(session.added) == 1
    assert session.added[0].title == "Company (Organization Profile)"
    assert session.added[0].phone == "+12120001000"


@pytest.mark.asyncio
@respx.mock
async def test_partial_transient_still_writes_successful_matches(
    patch_settings: None,
) -> None:
    """One officer 502s, the other returns a clean match. The successful
    contact is written, the cooldown IS stamped (because we did get useful
    data), and the org-enrich fallback is NOT triggered."""
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[
            {"name": "DOE, ALICE", "title": "CEO"},
            {"name": "SMITH, BOB", "title": "CFO"},
        ],
    )
    session = _FakeSession()

    def _selective(request: httpx.Request) -> httpx.Response:
        import json as _json
        payload = _json.loads(request.content.decode())
        if payload.get("last_name") == "Doe":
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(
            200,
            json={
                "person": {
                    "email": "bob@example.com",
                    "email_status": "verified",
                    "linkedin_url": "https://linkedin.com/in/bob",
                    "phone_numbers": None,
                }
            },
        )

    respx.post(APOLLO_MATCH_URL).mock(side_effect=_selective)
    org_route = respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(200, json={"organization": None})
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert not org_route.called, (
        "We got at least one good match — no need to fall back to org enrich"
    )
    assert len(session.added) == 1
    assert session.added[0].name == "Bob Smith"
    assert bd.last_enrich_attempt_at is not None, (
        "Partial success with at least one good contact should stamp the cooldown"
    )


@pytest.mark.asyncio
@respx.mock
async def test_all_match_calls_transient_skips_org_fallback_and_no_stamp(
    patch_settings: None,
) -> None:
    """If every /people/match returns 5xx, we still try the org-enrich
    fallback so the user sees *something*. If that ALSO fails transiently,
    we must NOT stamp the cooldown — the next visit should be allowed to
    retry instead of being locked out for 24h by a flaky Apollo."""
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    match_route = respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )
    org_route = respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert match_route.called
    assert org_route.called, "Empty per-officer pass falls back to org enrich"
    assert bd.last_enrich_attempt_at is None
    assert session.commit_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_match_returns_no_channels_is_dropped(patch_settings: None) -> None:
    """Apollo sometimes returns a person object with name + title only —
    no email, no linkedin, no phone. The detail-page ContactRow render
    guard skips contacts with zero channels, so we drop them at the
    service boundary instead of writing empty rows that won't render."""
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "title": "CEO",
                    "email": None,
                    "linkedin_url": None,
                    "phone_numbers": None,
                }
            },
        )
    )
    org_route = respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(200, json={"organization": None})
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert org_route.called, "Empty per-officer pass falls back to org enrich"
    assert session.added == []
    assert bd.last_enrich_attempt_at is not None, (
        "Clean Apollo-owned no-result must still stamp"
    )


# ───────────────────── FINRA name parser unit tests ─────────────────────


def test_parse_finra_person_name_handles_canonical_format() -> None:
    parsed = ExecutiveContactService._parse_finra_person_name("DOE, ALICE")
    assert parsed == ("Alice", "Doe")


def test_parse_finra_person_name_strips_middle_initial() -> None:
    parsed = ExecutiveContactService._parse_finra_person_name("KRZAK, ALEXANDER J.")
    assert parsed == ("Alexander", "Krzak")


def test_parse_finra_person_name_rejects_org_rows() -> None:
    assert (
        ExecutiveContactService._parse_finra_person_name(
            "CARLYLE INVESTMENT MANAGEMENT, L.L.C."
        )
        is None
    )
    assert (
        ExecutiveContactService._parse_finra_person_name("PARENT HOLDINGS L.P.")
        is None
    )
    assert ExecutiveContactService._parse_finra_person_name("ACME GROUP, INC.") is None


def test_parse_finra_person_name_rejects_malformed_input() -> None:
    assert ExecutiveContactService._parse_finra_person_name("") is None
    assert ExecutiveContactService._parse_finra_person_name("NO COMMA HERE") is None
    assert ExecutiveContactService._parse_finra_person_name("DOE,") is None


def test_website_domain_strips_scheme_and_path() -> None:
    assert (
        ExecutiveContactService._website_domain("https://www.acme.com/about")
        == "www.acme.com"
    )
    assert ExecutiveContactService._website_domain("HTTP://Acme.com/") == "acme.com"
    assert ExecutiveContactService._website_domain(None) is None
    assert ExecutiveContactService._website_domain("") is None


# ───────────────────── Auto-PDL phone resolution ─────────────────────


@pytest.fixture
def patch_settings_with_pdl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the auto-PDL step with a fake API key so respx mocks the URL.

    Same shape as ``patch_settings`` but pdl_api_key is set so the
    /people/match → PDL bridge in enrich_contacts actually fires.
    """
    monkeypatch.setattr(settings, "contact_enrichment_provider", "apollo")
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "apollo_enrich_cooldown_hours", 24)
    monkeypatch.setattr(settings, "pdl_api_key", "test-pdl-key")
    monkeypatch.setattr(settings, "contact_enrich_auto_pdl_phones", True)
    monkeypatch.setattr(settings, "pdl_min_likelihood", 6)
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)


def _pdl_hit(*, mobile: str | None = None, work: str | None = None) -> dict:
    """Build a PDL /v5/person/enrich-shaped success response."""
    data: dict = {"emails": []}
    if mobile:
        data["mobile_phone"] = mobile
    if work:
        data["phone_numbers"] = [work]
    return {"likelihood": 9, "data": data}


@pytest.mark.asyncio
@respx.mock
async def test_pdl_fills_phone_for_apollo_match(
    patch_settings_with_pdl: None,
) -> None:
    """The headline outcome: Apollo returns email + linkedin (no phone), PDL
    re-anchors on the email and fills both the scalar ``phone`` and the
    multi-value ``phones[]`` JSONB array so the FE renders mobile + work
    chips."""
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "alice@example.com",
                    "email_status": "verified",
                    "linkedin_url": "https://linkedin.com/in/alice",
                    "phone_numbers": None,
                }
            },
        )
    )
    pdl_route = respx.post(PDL_PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(
            200, json=_pdl_hit(mobile="+15550111", work="+15550222")
        )
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert pdl_route.called, "Apollo email + no phone → PDL must be hit"
    assert len(session.added) == 1
    contact = session.added[0]
    assert contact.phone == "+15550111", (
        "Scalar phone gets PDL's best single (mobile preferred)"
    )
    assert isinstance(contact.phones, list) and len(contact.phones) == 2
    assert {p["value"] for p in contact.phones} == {"+15550111", "+15550222"}
    # Apollo data is preserved untouched.
    assert contact.email == "alice@example.com"
    assert contact.linkedin_url == "https://linkedin.com/in/alice"


@pytest.mark.asyncio
@respx.mock
async def test_pdl_no_match_leaves_contact_alone(
    patch_settings_with_pdl: None,
) -> None:
    """PDL returns 404 (no confident match). Contact is still written with
    Apollo's email + linkedin, just without a phone — exactly what the page
    used to look like before this feature."""
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "alice@example.com",
                    "email_status": "verified",
                    "linkedin_url": "https://linkedin.com/in/alice",
                    "phone_numbers": None,
                }
            },
        )
    )
    pdl_route = respx.post(PDL_PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(404, json={"status": 404})
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert pdl_route.called
    assert len(session.added) == 1
    contact = session.added[0]
    assert contact.phone is None
    # phones JSONB stays unset (None) — pydantic synthesizer in the API
    # response will then render from the scalar (None) and produce []
    assert contact.phones is None
    assert contact.email == "alice@example.com"
    assert contact.linkedin_url == "https://linkedin.com/in/alice"


@pytest.mark.asyncio
@respx.mock
async def test_pdl_error_silenced_apollo_data_still_writes(
    patch_settings_with_pdl: None,
) -> None:
    """PDL 500 must NOT block the Apollo write. The phone stays empty but
    the contact (with email + linkedin) lands in the DB and the cooldown is
    stamped — exactly the policy for an optional best-effort step."""
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "alice@example.com",
                    "email_status": "verified",
                    "linkedin_url": "https://linkedin.com/in/alice",
                    "phone_numbers": None,
                }
            },
        )
    )
    respx.post(PDL_PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(500, text="boom")
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert len(session.added) == 1, "Apollo commit must not depend on PDL"
    assert session.added[0].phone is None
    assert bd.last_enrich_attempt_at is not None, (
        "PDL is best-effort — its failure should NOT block the cooldown stamp"
    )


@pytest.mark.asyncio
@respx.mock
async def test_pdl_skipped_when_setting_disabled(
    patch_settings_with_pdl: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The setting flag turns the step off entirely (escape hatch in case
    PDL spend becomes a concern)."""
    monkeypatch.setattr(settings, "contact_enrich_auto_pdl_phones", False)
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": "alice@example.com",
                    "email_status": "verified",
                    "linkedin_url": "https://linkedin.com/in/alice",
                    "phone_numbers": None,
                }
            },
        )
    )
    pdl_route = respx.post(PDL_PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(200, json=_pdl_hit(mobile="+15550111"))
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert not pdl_route.called, "Setting disabled → PDL not called"
    assert len(session.added) == 1
    assert session.added[0].phone is None


@pytest.mark.asyncio
@respx.mock
async def test_pdl_skipped_when_no_email(patch_settings_with_pdl: None) -> None:
    """Apollo returned a person object with linkedin but no email — there's
    nothing for PDL to anchor on, so we skip the call rather than waste a
    credit on a guaranteed miss."""
    bd = _make_bd(
        last_attempt=None,
        executive_officers=[{"name": "DOE, ALICE", "title": "CEO"}],
    )
    session = _FakeSession()

    respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "email": None,
                    "linkedin_url": "https://linkedin.com/in/alice",
                    "phone_numbers": None,
                }
            },
        )
    )
    pdl_route = respx.post(PDL_PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(200, json=_pdl_hit(mobile="+15550111"))
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert not pdl_route.called, "No email → no PDL anchor → skip the call"
    assert len(session.added) == 1
    assert session.added[0].email is None


@pytest.mark.asyncio
@respx.mock
async def test_pdl_skipped_when_synthetic_company_row_already_has_phone(
    patch_settings_with_pdl: None,
) -> None:
    """The org-enrich fallback already carries the company HQ phone on the
    synthetic Company row. PDL has no email to anchor on for that row
    anyway, but the explicit phone-present guard means we don't bother
    trying — protecting against future regressions where someone changes
    the fallback to include a contact@-style email."""
    bd = _make_bd(
        last_attempt=None,
        name="ACME LLC",
        # No person officers → fall back to org enrich.
        direct_owners=[{"name": "PARENT HOLDINGS L.P.", "title": "MEMBER"}],
    )
    session = _FakeSession()

    respx.post(APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": None})
    )
    respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "organization": {
                    "name": "Acme LLC",
                    "linkedin_url": "https://linkedin.com/company/acme",
                    "primary_phone": {"sanitized_number": "+12120001000"},
                }
            },
        )
    )
    pdl_route = respx.post(PDL_PERSON_ENRICH_URL).mock(
        return_value=httpx.Response(200, json=_pdl_hit(mobile="+15550111"))
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert not pdl_route.called, (
        "Org row already has a phone — no PDL anchor needed"
    )
    assert len(session.added) == 1
    # Original HQ phone from Apollo /organizations/enrich is preserved.
    assert session.added[0].phone == "+12120001000"
