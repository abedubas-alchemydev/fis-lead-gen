"""Google OAuth helpers for the Gmail-send path.

Better Auth stores per-user Google credentials on the ``account`` table
(provider_id, access_token, refresh_token, scope, access_token_expires_at)
when a user clicks "Continue with Google". This module reads those rows
and refreshes the access token against Google's token endpoint when it
is expired or about to expire, so the Gmail send endpoint can always
hand a live bearer token to the Gmail API.

Scope-checking happens at the caller (the send endpoint) because raising
a typed exception per missing scope blurs the line with "no Google
account at all" — both paths route to the FE-side ``linkSocial`` re-
consent flow, but the FE needs to know which kind of 412 it got.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.auth import Account

logger = logging.getLogger(__name__)

# Token endpoint per https://developers.google.com/identity/protocols/oauth2/web-server#offline
_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Refresh slightly before expiry so a request that arrives 30s before
# the access token would otherwise expire still gets a fresh one.
_EXPIRY_SKEW = timedelta(seconds=60)
_REFRESH_TIMEOUT = 15.0


class GoogleAccountNotLinked(RuntimeError):
    """Caller has no Google ``account`` row, or its refresh token is dead.

    Maps to ``412 google_account_not_linked`` in the endpoint, which the
    frontend treats as "prompt the user to (re)link via ``linkSocial``".
    """


class GoogleOAuthConfigurationError(RuntimeError):
    """``google_client_id`` / ``google_client_secret`` are missing from env."""


async def get_fresh_google_access_token(
    db: AsyncSession, user_id: str
) -> tuple[str, list[str]]:
    """Return ``(access_token, scopes)`` for the user's Google account.

    Looks up the ``account`` row keyed by ``(user_id, provider_id='google')``.
    Refreshes via the Google token endpoint when ``access_token_expires_at``
    is null, in the past, or within ``_EXPIRY_SKEW`` of now. Persists the
    new token + expiry back to the row.

    Raises ``GoogleAccountNotLinked`` when there is no Google account on
    file, or when the refresh attempt returns ``invalid_grant`` (Google's
    canonical "the refresh token was revoked / expired" response).
    """
    stmt = select(Account).where(
        Account.user_id == user_id, Account.provider_id == "google"
    )
    account = (await db.execute(stmt)).scalar_one_or_none()
    if account is None:
        raise GoogleAccountNotLinked(
            f"No Google account linked for user {user_id}."
        )

    scopes = _parse_scopes(account.scope)
    now = datetime.now(timezone.utc)
    needs_refresh = (
        account.access_token is None
        or account.access_token_expires_at is None
        or account.access_token_expires_at - _EXPIRY_SKEW <= now
    )

    if not needs_refresh and account.access_token is not None:
        return account.access_token, scopes

    if not account.refresh_token:
        # No way to mint a new access token without a refresh token. This
        # happens when Google omitted ``refresh_token`` on the consent
        # response (it does that on silent re-auth without
        # prompt=consent). Force a re-link.
        raise GoogleAccountNotLinked(
            f"User {user_id} has a Google account but no refresh token."
        )

    new_access_token, new_expires_at = await _refresh_access_token(
        refresh_token=account.refresh_token
    )
    account.access_token = new_access_token
    account.access_token_expires_at = new_expires_at
    # SQLAlchemy auto-flush will pick this up; do not commit here so the
    # caller controls transaction boundaries (the send endpoint commits
    # after writing the outreach_sends audit row).
    return new_access_token, scopes


def _parse_scopes(raw: str | None) -> list[str]:
    """Better Auth stores OAuth scopes as a space-separated string.

    Returns ``[]`` for null / empty inputs so callers can use ``in`` checks
    without worrying about ``None`` casting.
    """
    if not raw:
        return []
    return [scope for scope in raw.split() if scope]


async def _refresh_access_token(
    *, refresh_token: str
) -> tuple[str, datetime]:
    """Exchange the refresh token for a new access token + expiry.

    Returns the new bearer token and an absolute UTC expiry derived from
    Google's relative ``expires_in`` seconds. Raises
    ``GoogleAccountNotLinked`` on ``invalid_grant`` (revoked) and
    ``GoogleOAuthConfigurationError`` when client_id / secret are unset.
    """
    if not settings.google_client_id or not settings.google_client_secret:
        raise GoogleOAuthConfigurationError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not configured."
        )

    data = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=_REFRESH_TIMEOUT) as client:
        response = await client.post(_TOKEN_URL, data=data)

    if response.status_code == 400:
        # Google uses 400 + ``{"error": "invalid_grant"}`` for revoked /
        # expired refresh tokens — promote to a domain-level "not linked"
        # so the endpoint surfaces 412 and the FE re-prompts consent.
        body = _safe_json(response)
        if body.get("error") == "invalid_grant":
            raise GoogleAccountNotLinked(
                "Google refresh token was revoked or expired."
            )

    if response.status_code >= 400:
        logger.warning(
            "google token refresh failed: status=%s body=%s",
            response.status_code,
            response.text[:500],
        )
        response.raise_for_status()

    body = response.json()
    access_token = body.get("access_token")
    expires_in = body.get("expires_in")
    if not isinstance(access_token, str) or not isinstance(expires_in, int):
        raise RuntimeError(
            "Google token response missing access_token / expires_in."
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return access_token, expires_at


def _safe_json(response: httpx.Response) -> dict[str, object]:
    """``response.json()`` that never raises — used to parse error bodies."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
