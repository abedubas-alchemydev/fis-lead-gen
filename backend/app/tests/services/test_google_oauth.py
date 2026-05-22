"""Unit tests for google_oauth.get_fresh_google_access_token.

The function does one DB read, one optional DB write, and one HTTP call
to Google's token endpoint. We stub the DB layer with a tiny in-memory
``Account``-shaped object so the tests stay hermetic — the goal here is
to lock the refresh / not-linked branching, not to re-test SQLAlchemy.

Integration coverage for the endpoint that wraps this (
``POST /api/v1/outreach/send``) lives separately and is gated on a live
Postgres so the Account row read can exercise the real account row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import respx

from app.services.google_oauth import (
    GoogleAccountNotLinked,
    _parse_scopes,
    _refresh_access_token,
)


_TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass
class _FakeAccount:
    """In-memory stand-in for the SQLAlchemy ``Account`` ORM row.

    We only need the fields ``get_fresh_google_access_token`` touches
    (the rest of the column set is irrelevant to the refresh logic).
    Keyed by ``id`` (Better Auth's PK) now that the helper accepts
    ``account_id`` instead of ``user_id``.
    """

    id: str = "acc-1"
    user_id: str = "user-1"
    provider_id: str = "google"
    access_token: str | None = None
    refresh_token: str | None = None
    access_token_expires_at: datetime | None = None
    scope: str | None = None


@dataclass
class _FakeResult:
    obj: _FakeAccount | None

    def scalar_one_or_none(self) -> _FakeAccount | None:
        return self.obj


@dataclass
class _FakeSession:
    """Minimal ``AsyncSession`` stub: returns the seeded Account row.

    Only ``execute`` is used by ``get_fresh_google_access_token``. The
    function mutates the returned Account in-place to persist the new
    access token; we don't need a real commit because tests inspect the
    in-memory object directly.
    """

    account: _FakeAccount | None
    executions: list[Any] = field(default_factory=list)

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executions.append(stmt)
        return _FakeResult(self.account)


def test_parse_scopes_handles_space_separated_and_empty() -> None:
    assert _parse_scopes(None) == []
    assert _parse_scopes("") == []
    assert _parse_scopes("openid email") == ["openid", "email"]
    # Real-world Better Auth writes scopes back with internal extra
    # whitespace after incremental consent — ``split()`` (no arg)
    # handles that for us; lock the behavior.
    assert _parse_scopes("openid  email\nprofile") == [
        "openid",
        "email",
        "profile",
    ]


def test_parse_scopes_handles_comma_separated() -> None:
    """Better Auth 1.3.6 actually persists scopes comma-separated in the
    ``account.scope`` column, not space-separated. Observed on staging
    after first OAuth signin: ``"https://.../userinfo.profile,...,openid"``.
    The parser must split on both so the gmail.send membership check
    works regardless of which version wrote the row."""
    raw = (
        "https://www.googleapis.com/auth/userinfo.profile,"
        "https://www.googleapis.com/auth/userinfo.email,"
        "openid,"
        "https://www.googleapis.com/auth/gmail.send"
    )
    scopes = _parse_scopes(raw)
    assert "https://www.googleapis.com/auth/gmail.send" in scopes
    assert "openid" in scopes
    assert len(scopes) == 4


def test_parse_scopes_handles_mixed_comma_and_space() -> None:
    """Belt and braces — if a future Better Auth bump flips back to
    spaces or mixes the two, the parser should still produce a clean
    list."""
    assert _parse_scopes("openid email, profile") == [
        "openid",
        "email",
        "profile",
    ]


async def test_get_fresh_token_raises_not_linked_when_no_google_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import google_oauth

    session = _FakeSession(account=None)
    with pytest.raises(GoogleAccountNotLinked):
        await google_oauth.get_fresh_google_access_token(
            db=session, account_id="acc-1"  # type: ignore[arg-type]
        )


async def test_get_fresh_token_returns_existing_when_not_near_expiry() -> None:
    from app.services import google_oauth

    far_future = datetime.now(timezone.utc) + timedelta(hours=1)
    account = _FakeAccount(
        access_token="existing-token",
        refresh_token="refresh-1",
        access_token_expires_at=far_future,
        scope="openid email https://www.googleapis.com/auth/gmail.send",
    )
    session = _FakeSession(account=account)

    token, scopes = await google_oauth.get_fresh_google_access_token(
        db=session, account_id="acc-1"  # type: ignore[arg-type]
    )

    assert token == "existing-token"
    assert "https://www.googleapis.com/auth/gmail.send" in scopes


@respx.mock
async def test_get_fresh_token_refreshes_when_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import google_oauth
    from app.core import config

    # The refresh call reads ``settings.google_client_id`` / ``_secret``;
    # the test environment may have them blank, so set explicitly.
    monkeypatch.setattr(config.settings, "google_client_id", "client-id-test")
    monkeypatch.setattr(
        config.settings, "google_client_secret", "client-secret-test"
    )

    respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "new-token", "expires_in": 3600}
        )
    )

    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    account = _FakeAccount(
        access_token="old-token",
        refresh_token="refresh-1",
        access_token_expires_at=past,
        scope="openid email",
    )
    session = _FakeSession(account=account)

    token, scopes = await google_oauth.get_fresh_google_access_token(
        db=session, account_id="acc-1"  # type: ignore[arg-type]
    )

    assert token == "new-token"
    # Mutation in-place — caller's commit picks this up.
    assert account.access_token == "new-token"
    assert account.access_token_expires_at is not None
    assert account.access_token_expires_at > datetime.now(timezone.utc)
    assert scopes == ["openid", "email"]


async def test_get_fresh_token_raises_not_linked_when_refresh_token_missing() -> None:
    from app.services import google_oauth

    # Google can omit the refresh_token on silent re-auth without
    # prompt=consent — when that happens we can't mint a new access
    # token, so force a re-link.
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    account = _FakeAccount(
        access_token="old-token",
        refresh_token=None,
        access_token_expires_at=past,
        scope="openid email",
    )
    session = _FakeSession(account=account)

    with pytest.raises(GoogleAccountNotLinked):
        await google_oauth.get_fresh_google_access_token(
            db=session, account_id="acc-1"  # type: ignore[arg-type]
        )


@respx.mock
async def test_refresh_access_token_maps_invalid_grant_to_not_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google uses 400 + ``error=invalid_grant`` when the refresh token
    was revoked at https://myaccount.google.com/permissions or expired
    after 6 months of inactivity. We promote that to ``GoogleAccountNotLinked``
    so the endpoint maps it to 412 and the FE re-prompts consent."""
    from app.services import google_oauth
    from app.core import config

    monkeypatch.setattr(config.settings, "google_client_id", "client-id-test")
    monkeypatch.setattr(
        config.settings, "google_client_secret", "client-secret-test"
    )

    respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            },
        )
    )

    with pytest.raises(GoogleAccountNotLinked):
        await _refresh_access_token(refresh_token="dead-refresh-token")
