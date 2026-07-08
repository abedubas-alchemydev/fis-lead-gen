"""API-layer tests for the admin ``/api/v1/shares`` routes.

Drive the real routes through the ASGI app with auth + db overridden and the
``lead_shares`` service + ``record_audit`` stubbed (same pattern as
``test_banks_endpoints.py``). No database required — default suite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

import app.api.v1.endpoints.shares_admin as endpoint
from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.schemas.lead_share import ShareSkippedItems
from app.services import lead_shares as svc
from app.services.auth import get_current_user


def _user(role: str = "admin", permissions: list[str] | None = None) -> AuthenticatedUser:
    return AuthenticatedUser(
        id="admin-1",
        name="Admin",
        email="admin@example.com",
        role=role,
        feature_permissions=permissions or [],
        session_expires_at=datetime(2099, 1, 1),
    )


def _fake_share(share_id: int = 1, *, revoked: bool = False):
    return SimpleNamespace(
        id=share_id,
        name="Q3 Prospects",
        token="tok_abc123",
        created_by="admin-1",
        created_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
        revoked_at=datetime(2026, 7, 8, tzinfo=timezone.utc) if revoked else None,
        password_rotated_at=None,
        view_count=0,
        last_viewed_at=None,
        source_favorite_list_id=42,
    )


class _FakeSession:
    """Minimal async session — only the incidental calls the endpoints make
    outside the (monkeypatched) service functions."""

    def __init__(self, scalar_value: int = 0) -> None:
        self._scalar = scalar_value

    async def scalar(self, *a, **k) -> int:
        return self._scalar

    async def commit(self) -> None:  # pragma: no cover - trivial
        pass

    async def flush(self) -> None:  # pragma: no cover - trivial
        pass

    def add(self, _obj) -> None:  # pragma: no cover - trivial
        pass


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _override(user: AuthenticatedUser, session=None) -> None:
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: session or _FakeSession()


# ── auth / permission gate ───────────────────────────────────────────────────

_ROUTES = [
    ("post", "/api/v1/shares"),
    ("get", "/api/v1/shares"),
    ("get", "/api/v1/shares/1"),
    ("post", "/api/v1/shares/1/rotate-password"),
    ("post", "/api/v1/shares/1/revoke"),
    ("post", "/api/v1/shares/1/unrevoke"),
    ("delete", "/api/v1/shares/1"),
]


async def test_all_routes_401_unauthenticated() -> None:
    async with _client() as client:
        for method, path in _ROUTES:
            resp = await client.request(method, path, json={})
            assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


async def test_all_routes_403_for_viewer() -> None:
    # A viewer with every feature permission is still not an admin.
    _override(_user(role="viewer", permissions=["my_favorites", "master_list"]))
    try:
        async with _client() as client:
            for method, path in _ROUTES:
                resp = await client.request(
                    method, path, json={"favorite_list_id": 1}
                )
                assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ── create ───────────────────────────────────────────────────────────────────


async def test_create_happy_returns_password_and_skipped(monkeypatch) -> None:
    async def _create(_db, **kwargs):
        return _fake_share(9), "ABCD-EFGH-JKMN", ShareSkippedItems(
            institutional_investors=2, reporting_owners=1
        )

    monkeypatch.setattr(svc, "create_share_from_favorite_list", _create)

    async def _noaudit(*a, **k):
        return None

    monkeypatch.setattr(endpoint, "record_audit", _noaudit)
    _override(_user(), session=_FakeSession(scalar_value=5))
    try:
        async with _client() as client:
            resp = await client.post("/api/v1/shares", json={"favorite_list_id": 42})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["password"] == "ABCD-EFGH-JKMN"
    assert body["skipped"] == {"institutional_investors": 2, "reporting_owners": 1}
    assert body["share"]["item_count"] == 5
    assert len(body["warnings"]) == 2
    assert "password_hash" not in resp.text


async def test_create_error_codes_map_to_status(monkeypatch) -> None:
    cases = [
        ("favorite_list_not_found", None, 404),
        ("share_source_empty", None, 400),
        ("share_too_large", 90, 400),
    ]
    for code, eligible, expected in cases:
        async def _raise(_db, _code=code, _elig=eligible, **kwargs):
            raise svc.ShareCreateError(_code, eligible=_elig)

        monkeypatch.setattr(svc, "create_share_from_favorite_list", _raise)
        _override(_user())
        try:
            async with _client() as client:
                resp = await client.post(
                    "/api/v1/shares", json={"favorite_list_id": 1}
                )
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == expected, f"{code}: {resp.status_code}"
        if code == "share_too_large":
            assert resp.json()["detail"]["max"] == svc.MAX_SHARE_ITEMS
            assert resp.json()["detail"]["eligible"] == 90


# ── lifecycle 409s + not-found ───────────────────────────────────────────────


async def test_rotate_on_revoked_is_409(monkeypatch) -> None:
    async def _get(_db, _id):
        return _fake_share(_id, revoked=True)

    monkeypatch.setattr(svc, "get_share", _get)
    _override(_user())
    try:
        async with _client() as client:
            resp = await client.post("/api/v1/shares/1/rotate-password")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 409
    assert resp.json()["detail"] == "share_revoked"


async def test_double_revoke_is_409(monkeypatch) -> None:
    async def _get(_db, _id):
        return _fake_share(_id, revoked=True)

    monkeypatch.setattr(svc, "get_share", _get)
    _override(_user())
    try:
        async with _client() as client:
            resp = await client.post("/api/v1/shares/1/revoke")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 409
    assert resp.json()["detail"] == "share_already_revoked"


async def test_unrevoke_when_active_is_409(monkeypatch) -> None:
    async def _get(_db, _id):
        return _fake_share(_id, revoked=False)

    monkeypatch.setattr(svc, "get_share", _get)
    _override(_user())
    try:
        async with _client() as client:
            resp = await client.post("/api/v1/shares/1/unrevoke")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 409
    assert resp.json()["detail"] == "share_not_revoked"


async def test_unknown_share_is_404(monkeypatch) -> None:
    async def _get(_db, _id):
        return None

    monkeypatch.setattr(svc, "get_share", _get)
    _override(_user())
    try:
        async with _client() as client:
            resp = await client.post("/api/v1/shares/999/revoke")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


async def test_list_response_never_leaks_hash(monkeypatch) -> None:
    async def _list(_db):
        return [(_fake_share(1), 3, "admin@example.com")]

    monkeypatch.setattr(svc, "list_shares", _list)
    _override(_user())
    try:
        async with _client() as client:
            resp = await client.get("/api/v1/shares")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "password_hash" not in resp.text
    assert resp.json()["items"][0]["item_count"] == 3
