from __future__ import annotations

import base64
import hashlib
import hmac
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
