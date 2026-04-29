"""Auth dual-path dependency tests for Tier 2 pipeline endpoints.

``_ensure_admin_or_scheduler_sa`` lets two distinct callers reach the same
endpoint:

  - the /settings/pipelines admin UI, which sends a BetterAuth session cookie
    that resolves to a user with ``role == "admin"``; and
  - Cloud Scheduler jobs, which send an ``Authorization: Bearer <id_token>``
    where the token is a Google-signed OIDC token whose ``email`` claim is
    the runtime SA configured in ``settings.cloud_scheduler_sa_email`` and
    whose ``aud`` claim is ``settings.backend_audience``.

Anything else — anonymous, non-admin cookie, wrong-email OIDC, malformed
OIDC — must 403 with the same shape so the failure mode is uniform.

These tests don't hit Postgres or the Google token endpoint. The
``id_token.verify_oauth2_token`` call is monkeypatched so the auth helper
runs end-to-end without needing real tokens.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.schemas.auth import AuthenticatedUser
from app.services.auth import _ensure_admin_or_scheduler_sa


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="admin-1",
        name="Admin User",
        email="admin@example.com",
        role="admin",
        session_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


def _viewer() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="viewer-1",
        name="Viewer User",
        email="viewer@example.com",
        role="viewer",
        session_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


class _FakeRequest:
    """Tiny stand-in for FastAPI's ``Request`` exposing only ``.headers``.

    The dependency's only ``request.<...>`` use is ``request.headers.get(...)``
    so a dict-headers shim is enough; building a real Starlette Request
    requires ASGI scope plumbing that adds noise to these tests.
    """

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


async def test_admin_cookie_passes_through_returns_email() -> None:
    """A current_user with role=admin short-circuits to the cookie path."""
    request = _FakeRequest()
    identity = await _ensure_admin_or_scheduler_sa(
        request=request,  # type: ignore[arg-type]
        current_user=_admin(),
    )
    assert identity == "admin@example.com"


async def test_non_admin_cookie_falls_through_then_403() -> None:
    """Viewer cookie does not short-circuit; without an OIDC header → 403."""
    request = _FakeRequest()
    with pytest.raises(HTTPException) as excinfo:
        await _ensure_admin_or_scheduler_sa(
            request=request,  # type: ignore[arg-type]
            current_user=_viewer(),
        )
    assert excinfo.value.status_code == 403


async def test_anonymous_no_cookie_no_oidc_returns_403() -> None:
    """No cookie + no Authorization header → 403 (uniform failure shape)."""
    request = _FakeRequest()
    with pytest.raises(HTTPException) as excinfo:
        await _ensure_admin_or_scheduler_sa(
            request=request,  # type: ignore[arg-type]
            current_user=None,
        )
    assert excinfo.value.status_code == 403


async def test_valid_oidc_token_with_correct_email_returns_sa_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified token with ``email == settings.cloud_scheduler_sa_email``
    returns ``"sa:<email>"`` so handlers can record a distinguishable
    trigger source."""

    def _fake_verify(token: str, _request: Any, audience: str) -> dict[str, Any]:
        assert token == "valid-token"
        assert audience == settings.backend_audience
        return {"email": settings.cloud_scheduler_sa_email, "aud": audience}

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", _fake_verify)

    request = _FakeRequest({"Authorization": "Bearer valid-token"})
    identity = await _ensure_admin_or_scheduler_sa(
        request=request,  # type: ignore[arg-type]
        current_user=None,
    )
    assert identity == f"sa:{settings.cloud_scheduler_sa_email}"


async def test_oidc_token_with_wrong_email_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token verifies but the ``email`` claim is not the configured SA → 403.

    Guards against an attacker minting a Google-signed token from a
    different service account that happens to share the same audience.
    """

    def _fake_verify(_token: str, _request: Any, audience: str) -> dict[str, Any]:
        del audience  # asserted via the success test; unused here.
        return {"email": "imposter@malicious.example.com"}

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", _fake_verify)

    request = _FakeRequest({"Authorization": "Bearer good-shape-token"})
    with pytest.raises(HTTPException) as excinfo:
        await _ensure_admin_or_scheduler_sa(
            request=request,  # type: ignore[arg-type]
            current_user=None,
        )
    assert excinfo.value.status_code == 403


async def test_invalid_oidc_token_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``verify_oauth2_token`` raises ValueError for malformed / expired /
    wrong-audience tokens — the helper must surface that as a 403."""

    def _fake_verify(_token: str, _request: Any, audience: str) -> dict[str, Any]:
        del audience
        raise ValueError("Token expired")

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", _fake_verify)

    request = _FakeRequest({"Authorization": "Bearer expired-token"})
    with pytest.raises(HTTPException) as excinfo:
        await _ensure_admin_or_scheduler_sa(
            request=request,  # type: ignore[arg-type]
            current_user=None,
        )
    assert excinfo.value.status_code == 403


async def test_empty_bearer_token_returns_403() -> None:
    """``Authorization: Bearer `` (empty token) shouldn't reach
    ``verify_oauth2_token`` — short-circuit to 403 before the network call."""
    request = _FakeRequest({"Authorization": "Bearer "})
    with pytest.raises(HTTPException) as excinfo:
        await _ensure_admin_or_scheduler_sa(
            request=request,  # type: ignore[arg-type]
            current_user=None,
        )
    assert excinfo.value.status_code == 403


async def test_admin_cookie_takes_precedence_over_oidc_header() -> None:
    """If both are present the admin cookie wins; ``verify_oauth2_token``
    must not be reached. Avoids a confusing failure where a valid admin
    session would be rejected because of a malformed Authorization header."""
    request = _FakeRequest({"Authorization": "Bearer something"})
    identity = await _ensure_admin_or_scheduler_sa(
        request=request,  # type: ignore[arg-type]
        current_user=_admin(),
    )
    assert identity == "admin@example.com"
