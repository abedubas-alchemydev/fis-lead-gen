"""Regression tests for the extracted reusable profile builders.

Phase 1 of the DOX Share public-API work pulled the profile-assembly bodies
out of three detail endpoints into standalone builders so the same assembly
can serve both the authenticated detail page and (in a later phase) the
public share surface:

- ``broker_dealers.build_broker_dealer_profile(db, bd, *, user_id)``
- ``investment_advisors.build_investment_advisor_profile(db, advisor, *, user_id)``
- ``banks.build_bank_detail(bank)``  (pure sync — no per-user state)

Two things are locked here, with no database (default, non-integration
suite): the three endpoints still return the pre-refactor response shape via
their now-thin wrappers (repos monkeypatched, ORM fixtures built inline), and
the ``user_id=None`` public path on the BD/IA builders skips the per-user
favorites lookup entirely — the lookup is monkeypatched to raise, proving it
is never awaited, and the response carries ``is_favorited=False`` /
``favorited_at=None``.

Same driving pattern as ``test_banks_endpoints.py``: ``dependency_overrides``
for auth + db, ``monkeypatch`` on the module-level repository / favorites
names.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import app.api.v1.endpoints.banks as banks_endpoints
import app.api.v1.endpoints.broker_dealers as bd_endpoints
import app.api.v1.endpoints.investment_advisors as ia_endpoints
from app.db.session import get_db_session
from app.main import app
from app.models.bank import Bank, BankApplicationEvent, BankContact
from app.models.broker_dealer import BrokerDealer
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

NOW = datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc)

# Sentinel handed to the builders in the direct-call tests. Every repository
# call is monkeypatched to ignore its ``db`` argument, so the value is never
# actually used — it only proves the builder threads what it is given.
_DB_SENTINEL: Any = object()


# ── Shared helpers ───────────────────────────────────────────────────────────


def _user(permissions: list[str], role: str = "viewer") -> AuthenticatedUser:
    return AuthenticatedUser(
        id="test-user",
        name="Test User",
        email="test-user@example.com",
        role=role,
        feature_permissions=permissions,
        session_expires_at=datetime(2099, 1, 1),
    )


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _areturn(value: Any):
    """Build an async stub that ignores its args and returns ``value``."""

    async def _stub(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _stub


async def _must_not_be_awaited(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError(
        "favorites lookup must not be awaited on the public (user_id=None) path"
    )


# ── Fixtures (inline ORM rows; no DB) ────────────────────────────────────────


def _bd_fixture() -> BrokerDealer:
    return BrokerDealer(
        id=7,
        name="Stark Securities LLC",
        cik="0001234567",
        crd_number="123456",
        sec_file_number="8-01234",
        city="New York",
        state="NY",
        status="active",
        matched_source="edgar",
        is_deficient=False,
        current_clearing_is_competitor=False,
        is_niche_restricted=False,
        created_at=NOW,
    )


def _advisor_fixture() -> InvestmentAdvisor:
    return InvestmentAdvisor(
        id=11,
        name="Rhodes Capital Advisers LLC",
        cik="0007654321",
        crd_number="654321",
        sec_file_number="801-98765",
        city="Los Angeles",
        state="CA",
        status="active",
        matched_source="iapd",
        files_13f=True,
        created_at=NOW,
        updated_at=NOW,
    )


def _bank_fixture() -> Bank:
    bank = Bank(
        id=7,
        fdic_cert="59378",
        name="Erebor Bank, NA",
        city="Columbus",
        state="OH",
        charter_authority="OCC",
        charter_type="National",
        charter_status="opened",
        digital_assets=True,
        active=True,
        source="fdic+occ",
        created_at=NOW,
        updated_at=NOW,
    )
    bank.application_events = [
        BankApplicationEvent(
            id=3,
            bank_id=7,
            action="Consummated/Effective",
            action_date=date(2026, 2, 6),
            filing_type="New Bank Charter",
            source_url=(
                "https://apps.occ.gov/CAS/home/details"
                "?FilingTypeID=2&FilingID=342076&FilingSubtypeID=1101"
            ),
            created_at=NOW,
        ),
    ]
    bank.contacts = [
        BankContact(
            id=1,
            bank_id=7,
            name="Jane Organizer",
            title="Organizer",
            email="jane.organizer@erebor.example",
            source="application_pdf",
            created_at=NOW,
        ),
    ]
    return bank


def _patch_bd_data_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every non-favorites data read the BD builder makes to empty."""
    monkeypatch.setattr(bd_endpoints.repository, "get_financial_metrics", _areturn([]))
    monkeypatch.setattr(bd_endpoints.repository, "list_clearing_arrangements", _areturn([]))
    monkeypatch.setattr(bd_endpoints.repository, "list_clearing_memberships", _areturn([]))
    monkeypatch.setattr(bd_endpoints.repository, "list_introducing_arrangements", _areturn([]))
    monkeypatch.setattr(bd_endpoints.repository, "list_industry_arrangements", _areturn([]))
    monkeypatch.setattr(bd_endpoints.repository, "get_executive_contacts", _areturn([]))
    monkeypatch.setattr(
        bd_endpoints.alert_repository, "list_alerts", _areturn(SimpleNamespace(items=[]))
    )


