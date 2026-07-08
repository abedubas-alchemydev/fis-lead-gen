"""Public API for DOX Share — the unauthenticated ``/share/{token}`` surface.

No cookie-auth, no role: access is gated entirely by the capability token in
the URL plus the per-share password. The flow is:

1. ``GET /{token}`` — resolve the share, report only its display name and
   whether the caller already holds a valid unlock cookie (the item count is
   withheld until unlocked, so the list size can't be probed pre-password).
2. ``POST /{token}/unlock`` — brute-force-throttled password check; on success
   mint a signed, HttpOnly unlock cookie scoped to this share.
3. ``GET /{token}/items`` — the unlocked list.
4. ``GET /{token}/items/{item_id}/profile`` — one allowlisted public profile.

Two deliberate anti-enumeration properties:

* An unknown token and a revoked share return a **byte-identical** 404, so a
  probe can't tell "never existed" from "was revoked".
* ``get_item_for_share`` scopes an item id to its share, so a foreign item id
  (IDOR attempt) 404s exactly like a non-existent one.

Every write path (failed/successful unlock, view touches) is deliberate and
throttled; the scrypt verify runs off the event loop via ``to_thread``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.schemas.lead_share import (
    PublicProfileResponse,
    PublicShareListResponse,
    PublicShareMeta,
)
from app.services import lead_shares as svc
from app.services.audit import record_audit
from app.services.lead_share_profiles import build_public_profile

router = APIRouter(prefix="/public/shares", tags=["lead-shares-public"])


class ShareUnlockRequest(BaseModel):
    # Bounded so an oversized body can't reach scrypt; the throttle governs
    # attempt volume, this governs per-attempt input size.
    password: str = Field(..., max_length=128)


@router.get("/{token}", response_model=PublicShareMeta)
async def get_share_meta(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> PublicShareMeta:
    """Landing-page metadata: the share name and whether this caller is
    already unlocked. ``item_count`` is populated only once unlocked."""
    now = datetime.now(timezone.utc)
    share = await svc.get_active_share_by_token(db, token)
    if share is None:
        raise HTTPException(status_code=404, detail="Not found.")

    unlocked = svc.verify_share_cookie(
        share, request.cookies.get(svc.share_cookie_name(share.id)), now=now
    )
    item_count = len(await svc.list_share_items(db, share)) if unlocked else None
    return PublicShareMeta(
        name=share.name, unlocked=unlocked, item_count=item_count
    )


@router.post("/{token}/unlock", response_model=PublicShareMeta)
async def unlock_share(
    token: str,
    body: ShareUnlockRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> PublicShareMeta:
    """Verify the share password. On success mint the unlock cookie and return
    the unlocked metadata; on failure register the attempt and 403. A tripped
    brute-force window 429s *before* any scrypt work is done."""
    now = datetime.now(timezone.utc)
    share = await svc.get_active_share_by_token(db, token)
    if share is None:
        raise HTTPException(status_code=404, detail="Not found.")

    retry = svc.unlock_retry_after(share, now=now)
    if retry > 0:
        raise HTTPException(
            status_code=429,
            detail="too_many_attempts",
            headers={"Retry-After": str(retry)},
        )

    ok = await asyncio.to_thread(
        svc.verify_share_password, body.password, share.password_hash
    )
    if not ok:
        await svc.register_failed_unlock(db, share, now=now)
        await record_audit(
            db,
            action="lead_share_unlock_failed",
            user_id=None,
            details={"share_id": share.id},
            commit=True,
        )
        raise HTTPException(status_code=403, detail="invalid_password")

    await svc.record_successful_unlock(db, share, now=now)
    await record_audit(
        db,
        action="lead_share_unlocked",
        user_id=None,
        details={"share_id": share.id},
        commit=True,
    )

    name, value, max_age = svc.mint_share_cookie(share, now=now)
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=(settings.environment != "development"),
        path="/",
    )
    return PublicShareMeta(
        name=share.name,
        unlocked=True,
        item_count=len(await svc.list_share_items(db, share)),
    )


@router.get("/{token}/items", response_model=PublicShareListResponse)
async def list_public_share_items(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> PublicShareListResponse:
    """The unlocked list of shared leads. Requires a valid unlock cookie."""
    now = datetime.now(timezone.utc)
    share = await svc.get_active_share_by_token(db, token)
    if share is None:
        raise HTTPException(status_code=404, detail="Not found.")

    if not svc.verify_share_cookie(
        share, request.cookies.get(svc.share_cookie_name(share.id)), now=now
    ):
        raise HTTPException(status_code=401, detail="share_locked")

    await svc.touch_last_viewed(db, share, now=now)
    return PublicShareListResponse(
        name=share.name, items=await svc.list_share_items(db, share)
    )


@router.get(
    "/{token}/items/{item_id}/profile", response_model=PublicProfileResponse
)
async def get_public_share_item_profile(
    token: str,
    item_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> PublicProfileResponse:
    """One shared lead's full allowlisted public profile. Requires a valid
    unlock cookie; the item id is scoped to this share (IDOR guard)."""
    now = datetime.now(timezone.utc)
    share = await svc.get_active_share_by_token(db, token)
    if share is None:
        raise HTTPException(status_code=404, detail="Not found.")

    if not svc.verify_share_cookie(
        share, request.cookies.get(svc.share_cookie_name(share.id)), now=now
    ):
        raise HTTPException(status_code=401, detail="share_locked")

    item = await svc.get_item_for_share(db, share_id=share.id, item_id=item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Not found.")

    profile = await build_public_profile(db, item)
    if profile is None:
        raise HTTPException(status_code=404, detail="Not found.")

    await svc.touch_last_viewed(db, share, now=now)
    return profile
