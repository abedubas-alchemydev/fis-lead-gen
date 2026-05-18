"""Yahoo OAuth refresh for the Yahoo SMTP XOAUTH2 send path.

Better Auth stores Yahoo accounts via the ``genericOAuth`` plugin in
the same ``account`` table (``provider_id='yahoo'``,
``access_token``, ``refresh_token``, ``scope``,
``access_token_expires_at``). Yahoo follows OpenID Connect Discovery
so the token endpoint URL is the same one published in their
``.well-known/openid-configuration`` -- pinned here so the refresh
path doesn't make a discovery HTTP call on every send.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.auth import Account
from app.services.email_providers.base import (
    EmailAccountNotLinked,
    EmailProviderConfigurationError,
)


logger = logging.getLogger(__name__)


# Per https://developer.yahoo.com/oauth2/guide/flows_authcode/
_TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
_EXPIRY_SKEW = timedelta(seconds=60)
_REFRESH_TIMEOUT = 15.0


async def get_fresh_yahoo_access_token(
    db: AsyncSession, user_id: str
) -> tuple[str, list[str]]:
    """Return ``(access_token, scopes)`` for the user's Yahoo account.

    Same contract as the Google + Microsoft helpers. Raises
    :class:`EmailAccountNotLinked` on missing row or revoked refresh
    token; :class:`EmailProviderConfigurationError` on missing env.
    """

    stmt = select(Account).where(
        Account.user_id == user_id, Account.provider_id == "yahoo"
    )
    account = (await db.execute(stmt)).scalar_one_or_none()
    if account is None:
        raise EmailAccountNotLinked(
            f"No Yahoo account linked for user {user_id}."
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
        raise EmailAccountNotLinked(
            f"User {user_id} has a Yahoo account but no refresh token."
        )

    new_access_token, new_expires_at = await _refresh_access_token(
        refresh_token=account.refresh_token
    )
    account.access_token = new_access_token
    account.access_token_expires_at = new_expires_at
    return new_access_token, scopes


def _parse_scopes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [scope.strip() for scope in raw.replace(",", " ").split() if scope.strip()]


async def _refresh_access_token(
    *, refresh_token: str
) -> tuple[str, datetime]:
    """Exchange the refresh token at Yahoo's token endpoint.

    Yahoo expects Basic-Auth client credentials in the Authorization
    header (not body params, unlike Google + Microsoft). The body
    carries the grant_type + refresh_token only.
    """

    if not settings.yahoo_client_id or not settings.yahoo_client_secret:
        raise EmailProviderConfigurationError(
            "YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET are not configured."
        )

    basic = base64.b64encode(
        f"{settings.yahoo_client_id}:{settings.yahoo_client_secret}".encode("utf-8")
    ).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "redirect_uri": "oob",  # Yahoo requires the field; ignored on refresh
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=_REFRESH_TIMEOUT) as client:
        response = await client.post(_TOKEN_URL, headers=headers, data=data)

    if response.status_code == 400:
        body = _safe_json(response)
        if body.get("error") == "invalid_grant":
            raise EmailAccountNotLinked(
                "Yahoo refresh token was revoked or expired."
            )

    if response.status_code >= 400:
        logger.warning(
            "yahoo token refresh failed: status=%s body=%s",
            response.status_code,
            response.text[:500],
        )
        response.raise_for_status()

    body = response.json()
    access_token = body.get("access_token")
    expires_in = body.get("expires_in")
    if not isinstance(access_token, str) or not isinstance(expires_in, int):
        raise RuntimeError(
            "Yahoo token response missing access_token / expires_in."
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return access_token, expires_at


def _safe_json(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
