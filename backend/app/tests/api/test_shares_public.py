"""API-layer tests for the public ``/api/v1/public/shares`` routes.

Drive the real routes through the ASGI app with ``get_db_session`` overridden
and the ``lead_shares`` service + ``record_audit`` + ``build_public_profile``
stubbed (same no-DB pattern as ``test_shares_admin.py``). These assert the
security-shaped behaviour of the public surface: the identical unknown/revoked
404, the pre-scrypt brute-force 429, the cookie gate, the IDOR item guard, and
that the public profile projection carries none of the internal-only fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

import app.api.v1.endpoints.shares_public as endpoint
from app.db.session import get_db_session
from app.main import app
from app.schemas.lead_share import (
    PublicBrokerDealerProfile,
    PublicProfileResponse,
    PublicShareItemRow,
)
from app.services import lead_shares as svc

BASE = "/api/v1/public/shares"
TOKEN = "tok_public_abc123"


# ── fixtures / helpers ────────────────────────────────────────────────────────


def _fake_share(share_id: int = 1):
    """Just the attributes the endpoints read directly; every service call that
    would touch other fields is monkeypatched per test."""
    return SimpleNamespace(
        id=share_id,
        name="Q3 Prospects",
        password_hash="scrypt$16384$8$1$c2FsdA==$ZGs=",
        password_generation=1,
    )


class _FakeSession:
    async def commit(self) -> None:  # pragma: no cover - trivial
        pass

    async def flush(self) -> None:  # pragma: no cover - trivial
        pass

    def add(self, _obj) -> None:  # pragma: no cover - trivial
        pass


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _override_db() -> None:
    app.dependency_overrides[get_db_session] = lambda: _FakeSession()


def _patch_share(monkeypatch, share) -> None:
    """Resolve ``get_active_share_by_token`` to ``share`` (or None)."""

    async def _get(_db, _token):
        return share

    monkeypatch.setattr(svc, "get_active_share_by_token", _get)


def _patch_cookie(monkeypatch, unlocked: bool) -> None:
    monkeypatch.setattr(svc, "verify_share_cookie", lambda *a, **k: unlocked)


async def _noaudit(*a, **k):
    return None


def _fake_internal_bd_profile():
    """A stand-in for the internal ``BrokerDealerProfileResponse`` carrying the
    internal-only fields (lead_score / is_favorited / unknown_reason) so the
    ``from_internal`` allowlist can be proven to drop them."""
    broker_dealer = SimpleNamespace(
        name="Acme Securities LLC",
        crd_number="123456",
        sec_file_number="8-12345",
        cik="0000012345",
        city="New York",
        state="NY",
        website="https://acme.example",
        status="Active",
        registration_date=None,
        formation_date=None,
        business_type=None,
        branch_count=None,
        dba_names=None,
        types_of_business=None,
        types_of_business_other=None,
        types_of_business_total=None,
        firm_operations_text=None,
        direct_owners=None,
        executive_officers=None,
        member_agencies=["FINRA"],
        current_clearing_partner="Pershing",
        current_clearing_type=None,
        last_filing_date=None,
        filings_index_url=None,
        latest_net_capital=1_000_000.0,
        latest_excess_net_capital=None,
        latest_total_assets=None,
        required_min_capital=None,
        yoy_growth=None,
        three_year_cagr=None,
        health_status="healthy",
        is_deficient=False,
        latest_deficiency_filed_at=None,
        # Internal-only fields that must NOT survive the projection:
        lead_score=99.0,
        is_favorited=True,
        unknown_reason={"code": "SHOULD_NOT_LEAK"},
    )
    # A populated contact whose emails[]/phones[] carry the internal per-hit
    # ``source`` (vendor) + ``confidence`` — these MUST be dropped (M1).
    contact = SimpleNamespace(
        name="Jane Scout",
        title="COO",
        role_context=None,
        email="scout@acme.example",
        phone="+15550000",
        linkedin_url=None,
        emails=[SimpleNamespace(value="scout@acme.example", type="work", source="apollo", confidence=0.9)],
        phones=[SimpleNamespace(value="+15550000", type="mobile", source="pdl", confidence=0.8)],
    )
    return SimpleNamespace(
        broker_dealer=broker_dealer,
        financials=[],
        clearing_arrangements=[],
        clearing_memberships=[],
        introducing_arrangements=[],
        industry_arrangements=[],
        registration_compliance=None,
        deficiency_status=None,
        executive_contacts=[contact],
    )


# ── unknown / revoked token → identical 404 on every route ────────────────────

_ALL_ROUTES = [
    ("get", f"{BASE}/{TOKEN}", None),
    ("post", f"{BASE}/{TOKEN}/unlock", {"password": "ABCD-EFGH-JKMN"}),
    ("get", f"{BASE}/{TOKEN}/items", None),
    ("get", f"{BASE}/{TOKEN}/items/5/profile", None),
]


async def test_unknown_token_404_on_all_routes(monkeypatch) -> None:
    _patch_share(monkeypatch, None)  # service returns None for unknown tokens
    _override_db()
    try:
        async with _client() as client:
            for method, path, body in _ALL_ROUTES:
                resp = await client.request(method, path, json=body)
                assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"
                assert resp.json() == {"detail": "Not found."}
    finally:
        app.dependency_overrides.clear()


async def test_revoked_share_404_byte_identical_to_unknown(monkeypatch) -> None:
    # The service collapses both unknown and revoked to None, so the endpoint
    # cannot tell them apart — the 404 payload must be byte-identical.
    _patch_share(monkeypatch, None)
    _override_db()
    try:
        async with _client() as client:
            unknown = await client.get(f"{BASE}/does-not-exist")
            revoked = await client.get(f"{BASE}/was-revoked")
    finally:
        app.dependency_overrides.clear()
    assert unknown.status_code == revoked.status_code == 404
    assert unknown.content == revoked.content
    assert unknown.content == b'{"detail":"Not found."}'


# ── meta: locked vs unlocked ──────────────────────────────────────────────────


async def test_meta_locked_hides_item_count(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share())
    _patch_cookie(monkeypatch, unlocked=False)
    _override_db()
    try:
        async with _client() as client:
            resp = await client.get(f"{BASE}/{TOKEN}")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"name": "Q3 Prospects", "unlocked": False, "item_count": None}


async def test_meta_unlocked_reports_item_count(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share())
    _patch_cookie(monkeypatch, unlocked=True)

    async def _items(_db, _share):
        return [object(), object(), object()]

    monkeypatch.setattr(svc, "list_share_items", _items)
    _override_db()
    try:
        async with _client() as client:
            resp = await client.get(f"{BASE}/{TOKEN}")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "name": "Q3 Prospects",
        "unlocked": True,
        "item_count": 3,
    }


# ── unlock ────────────────────────────────────────────────────────────────────


async def test_unlock_wrong_password_403_and_registers_failure(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share())
    monkeypatch.setattr(svc, "unlock_retry_after", lambda *a, **k: 0)
    monkeypatch.setattr(svc, "verify_share_password", lambda *a, **k: False)

    registered = {"count": 0}

    async def _register(_db, _share, *, now):
        registered["count"] += 1

    monkeypatch.setattr(svc, "register_failed_unlock", _register)
    monkeypatch.setattr(endpoint, "record_audit", _noaudit)
    _override_db()
    try:
        async with _client() as client:
            resp = await client.post(
                f"{BASE}/{TOKEN}/unlock", json={"password": "WRON-GPAS-SXXX"}
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403
    assert resp.json()["detail"] == "invalid_password"
    assert registered["count"] == 1
    # A failed unlock must never mint a cookie.
    assert resp.headers.get_list("set-cookie") == []


async def test_unlock_correct_password_sets_single_hardened_cookie(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share(share_id=1))
    monkeypatch.setattr(svc, "unlock_retry_after", lambda *a, **k: 0)
    monkeypatch.setattr(svc, "verify_share_password", lambda *a, **k: True)

    async def _record_ok(_db, _share, *, now):
        return None

    monkeypatch.setattr(svc, "record_successful_unlock", _record_ok)
    monkeypatch.setattr(
        svc,
        "mint_share_cookie",
        lambda share, *, now: ("dox_share_1", "v1.1.1.9999999999.sig", 43_200),
    )

    async def _items(_db, _share):
        return [object(), object()]

    monkeypatch.setattr(svc, "list_share_items", _items)
    monkeypatch.setattr(endpoint, "record_audit", _noaudit)
    _override_db()
    try:
        async with _client() as client:
            resp = await client.post(
                f"{BASE}/{TOKEN}/unlock", json={"password": "ABCD-EFGH-JKMN"}
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"name": "Q3 Prospects", "unlocked": True, "item_count": 2}

    cookies = resp.headers.get_list("set-cookie")
    assert len(cookies) == 1, cookies  # EXACTLY one Set-Cookie
    cookie = cookies[0]
    lowered = cookie.lower()
    assert cookie.startswith("dox_share_1=")
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "path=/" in lowered
    # development environment → not marked Secure (so localhost http works).
    assert "secure" not in lowered


async def test_unlock_throttled_429_before_scrypt(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share())
    monkeypatch.setattr(svc, "unlock_retry_after", lambda *a, **k: 300)

    reached = {"scrypt": False}

    def _boom(*a, **k):
        reached["scrypt"] = True
        raise AssertionError("scrypt verify must not run while throttled")

    monkeypatch.setattr(svc, "verify_share_password", _boom)
    monkeypatch.setattr(endpoint, "record_audit", _noaudit)
    _override_db()
    try:
        async with _client() as client:
            resp = await client.post(
                f"{BASE}/{TOKEN}/unlock", json={"password": "ABCD-EFGH-JKMN"}
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 429
    assert resp.json()["detail"] == "too_many_attempts"
    assert resp.headers["Retry-After"] == "300"
    assert reached["scrypt"] is False  # never reached the expensive verify


# ── items: cookie gate ────────────────────────────────────────────────────────


async def test_items_without_cookie_401(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share())
    _patch_cookie(monkeypatch, unlocked=False)
    _override_db()
    try:
        async with _client() as client:
            resp = await client.get(f"{BASE}/{TOKEN}/items")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 401
    assert resp.json()["detail"] == "share_locked"


async def test_items_with_stale_cookie_401(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share())
    _patch_cookie(monkeypatch, unlocked=False)  # a stale/invalid cookie fails verify
    _override_db()
    try:
        async with _client() as client:
            resp = await client.get(
                f"{BASE}/{TOKEN}/items",
                cookies={svc.share_cookie_name(1): "v1.1.0.1.staledead"},
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 401
    assert resp.json()["detail"] == "share_locked"


async def test_items_unlocked_returns_list(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share())
    _patch_cookie(monkeypatch, unlocked=True)

    touched = {"count": 0}

    async def _touch(_db, _share, *, now):
        touched["count"] += 1

    async def _items(_db, _share):
        return [
            PublicShareItemRow(
                item_id=11, kind="broker_dealer", position=1, name="Acme Securities"
            )
        ]

    monkeypatch.setattr(svc, "touch_last_viewed", _touch)
    monkeypatch.setattr(svc, "list_share_items", _items)
    _override_db()
    try:
        async with _client() as client:
            resp = await client.get(f"{BASE}/{TOKEN}/items")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Q3 Prospects"
    assert body["items"][0]["item_id"] == 11
    assert body["items"][0]["name"] == "Acme Securities"
    assert touched["count"] == 1


# ── profile: cookie gate + IDOR guard + allowlist projection ──────────────────


async def test_profile_locked_401(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share())
    _patch_cookie(monkeypatch, unlocked=False)
    _override_db()
    try:
        async with _client() as client:
            resp = await client.get(f"{BASE}/{TOKEN}/items/5/profile")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 401
    assert resp.json()["detail"] == "share_locked"


async def test_profile_foreign_item_id_404_idor(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share())
    _patch_cookie(monkeypatch, unlocked=True)

    async def _get_item(_db, *, share_id, item_id):
        return None  # foreign item id doesn't resolve within this share

    monkeypatch.setattr(svc, "get_item_for_share", _get_item)
    _override_db()
    try:
        async with _client() as client:
            resp = await client.get(f"{BASE}/{TOKEN}/items/999/profile")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not found."}


async def test_profile_public_bd_projection_drops_internal_fields(monkeypatch) -> None:
    _patch_share(monkeypatch, _fake_share())
    _patch_cookie(monkeypatch, unlocked=True)

    async def _get_item(_db, *, share_id, item_id):
        return SimpleNamespace(id=item_id, broker_dealer_id=7)

    async def _touch(_db, _share, *, now):
        return None

    async def _build(_db, item):
        public = PublicBrokerDealerProfile.from_internal(_fake_internal_bd_profile())
        return PublicProfileResponse(
            item_id=item.id, kind="broker_dealer", broker_dealer=public
        )

    monkeypatch.setattr(svc, "get_item_for_share", _get_item)
    monkeypatch.setattr(svc, "touch_last_viewed", _touch)
    monkeypatch.setattr(endpoint, "build_public_profile", _build)
    _override_db()
    try:
        async with _client() as client:
            resp = await client.get(f"{BASE}/{TOKEN}/items/7/profile")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "broker_dealer"
    assert body["broker_dealer"]["name"] == "Acme Securities LLC"
    # The internal-only fields must be absent everywhere in the payload.
    for forbidden in ("lead_score", "is_favorited", "unknown_reason"):
        assert forbidden not in resp.text
        assert forbidden not in body["broker_dealer"]
    # M1: per-hit vendor/source + confidence dropped, but the value survives.
    contact = body["broker_dealer"]["executive_contacts"][0]
    assert contact["emails"][0] == {"value": "scout@acme.example", "type": "work"}
    assert contact["phones"][0] == {"value": "+15550000", "type": "mobile"}
    assert "confidence" not in contact["emails"][0]
    assert "apollo" not in resp.text and "pdl" not in resp.text
