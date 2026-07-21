"""Service layer for the per-user "Save Contact" store.

A saved contact is a SNAPSHOT of an individual contact the user pinned from the
email extractor. Snapshotting (rather than joining live to the source row) is
the whole point: the source ``discovered_email`` row cascade-deletes with its
``extraction_run``, so the save must carry its own copy of the fields and
reference the source only by a bare ``(source, contact_id)`` pair -- no FK.

Writes go through ``INSERT ... ON CONFLICT DO NOTHING`` on the
``(user_id, source, contact_id)`` unique key so the UI can fire the same save
twice (double-click, retry) without a 500; the endpoint layer needs no
idempotency guard of its own. The service raises ``HTTPException`` directly
(404 for a missing/foreign row, 400 for an unsupported source, 409 for the
rare same-user concurrent-delete self-race) so every caller gets identical,
correctly-shaped errors.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import Row, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import AuthUser
from app.models.discovered_email import DiscoveredEmail
from app.models.saved_contact import SavedContact


# The only origin wired up so far. New sources slot in by adding a resolver
# branch in ``save_contact`` -- the store itself is source-agnostic.
SOURCE_DISCOVERED_EMAIL = "discovered_email"


async def save_contact(
    db: AsyncSession, user_id: str, source: str, contact_id: int
) -> SavedContact:
    """Snapshot a discovered contact into the user's saved-contacts store.

    Idempotent: re-saving the same ``(source, contact_id)`` returns the existing
    row unchanged (``ON CONFLICT DO NOTHING`` + a follow-up SELECT), so the FE
    can fire the same POST twice without tripping the unique constraint.

    Args:
        db: Async DB session.
        user_id: BetterAuth user id (the owner).
        source: Origin discriminator; only ``"discovered_email"`` is supported.
        contact_id: Primary key of the source row to snapshot.

    Returns:
        The persisted ``SavedContact`` row (existing or newly inserted).

    Raises:
        HTTPException: 400 if ``source`` is unsupported; 404 if the referenced
            source row does not exist; 409 if the row is concurrently deleted
            by the same user between the insert-commit and the canonical
            re-select (retryable self-race).
    """
    if source != SOURCE_DISCOVERED_EMAIL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported contact source",
        )

    discovered = (
        await db.execute(
            select(DiscoveredEmail).where(DiscoveredEmail.id == contact_id)
        )
    ).scalar_one_or_none()
    if discovered is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact not found",
        )

    # Snapshot the source fields onto the row so the save survives the
    # discovered_email's cascade-delete.
    insert_stmt = (
        pg_insert(SavedContact)
        .values(
            user_id=user_id,
            source=source,
            contact_id=contact_id,
            name=discovered.enriched_name,
            title=discovered.enriched_title,
            email=discovered.email,
            company=discovered.enriched_company,
            phone=discovered.enriched_phone,
            linkedin_url=discovered.enriched_linkedin_url,
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "source", "contact_id"]
        )
    )
    await db.execute(insert_stmt)
    await db.commit()

    # Re-select the canonical row by the unique key -- returns the freshly
    # inserted row or the pre-existing one on a conflict no-op, identically.
    row = (
        await db.execute(
            select(SavedContact).where(
                SavedContact.user_id == user_id,
                SavedContact.source == source,
                SavedContact.contact_id == contact_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # Self-race: the same user concurrently DELETEd this contact between
        # our insert-commit above and this re-select, so the canonical row is
        # already gone. Surface a retryable client error rather than letting
        # the missing row bubble up as an unhandled 500.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="save race, please retry",
        )
    return row


async def list_saved_contacts(
    db: AsyncSession, user_id: str, source: str | None = None
) -> Sequence[SavedContact]:
    """Return the user's saved contacts, newest first.

    Ordered ``created_at DESC`` with an ``id DESC`` tiebreak so saves made in
    the same instant still have a stable order.

    Args:
        db: Async DB session.
        user_id: BetterAuth user id (the owner).
        source: Optional origin filter (e.g. ``"discovered_email"``).

    Returns:
        The owner's ``SavedContact`` rows in newest-first order.
    """
    stmt = select(SavedContact).where(SavedContact.user_id == user_id)
    if source is not None:
        stmt = stmt.where(SavedContact.source == source)
    stmt = stmt.order_by(SavedContact.created_at.desc(), SavedContact.id.desc())
    return list((await db.execute(stmt)).scalars().all())


async def list_all_saved_contacts(
    db: AsyncSession, source: str | None = None, limit: int = 1000
) -> Sequence[Row[tuple[SavedContact, str, str | None, str | None]]]:
    """Return EVERY user's saved contacts joined to their owner, newest first.

    The cross-user counterpart to :func:`list_saved_contacts`: it is NOT scoped
    to a single ``user_id`` and inner-joins ``user`` so each row carries the
    owner's id / name / email. Reserved for the service-to-service CRM
    integration endpoint, which gates the read on a shared key rather than a
    per-user session -- never expose this to an end-user surface.

    Ordered ``created_at DESC`` with an ``id DESC`` tiebreak, matching
    :func:`list_saved_contacts` so both surfaces agree on ordering.

    Args:
        db: Async DB session.
        source: Optional origin filter (e.g. ``"discovered_email"``).
        limit: Max rows to return. Hard-clamped to ``1..1000`` so an unbounded
            (or hostile) value can never pull the entire cross-user table; the
            cap applies AFTER ordering, so callers get the newest ``limit`` rows.

    Returns:
        Rows of ``(SavedContact, owner_id, owner_name, owner_email)`` across all
        users, newest-first. The inner join drops any orphaned save whose owner
        row is gone (the ``user`` FK makes that unreachable in practice).
    """
    limit = min(max(limit, 1), 1000)
    stmt = (
        select(
            SavedContact,
            AuthUser.id.label("owner_id"),
            AuthUser.name.label("owner_name"),
            AuthUser.email.label("owner_email"),
        )
        .join(AuthUser, AuthUser.id == SavedContact.user_id)
    )
    if source is not None:
        stmt = stmt.where(SavedContact.source == source)
    stmt = (
        stmt.order_by(SavedContact.created_at.desc(), SavedContact.id.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).all())


async def delete_saved_contact(
    db: AsyncSession, user_id: str, saved_id: int
) -> None:
    """Delete one of the user's saved contacts.

    Scoped to ``user_id`` so a leaked ``saved_id`` can't delete another user's
    row -- a save that isn't the caller's reads as "not found".

    Args:
        db: Async DB session.
        user_id: BetterAuth user id (the owner).
        saved_id: ``saved_contact`` primary key to delete.

    Raises:
        HTTPException: 404 if no row with that id belongs to the user.
    """
    result = await db.execute(
        delete(SavedContact).where(
            SavedContact.id == saved_id,
            SavedContact.user_id == user_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved contact not found",
        )
    await db.commit()
