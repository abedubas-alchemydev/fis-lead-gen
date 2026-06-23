"""Management endpoints for Doxie's private per-user memory.

``GET /doxie/memories`` lists the calling user's remembered facts and
``DELETE /doxie/memories/{id}`` removes one. Both are user-scoped via
``get_current_user`` — no admin gate (every authenticated user manages
their own memory) — and the ``user_id`` filter lives in the service-layer
SQL ``WHERE``, never in the request body. A delete that matches no row the
caller owns returns **404, not 403**, so a memory id can't be probed for
existence across users.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.chatbot import DoxieMemoryListResponse
from app.services.auth import get_current_user
from app.services.chatbot_user_memory import (
    count_memories,
    delete_memory,
    list_memories,
)

router = APIRouter(prefix="/doxie")


@router.get("/memories", response_model=DoxieMemoryListResponse)
async def get_doxie_memories(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> DoxieMemoryListResponse:
    """List the caller's own remembered facts, newest first."""
    items = await list_memories(
        db, current_user.id, limit=limit, offset=offset
    )
    total = await count_memories(db, current_user.id)
    return DoxieMemoryListResponse(items=items, total=total)


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_doxie_memory(
    memory_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Delete one of the caller's memories.

    Ownership is enforced in the service SQL. A non-existent id — or one that
    belongs to another user — matches nothing and yields 404 (never 403) so
    the endpoint can't confirm whether a memory id exists for someone else.
    """
    deleted = await delete_memory(
        db, user_id=current_user.id, memory_id=memory_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="doxie_memory_not_found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
