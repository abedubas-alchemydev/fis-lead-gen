from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from urllib.parse import unquote

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import get_db_session
from app.models.auth import AuthSession
from app.schemas.auth import AuthenticatedUser

logger = logging.getLogger(__name__)


def _decode_signed_session_cookie(raw_cookie_value: str | None) -> str | None:
    if not raw_cookie_value:
        return None

    decoded_value = unquote(raw_cookie_value)
    token, separator, signature = decoded_value.rpartition(".")
    if not separator or not token or not signature:
        return raw_cookie_value

    expected_signature = base64.b64encode(
        hmac.new(settings.auth_secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

    if not hmac.compare_digest(signature, expected_signature):
        return None

    return token


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> AuthenticatedUser:
    session_token = _decode_signed_session_cookie(request.cookies.get(settings.auth_session_cookie_name))
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    stmt = (
        select(AuthSession)
        .options(selectinload(AuthSession.user))
        .where(AuthSession.token == session_token)
        .where(AuthSession.expires_at > datetime.now(timezone.utc))
    )

    try:
        result = await db.execute(stmt)
    except ProgrammingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication tables are unavailable. Run migrations before using auth-protected routes.",
        ) from exc

    auth_session = result.scalar_one_or_none()
    if auth_session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid or expired.")

    return AuthenticatedUser(
        id=auth_session.user.id,
        name=auth_session.user.name,
        email=auth_session.user.email,
        role=auth_session.user.role,
        session_expires_at=auth_session.expires_at,
    )


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> AuthenticatedUser | None:
    """Non-throwing variant of :func:`get_current_user`.

    Returns ``None`` when the request has no valid session cookie or the
    session lookup fails for any reason. Used by dependencies (such as
    :func:`_ensure_admin_or_scheduler_sa`) that need to fall through from the
    cookie path to a secondary auth path without short-circuiting on the
    absence of a session.
    """
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None


async def _ensure_admin_or_scheduler_sa(
    request: Request,
    current_user: AuthenticatedUser | None = Depends(get_current_user_optional),
) -> str:
    """Dual-path auth for Cloud Scheduler-triggered pipeline endpoints.

    Accepts EITHER:
      - an admin BetterAuth session cookie (used by the /settings/pipelines
        admin UI for manual triggers), OR
      - a verified Google-signed OIDC token from
        :attr:`Settings.cloud_scheduler_sa_email` whose ``aud`` claim matches
        :attr:`Settings.backend_audience` (the path Cloud Scheduler jobs use).

    Returns the caller identity string on success — either the admin's email
    or ``"sa:<service-account-email>"`` so handlers can record a
    distinguishable ``trigger_source``. Raises ``HTTPException(403)`` on any
    failure so cookie-only callers and SA-only callers see the same shape.
    """
    if current_user is not None and current_user.role == "admin":
        return current_user.email

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer ") :].strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin session cookie or scheduler SA OIDC token required.",
            )
        try:
            # Imported lazily so the rest of the auth module stays importable
            # in environments where google-auth isn't installed (e.g. lint /
            # type-check stages that never exercise the OIDC path).
            from google.auth.transport import requests as google_requests
            from google.oauth2 import id_token

            info = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                audience=settings.backend_audience,
            )
        except ValueError as exc:
            logger.warning("Rejected OIDC token on pipeline endpoint: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid OIDC token.",
            ) from exc

        token_email = info.get("email")
        if token_email != settings.cloud_scheduler_sa_email:
            logger.warning(
                "OIDC token email %r is not the configured scheduler SA.",
                token_email,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="OIDC token email is not the configured scheduler SA.",
            )
        return f"sa:{token_email}"

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin session cookie or scheduler SA OIDC token required.",
    )