def _patch_advisor_data_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ia_endpoints.repository, "list_advisor_contacts", _areturn([]))
    monkeypatch.setattr(ia_endpoints.repository, "list_advisor_filings", _areturn([]))
    monkeypatch.setattr(ia_endpoints.repository, "list_clearing_memberships", _areturn([]))


# ── Broker-dealer: endpoint shape via the thin wrapper ───────────────────────


async def test_broker_dealer_profile_endpoint_returns_pre_refactor_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bd = _bd_fixture()
    _patch_bd_data_methods(monkeypatch)
    monkeypatch.setattr(
        bd_endpoints.repository,
        "get_broker_dealer",
        _areturn(bd),
    )
    monkeypatch.setattr(bd_endpoints, "is_favorited", _areturn((True, NOW)))

    app.dependency_overrides[get_current_user] = lambda: _user(["master_list"])
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            response = await client.get("/api/v1/broker-dealers/7/profile")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    # Full pre-refactor envelope is intact.
    assert set(body) >= {
        "broker_dealer",
        "financials",
        "clearing_arrangements",
        "clearing_memberships",
        "introducing_arrangements",
        "industry_arrangements",
        "recent_alerts",
        "filing_history",
        "executive_contacts",
        "registration_compliance",
        "deficiency_status",
        "is_favorited",
        "favorited_at",
    }
    assert body["broker_dealer"]["id"] == 7
    assert body["broker_dealer"]["name"] == "Stark Securities LLC"
    assert body["registration_compliance"]["registration_status"] == "active"
    assert body["registration_compliance"]["crd_number"] == "123456"
    assert body["deficiency_status"]["is_deficient"] is False
    # Authenticated path surfaces the favorites lookup result.
    assert body["is_favorited"] is True
    assert body["favorited_at"] is not None


async def test_broker_dealer_profile_endpoint_404_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bd_endpoints.repository, "get_broker_dealer", _areturn(None))
    # The builder must never run on a miss, so a favorites call would be a bug.
    monkeypatch.setattr(bd_endpoints, "is_favorited", _must_not_be_awaited)

    app.dependency_overrides[get_current_user] = lambda: _user(["master_list"])
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            response = await client.get("/api/v1/broker-dealers/999/profile")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


# ── Broker-dealer: builder favorites gating (direct call) ────────────────────


async def test_build_broker_dealer_profile_public_skips_favorites_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bd = _bd_fixture()
    _patch_bd_data_methods(monkeypatch)
    monkeypatch.setattr(bd_endpoints, "is_favorited", _must_not_be_awaited)

    result = await bd_endpoints.build_broker_dealer_profile(
        _DB_SENTINEL, bd, user_id=None
    )

    # Public path: favorites never consulted, both fields default off.
    assert result.is_favorited is False
    assert result.favorited_at is None
    # The rest of the envelope still assembles correctly.
    assert result.broker_dealer.id == 7
    assert result.registration_compliance.registration_status == "active"


async def test_build_broker_dealer_profile_authenticated_uses_favorites_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bd = _bd_fixture()
    _patch_bd_data_methods(monkeypatch)
    seen: dict[str, Any] = {}

    async def _fake_is_favorited(db: Any, user_id: str, firm_id: int):
        seen["args"] = (db, user_id, firm_id)
        return True, NOW

    monkeypatch.setattr(bd_endpoints, "is_favorited", _fake_is_favorited)

    result = await bd_endpoints.build_broker_dealer_profile(
        _DB_SENTINEL, bd, user_id="user-abc"
    )

    assert result.is_favorited is True
    assert result.favorited_at == NOW
    # Lookup is called with the threaded db + the caller's user_id + the firm id.
    assert seen["args"] == (_DB_SENTINEL, "user-abc", 7)


