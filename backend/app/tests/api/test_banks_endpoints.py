"""API-layer tests for ``GET /api/v1/banks*``.

Drive the real routes through the ASGI app with auth + db dependencies
overridden and the repository stubbed (same pattern as
``test_broker_dealers_filter_parsing.py``): auth/permission gates, query-
param parsing into repository kwargs, the 422 window guard, 404s, and the
detail response shape (event timeline + official source links). No
database required — runs in the default (non-integration) suite.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

import app.api.v1.endpoints.banks as banks_endpoints
from app.db.session import get_db_session
from app.main import app
from app.models.bank import Bank, BankApplicationEvent
from app.schemas.auth import AuthenticatedUser
from app.schemas.bank import BankListMeta, BankListResponse
from app.services.auth import get_current_user


def _user(permissions: list[str], role: str = "viewer") -> AuthenticatedUser:
    return AuthenticatedUser(
        id="test-user",
        name="Test User",
        email="test-user@example.com",
        role=role,
        feature_permissions=permissions,
        session_expires_at=datetime(2099, 1, 1),
    )


def _empty_response() -> BankListResponse:
    return BankListResponse(
        items=[], meta=BankListMeta(page=1, limit=25, total=0, total_pages=1)
    )


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _call_with_captured_kwargs(monkeypatch, params) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def _capture(_db, **kwargs):
        captured.update(kwargs)
        return _empty_response()

    monkeypatch.setattr(banks_endpoints.repository, "list_banks", _capture)
    app.dependency_overrides[get_current_user] = lambda: _user(["banks"])
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            response = await client.get("/api/v1/banks", params=params)
        assert response.status_code == 200, response.text
        return captured
    finally:
        app.dependency_overrides.clear()


# ── Auth / permission gate ──────────────────────────────────────────────────


async def test_unauthenticated_is_401() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/banks")
    assert response.status_code == 401


async def test_viewer_without_banks_permission_is_403(monkeypatch) -> None:
    app.dependency_overrides[get_current_user] = lambda: _user(["master_list"])
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            list_resp = await client.get("/api/v1/banks")
            states_resp = await client.get("/api/v1/banks/states")
            detail_resp = await client.get("/api/v1/banks/1")
    finally:
        app.dependency_overrides.clear()
    assert (list_resp.status_code, states_resp.status_code, detail_resp.status_code) == (
        403, 403, 403,
    )


async def test_admin_bypasses_permission_gate(monkeypatch) -> None:
    async def _capture(_db, **kwargs):
        return _empty_response()

    monkeypatch.setattr(banks_endpoints.repository, "list_banks", _capture)
    app.dependency_overrides[get_current_user] = lambda: _user([], role="admin")
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            response = await client.get("/api/v1/banks")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200


# ── Query-param parsing ─────────────────────────────────────────────────────


async def test_multi_selects_accept_repeat_and_comma_forms(monkeypatch) -> None:
    captured = await _call_with_captured_kwargs(
        monkeypatch,
        [
            ("state", "NY,CA"),
            ("state", "FL"),
            ("charter_authority", "OCC"),
            ("charter_status", "pending,approved"),
        ],
    )
    assert captured["states"] == ["NY", "CA", "FL"]
    assert captured["charter_authorities"] == ["OCC"]
    assert captured["charter_statuses"] == ["pending", "approved"]


async def test_defaults_reach_repository(monkeypatch) -> None:
    captured = await _call_with_captured_kwargs(monkeypatch, {})
    assert captured["sort_by"] == "established_date"
    assert captured["sort_dir"] == "desc"
    assert captured["digital_assets"] is None
    assert captured["page"] == 1 and captured["limit"] == 25


async def test_digital_assets_and_window_pass_through(monkeypatch) -> None:
    captured = await _call_with_captured_kwargs(
        monkeypatch,
        {
            "digital_assets": "true",
            "established_after": "2026-01-01",
            "established_before": "2026-07-02",
            "search": "trust",
        },
    )
    assert captured["digital_assets"] is True
    assert captured["established_after"] == date(2026, 1, 1)
    assert captured["established_before"] == date(2026, 7, 2)
    assert captured["search"] == "trust"


async def test_backwards_window_is_422(monkeypatch) -> None:
    async def _explode(_db, **kwargs):  # pragma: no cover
        raise AssertionError("repository must not be reached on a 422")

    monkeypatch.setattr(banks_endpoints.repository, "list_banks", _explode)
    app.dependency_overrides[get_current_user] = lambda: _user(["banks"])
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/banks",
                params={"established_after": "2026-07-02", "established_before": "2026-01-01"},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


# ── Detail ──────────────────────────────────────────────────────────────────


def _bank_fixture() -> Bank:
    now = datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc)
    bank = Bank(
        id=7,
        fdic_cert="59378",
        fed_rssd="1234567",
        occ_charter_number="25301",
        occ_control_number="2025-Charter-342076",
        name="Erebor Bank, NA",
        address="500 Neil Avenue, Suite 140",
        city="Columbus",
        state="OH",
        zip="43215",
        website=None,
        charter_authority="OCC",
        bkclass="N",
        regulator="OCC",
        charter_status="opened",
        established_date=date(2026, 2, 6),
        insured_date=date(2026, 2, 6),
        application_received_date=date(2025, 6, 12),
        last_action_date=date(2026, 2, 6),
        asset=None,
        deposits=None,
        offices=1,
        active=True,
        digital_assets=True,
        digital_asset_pdfs=[
            {
                "title": "Erebor Bank, NA",
                "url": "https://www.occ.gov/x/erebor.pdf",
                "received_date": "2025-06-12",
            }
        ],
        source="fdic+occ",
        fdic_checked_at=now,
        occ_checked_at=now,
        created_at=now,
        updated_at=now,
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
            created_at=now,
        ),
        BankApplicationEvent(
            id=1,
            bank_id=7,
            action="Receipt",
            action_date=date(2025, 6, 12),
            filing_type="New Bank Charter",
            source_url=None,
            created_at=now,
        ),
    ]
    return bank


async def test_detail_shape_includes_timeline_and_source_links(monkeypatch) -> None:
    bank = _bank_fixture()

    async def _get_bank(_db, bank_id: int):
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
    assert body["digital_assets"] is True
    assert [e["action"] for e in body["application_events"]] == [
        "Consummated/Effective", "Receipt",
    ]
    labels = [link["label"] for link in body["source_links"]]
    urls = [link["url"] for link in body["source_links"]]
    # FDIC BankFind page (from the cert), the CAS filing page (from the
    # newest event that carries one), and the digital-assets PDF.
    assert any("FDIC BankFind" in label for label in labels)
    assert "https://banks.data.fdic.gov/bankfind-suite/bankfind/institution/59378" in urls
    assert any("Corporate Applications Search" in label for label in labels)
    assert any(url.endswith("erebor.pdf") for url in urls)
    # PDF links also surface structurally, not just as source links.
    assert body["digital_asset_pdfs"][0]["url"] == "https://www.occ.gov/x/erebor.pdf"


async def test_list_response_model_tolerates_null_pdfs(monkeypatch) -> None:
    """digital_asset_pdfs is NULL for most rows — the schema coerces to []."""
    bank = _bank_fixture()
    bank.digital_assets = False
    bank.digital_asset_pdfs = None

    async def _list(_db, **kwargs):
        from app.schemas.bank import BankListItem

        return BankListResponse(
            items=[BankListItem.model_validate(bank)],
            meta=BankListMeta(page=1, limit=25, total=1, total_pages=1),
        )

    monkeypatch.setattr(banks_endpoints.repository, "list_banks", _list)
    app.dependency_overrides[get_current_user] = lambda: _user(["banks"])
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            response = await client.get("/api/v1/banks")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["digital_asset_pdfs"] == []
    assert item["fdic_cert"] == "59378"
