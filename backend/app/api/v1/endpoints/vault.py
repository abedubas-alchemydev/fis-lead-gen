"""Endpoints for the per-user "Vault" feature.

Manages user-owned ``vault_folder`` rows: each row is a named service
(e.g. "Custody", "Stock Loan") with a freeform description used by the
Outreach modal on ``/master-list/{id}`` to generate cold-email drafts.

The MVP is description-only — file uploads + RAG embeddings are a
follow-up. Ownership leaks are masked with 404 (same as
``favorite_lists.py``); duplicate folder names per user surface as 400.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.vault_folder import VaultFolder
from app.schemas.auth import AuthenticatedUser
from app.schemas.vault import (
    VaultFolderCreate,
    VaultFolderResponse,
    VaultFolderUpdate,
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/vault/folders")

_DUPLICATE_NAME_DETAIL = "A folder with that name already exists"
_NOT_FOUND_DETAIL = "vault_folder_not_found"


async def _get_owned_folder(
    db: AsyncSession, folder_id: int, user_id: str
) -> VaultFolder:
    """Return the folder iff it belongs to ``user_id``; 404 otherwise.

    404 (not 403) so a leaked folder_id can't be used to enumerate other
    users' folders.
    """
    result = await db.execute(
        select(VaultFolder).where(
            VaultFolder.id == folder_id,
            VaultFolder.user_id == user_id,
        )
    )
    folder = result.scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    return folder


@router.get("", response_model=list[VaultFolderResponse])
async def list_vault_folders(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[VaultFolder]:
    """Return the calling user's folders, newest-edited first."""
    stmt = (
        select(VaultFolder)
        .where(VaultFolder.user_id == current_user.id)
        .order_by(VaultFolder.updated_at.desc(), VaultFolder.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post("", response_model=VaultFolderResponse, status_code=201)
async def create_vault_folder(
    payload: VaultFolderCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> VaultFolder:
    """Create a new folder owned by the calling user.

    Returns 400 on duplicate ``name`` (per ``uq_vault_folder_user_name``).
    """
    folder = VaultFolder(
        user_id=current_user.id,
        name=payload.name,
        description=payload.description,
    )
    db.add(folder)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_DUPLICATE_NAME_DETAIL)
    await db.refresh(folder)
    return folder


@router.put("/{folder_id}", response_model=VaultFolderResponse)
async def update_vault_folder(
    payload: VaultFolderUpdate,
    folder_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> VaultFolder:
    """Partially update a folder owned by the calling user.

    Only fields explicitly set in the payload are touched, so ``name`` and
    ``description`` can be edited independently.
    """
    folder = await _get_owned_folder(db, folder_id, current_user.id)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return folder

    for field, value in updates.items():
        setattr(folder, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_DUPLICATE_NAME_DETAIL)
    await db.refresh(folder)
    return folder


@router.delete("/{folder_id}", status_code=204)
async def delete_vault_folder(
    folder_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Delete a folder owned by the calling user."""
    folder = await _get_owned_folder(db, folder_id, current_user.id)
    await db.delete(folder)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