# ── Investment advisor: endpoint shape via the thin wrapper ──────────────────


async def test_investment_advisor_profile_endpoint_returns_pre_refactor_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advisor = _advisor_fixture()
    _patch_advisor_data_methods(monkeypatch)
    monkeypatch.setattr(ia_endpoints.repository, "get_investment_advisor", _areturn(advisor))
    monkeypatch.setattr(ia_endpoints, "is_advisor_favorited", _areturn(True))

    app.dependency_overrides[get_current_user] = lambda: _user(["investment_advisors"])
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            response = await client.get("/api/v1/investment-advisors/11/profile")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {
        "advisor",
        "contacts",
        "filings",
        "clearing_memberships",
        "is_favorited",
    }
    assert body["advisor"]["id"] == 11
    assert body["advisor"]["name"] == "Rhodes Capital Advisers LLC"
    assert body["contacts"] == []
    assert body["filings"] == []
    assert body["is_favorited"] is True


async def test_investment_advisor_profile_endpoint_404_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ia_endpoints.repository, "get_investment_advisor", _areturn(None))
    monkeypatch.setattr(ia_endpoints, "is_advisor_favorited", _must_not_be_awaited)

    app.dependency_overrides[get_current_user] = lambda: _user(["investment_advisors"])
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            response = await client.get("/api/v1/investment-advisors/999/profile")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


# ── Investment advisor: builder favorites gating (direct call) ───────────────


async def test_build_investment_advisor_profile_public_skips_favorites_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advisor = _advisor_fixture()
    _patch_advisor_data_methods(monkeypatch)
    monkeypatch.setattr(ia_endpoints, "is_advisor_favorited", _must_not_be_awaited)

    result = await ia_endpoints.build_investment_advisor_profile(
        _DB_SENTINEL, advisor, user_id=None
    )

    assert result.is_favorited is False
    assert result.advisor.id == 11
    assert result.contacts == []
    assert result.filings == []


async def test_build_investment_advisor_profile_authenticated_uses_favorites_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advisor = _advisor_fixture()
    _patch_advisor_data_methods(monkeypatch)
    seen: dict[str, Any] = {}

    async def _fake_is_advisor_favorited(db: Any, user_id: str, advisor_id: int):
        seen["args"] = (db, user_id, advisor_id)
        return True

    monkeypatch.setattr(ia_endpoints, "is_advisor_favorited", _fake_is_advisor_favorited)

    result = await ia_endpoints.build_investment_advisor_profile(
        _DB_SENTINEL, advisor, user_id="user-xyz"
    )

    assert result.is_favorited is True
    assert seen["args"] == (_DB_SENTINEL, "user-xyz", 11)


# ── Bank: endpoint shape + pure builder ──────────────────────────────────────


async def test_bank_detail_endpoint_returns_pre_refactor_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = _bank_fixture()

    async def _get_bank(_db: Any, bank_id: int):
        return bank if bank_id == 7 else None

    monkeypatch.setattr(banks_endpoints.repository, "get_bank", _get_bank)
    app.dependency_overrides[get_current_user] = lambda: _user(["banks"])
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            response = await client.get("/api/v1/banks/7")
            missing = await client.get("/api/v1/banks/999")
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code == 404
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Erebor Bank, NA"
    assert body["charter_status"] == "opened"
    assert [e["action"] for e in body["application_events"]] == ["Consummated/Effective"]
    labels = [link["label"] for link in body["source_links"]]
    assert any("FDIC BankFind" in label for label in labels)
    assert any("Corporate Applications Search" in label for label in labels)
    assert [c["name"] for c in body["contacts"]] == ["Jane Organizer"]


def test_build_bank_detail_is_pure_and_maps_events_links_and_contacts() -> None:
    # Pure sync builder — no db, no await, no per-user state.
    detail = banks_endpoints.build_bank_detail(_bank_fixture())

    assert detail.name == "Erebor Bank, NA"
    assert [e.action for e in detail.application_events] == ["Consummated/Effective"]
    # _build_source_links ran inside: FDIC BankFind (from the cert) + the CAS
    # filing page (from the event that carries a source_url).
    assert any("FDIC BankFind" in link.label for link in detail.source_links)
    assert any("Corporate Applications Search" in link.label for link in detail.source_links)
    # Contact channel arrays are synthesized from the scalar email column.
    assert [c.name for c in detail.contacts] == ["Jane Organizer"]
    assert detail.contacts[0].emails[0].value == "jane.organizer@erebor.example"
