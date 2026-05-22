"""Microsoft OAuth refresh for the Graph ``users.sendMail`` path.

Better Auth's Microsoft social provider stores per-user credentials on
the same ``account`` table Google uses (``provider_id='microsoft'``,
``access_token``, ``refresh_token``, ``scope``, ``access_token_expires_at``).
This module reads those rows and refreshes the access token against
Microsoft's identity platform token endpoint when it is expired or near
expiry, so the Graph send endpoint always receives a live bearer token.

Tenant ``common`` is used so the same flow accepts work / school
accounts (Azure AD organizational tenants) AND consumer Microsoft
accounts (outlook.com, hotmail.com, live.com). Locking to a specific
tenant would block one or the other and Deshorn's user base spans both.
"""

from __future__ import annotations

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


# Per https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow#refresh-the-access-token
# Multi-tenant + personal endpoint; matches Better Auth's default
# ``microsoft`` provider config which posts to /common as well.
_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
_EXPIRY_SKEW = timedelta(seconds=60)
_REFRESH_TIMEOUT = 15.0


async def get_fresh_microsoft_access_token(
    db: AsyncSession, account_id: str
) -> tuple[str, list[str]]:
    """Return ``(access_token, scopes)`` for the given Microsoft account row.

    Keyed by ``account.id`` (Better Auth's PK). The caller verifies row
    ownership before passing the id in. Same refresh + persistence
    semantics as :func:`get_fresh_google_access_token`.

    Raises :class:`EmailAccountNotLinked` when the account row is gone,
    not a Microsoft row, has no refresh token, or Microsoft returns
    ``invalid_grant`` (revoked / expired). Raises
    :class:`EmailProviderConfigurationError` when the env config is
    missing.
    """

    stmt = select(Account).where(
        Account.id == account_id, Account.provider_id == "microsoft"
    )
    account = (await db.execute(stmt)).scalar_one_or_none()
    if account is None:
        raise EmailAccountNotLinked(
            f"No Microsoft account row found for id {account_id}."
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
            f"Microsoft account {account_id} has no refresh token."
        )

    new_access_token, new_expires_at = await _refresh_access_token(
        refresh_token=account.refresh_token
    )
    account.access_token = new_access_token
    account.access_token_expires_at = new_expires_at
    return new_access_token, scopes


def _parse_scopes(raw: str | None) -> list[str]:
    """Parse ``account.scope`` into a list of scope URIs.

    Same liberal-in-what-we-accept split as the Google helper: Better
    Auth 1.3.6 stores scopes comma-separated, but the wire format is
    space-separated, and Microsoft sometimes returns scopes in either.
    """

    if not raw:
        return []
    return [scope.strip() for scope in raw.replace(",", " ").split() if scope.strip()]


async def _refresh_access_token(
    *, refresh_token: str
) -> tuple[str, datetime]:
    """Exchange the refresh token for a new access token + expiry.

    Microsoft returns 400 + ``{"error": "invalid_grant"}`` for revoked /
    expired refresh tokens — promote to ``EmailAccountNotLinked`` so the
    endpoint surfaces 412 and the FE re-prompts consent (same as Google).
    """

    if (
        not settings.microsoft_client_id
        or not settings.microsoft_client_secret
    ):
        raise EmailProviderConfigurationError(
            "MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET are not configured."
        )

    data = {
        "client_id": settings.microsoft_client_id,
        "client_secret": settings.microsoft_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        # No "scope" param: when omitted, MS returns a token covering
        # the originally-granted scope set. Specifying it can narrow
        # the token unexpectedly.
    }
    async with httpx.AsyncClient(timeout=_REFRESH_TIMEOUT) as client:
        response = await client.post(_TOKEN_URL, data=data)

    if response.status_code == 400:
        body = _safe_json(response)
        if body.get("error") == "invalid_grant":
            raise EmailAccountNotLinked(
                "Microsoft refresh token was revoked or expired."
            )

    if response.status_code >= 400:
        logger.warning(
            "microsoft token refresh failed: status=%s body=%s",
            response.status_code,
            response.text[:500],
        )
        response.raise_for_status()

    body = response.json()
    access_token = body.get("access_token")
    expires_in = body.get("expires_in")
    if not isinstance(access_token, str) or not isinstance(expires_in, int):
        raise RuntimeError(
            "Microsoft token response missing access_token / expires_in."
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return access_token, expires_at


def _safe_json(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
