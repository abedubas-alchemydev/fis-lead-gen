"""Admin API for DOX Share — create/list/inspect/rotate/revoke/delete share
links over curated favorite lists.

Admin-role only (``_ensure_admin``, mirroring ``users_admin.py``). Every
lifecycle action writes an ``audit_log`` row; the plaintext password is
returned exactly once on create/rotate and is never logged.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.lead_share import LeadShareItem
from app.schemas.auth import AuthenticatedUser
from app.schemas.lead_share import (
    RotatePasswordResponse,
    ShareCreateRequest,
    ShareCreateResponse,
    ShareDetailResponse,
    ShareListResponse,
    ShareSummary,
)
from app.services import lead_shares as svc
from app.services.audit import record_audit
from app.services.auth import get_current_user

router = APIRouter(prefix="/shares", tags=["lead-shares-admin"])


def _ensure_admin(current_user: AuthenticatedUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )


def _warnings(skipped: "svc.ShareSkippedItems") -> list[str]:
    out: list[str] = []
    if skipped.institutional_investors:
        out.append(
            f"{skipped.institutional_investors} institutional investor "
            "item(s) skipped — not supported in shares yet."
        )
    if skipped.reporting_owners:
        out.append(
            f"{skipped.reporting_owners} insider/reporting-owner item(s) "
            "skipped — not supported in shares yet."
        )
    return out


async def _summary_or_404(db: AsyncSession, share_id: int) -> ShareSummary:
    detail = await svc.get_share_detail(db, share_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="share_not_found")
    share, count, email, _rows = detail
    return svc.to_share_summary(share, item_count=count, created_by_email=email)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ShareCreateResponse)
async def create_share(
    payload: ShareCreateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ShareCreateResponse:
    _ensure_admin(current_user)
    try:
        share, password, skipped = await svc.create_share_from_favorite_list(
            db,
            favorite_list_id=payload.favorite_list_id,
            name=payload.name,
            created_by=current_user.id,
        )
    except svc.ShareCreateError as exc:
        if exc.code == "favorite_list_not_found":
            raise HTTPException(status_code=404, detail="favorite_list_not_found")
        if exc.code == "share_too_large":
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "share_too_large",
                    "eligible": exc.eligible,
                    "max": svc.MAX_SHARE_ITEMS,
                },
            )
        raise HTTPException(status_code=400, detail="share_source_empty")

    item_count = await db.scalar(
        select(func.count(LeadShareItem.id)).where(
            LeadShareItem.share_id == share.id
        )
    )
    summary = svc.to_share_summary(
        share, item_count=int(item_count or 0), created_by_email=current_user.email
    )
    await record_audit(
        db,
        action="lead_share_created",
        user_id=current_user.id,
        details={
            "share_id": share.id,
            "favorite_list_id": payload.favorite_list_id,
            "item_count": int(item_count or 0),
            "skipped_investors": skipped.institutional_investors,
            "skipped_reporting_owners": skipped.reporting_owners,
        },
    )
    await db.commit()
    return ShareCreateResponse(
        share=summary, password=password, skipped=skipped, warnings=_warnings(skipped)
    )


@router.get("", response_model=ShareListResponse)
async def list_shares(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ShareListResponse:
    _ensure_admin(current_user)
    rows = await svc.list_shares(db)
    return ShareListResponse(
        items=[
            svc.to_share_summary(share, item_count=count, created_by_email=email)
            for share, count, email in rows
        ]
    )


@router.get("/{share_id}", response_model=ShareDetailResponse)
async def get_share(
    share_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ShareDetailResponse:
    _ensure_admin(current_user)
    detail = await svc.get_share_detail(db, share_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="share_not_found")
    share, count, email, rows = detail
    return ShareDetailResponse(
        share=svc.to_share_summary(share, item_count=count, created_by_email=email),
        items=rows,
    )


@router.post("/{share_id}/rotate-password", response_model=RotatePasswordResponse)
async def rotate_password(
    share_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RotatePasswordResponse:
    _ensure_admin(current_user)
    share = await svc.get_share(db, share_id)
    if share is None:
        raise HTTPException(status_code=404, detail="share_not_found")
    if share.revoked_at is not None:
        raise HTTPException(status_code=409, detail="share_revoked")
    now = datetime.now(timezone.utc)
    password = await svc.rotate_password(db, share, now=now)
    await record_audit(
        db,
        action="lead_share_password_rotated",
        user_id=current_user.id,
        details={"share_id": share.id},
        commit=True,
    )
    return RotatePasswordResponse(
        share_id=share.id, password=password, password_rotated_at=now
    )


@router.post("/{share_id}/revoke", response_model=ShareSummary)
async def revoke_share(
    share_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ShareSummary:
    _ensure_admin(current_user)
    share = await svc.get_share(db, share_id)
    if share is None:
        raise HTTPException(status_code=404, detail="share_not_found")
    if share.revoked_at is not None:
        raise HTTPException(status_code=409, detail="share_already_revoked")
    await svc.revoke_share(db, share, now=datetime.now(timezone.utc))
    await record_audit(
        db,
        action="lead_share_revoked",
        user_id=current_user.id,
        details={"share_id": share.id},
        commit=True,
    )
    return await _summary_or_404(db, share_id)


@router.post("/{share_id}/unrevoke", response_model=ShareSummary)
async def unrevoke_share(
    share_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ShareSummary:
    _ensure_admin(current_user)
    share = await svc.get_share(db, share_id)
    if share is None:
        raise HTTPException(status_code=404, detail="share_not_found")
    if share.revoked_at is None:
        raise HTTPException(status_code=409, detail="share_not_revoked")
    await svc.unrevoke_share(db, share)
    await record_audit(
        db,
        action="lead_share_unrevoked",
        user_id=current_user.id,
        details={"share_id": share.id},
        commit=True,
    )
    return await _summary_or_404(db, share_id)


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_share(
    share_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    _ensure_admin(current_user)
    share = await svc.get_share(db, share_id)
    if share is None:
        raise HTTPException(status_code=404, detail="share_not_found")
    # Record the audit row (with the name) before the row + its items cascade.
    await record_audit(
        db,
        action="lead_share_deleted",
        user_id=current_user.id,
        details={"share_id": share.id, "name": share.name},
    )
    await svc.delete_share(db, share)
