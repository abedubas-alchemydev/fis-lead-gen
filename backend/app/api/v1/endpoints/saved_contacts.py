"""Endpoints for the per-user "Save Contact" store.

A saved contact is a server-resolved snapshot of an individual contact the
user pinned from the email extractor (see ``services/saved_contacts.py`` and
``models/saved_contact.py`` for why it snapshots rather than joins live).

Contract:
* ``GET  /api/v1/saved-contacts?source=discovered_email`` -> ``SavedContact[]``
  (newest first).
* ``POST /api/v1/saved-contacts`` ``{source, contact_id}`` -> ``SavedContact``
  (idempotent: re-saving returns the existing row, 200, never errors).
* ``DELETE /api/v1/saved-contacts/{saved_id}`` -> 204 (scoped to the caller;
  404 if the row isn't theirs).

Every route is owner-scoped via ``current_user.id``; there is no cross-user
read or delete path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.saved_contact import SavedContactCreate, SavedContactResponse
from app.services.auth import get_current_user
from app.services.saved_contacts import (
    delete_saved_contact,
    list_saved_contacts,
    save_contact,
)

router = APIRouter(prefix="/saved-contacts", tags=["saved-contacts"])


@router.get("", response_model=list[SavedContactResponse])
async def get_saved_contacts(
    source: str | None = Query(default=None),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[SavedContactResponse]:
    """Return the calling user's saved contacts, newest first.

    Optional ``source`` filters to a single origin (e.g. ``discovered_email``).
    """
    rows = await list_saved_contacts(db, current_user.id, source)
    return [SavedContactResponse.model_validate(row) for row in rows]


@router.post("", response_model=SavedContactResponse)
async def create_saved_contact(
    payload: SavedContactCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SavedContactResponse:
    """Snapshot and save a contact for the calling user.

    Idempotent -- re-POSTing the same ``{source, contact_id}`` returns the
    existing row (200) instead of raising on the unique constraint. 404 if the
    referenced contact doesn't exist; 400 for an unsupported ``source``.
    """
    row = await save_contact(
        db, current_user.id, payload.source, payload.contact_id
    )
    return SavedContactResponse.model_validate(row)


@router.delete("/{saved_id}", status_code=204)
async def remove_saved_contact(
    saved_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Delete one of the caller's saved contacts. 404 if it isn't theirs."""
    await delete_saved_contact(db, current_user.id, saved_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
