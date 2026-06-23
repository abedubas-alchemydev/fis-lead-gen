"""Persistence for Doxie's private per-user memory (``chatbot_user_memory``).

Async helpers behind the ``remember_fact`` chatbot tool and the
``/doxie/memories`` management endpoints. Unlike the global learned-term
glossary, every row is scoped to one ``user_id`` and never shared
cross-user, so there is **no** global unique key on ``content`` and **no**
``ON CONFLICT`` upsert: de-dupe is a per-user ``SELECT`` on the normalized
content before insert. ``create_memory`` also enforces a hard content
length cap and a per-user row cap (trimming the oldest rows when full) so a
chatty model can't grow the store without bound.
"""

from __future__ import annotations

import re

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chatbot_user_memory import ChatbotUserMemory

_WHITESPACE_RE = re.compile(r"\s+")

# Hard server-side cap on a single memory's stored length. The model is told
# to keep facts short; this is the backstop against a runaway tool call. Kept
# well under the column's unbounded Text so the value never truncates mid-row.
MAX_MEMORY_CONTENT_CHARS = 2000
# Per-user row ceiling. v1 injects ALL of a user's memories (capped) into the
# prompt, so this also bounds the injection cost. When full, the oldest rows
# are trimmed to make room for the new one (FIFO) rather than rejecting it.
MAX_MEMORIES_PER_USER = 200


def normalize_content(raw: str) -> str:
    """Lowercase, trim, and collapse internal whitespace for de-dupe keying."""
    return _WHITESPACE_RE.sub(" ", (raw or "").strip().lower())


async def create_memory(
    db: AsyncSession,
    *,
    user_id: str,
    content: str,
    kind: str | None = None,
    source_message_id: int | None = None,
) -> ChatbotUserMemory | None:
    """Store one durable fact/preference for ``user_id``.

    Trims ``content`` to :data:`MAX_MEMORY_CONTENT_CHARS`, de-dupes against
    the caller's existing memories on normalized content (returning the
    existing row instead of inserting a near-identical one), and enforces
    :data:`MAX_MEMORIES_PER_USER` by FIFO-trimming the oldest rows when full.
    No-ops (returns ``None``) on empty content. Commits on success.
    """
    trimmed = (content or "").strip()[:MAX_MEMORY_CONTENT_CHARS]
    if not trimmed:
        return None
    normalized = normalize_content(trimmed)

    # De-dupe per user. No global unique index exists (memories are private),
    # so this SELECT — not an ON CONFLICT upsert — is the dedup guard.
    existing = await db.execute(
        select(ChatbotUserMemory).where(
            ChatbotUserMemory.user_id == user_id,
            func.lower(ChatbotUserMemory.content) == normalized,
        )
    )
    found = existing.scalars().first()
    if found is not None:
        return found

    # Enforce the per-user cap by trimming the oldest rows so this insert
    # keeps the count at the ceiling rather than exceeding it.
    count_result = await db.execute(
        select(func.count(ChatbotUserMemory.id)).where(
            ChatbotUserMemory.user_id == user_id
        )
    )
    current = count_result.scalar_one()
    if current >= MAX_MEMORIES_PER_USER:
        overflow = current - MAX_MEMORIES_PER_USER + 1
        oldest = await db.execute(
            select(ChatbotUserMemory.id)
            .where(ChatbotUserMemory.user_id == user_id)
            .order_by(
                ChatbotUserMemory.created_at.asc(),
                ChatbotUserMemory.id.asc(),
            )
            .limit(overflow)
        )
        stale_ids = [row[0] for row in oldest.all()]
        if stale_ids:
            await db.execute(
                delete(ChatbotUserMemory).where(
                    ChatbotUserMemory.id.in_(stale_ids)
                )
            )

    memory = ChatbotUserMemory(
        user_id=user_id,
        content=trimmed,
        kind=(kind[:32] or None) if kind else None,
        source_message_id=source_message_id,
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return memory


async def list_memories(
    db: AsyncSession,
    user_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[ChatbotUserMemory]:
    """Return the caller's memories, newest first. Scoped by ``user_id``."""
    result = await db.execute(
        select(ChatbotUserMemory)
        .where(ChatbotUserMemory.user_id == user_id)
        .order_by(
            ChatbotUserMemory.created_at.desc(),
            ChatbotUserMemory.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def count_memories(db: AsyncSession, user_id: str) -> int:
    """Total memory count for ``user_id``."""
    result = await db.execute(
        select(func.count(ChatbotUserMemory.id)).where(
            ChatbotUserMemory.user_id == user_id
        )
    )
    return int(result.scalar_one())


async def delete_memory(
    db: AsyncSession,
    *,
    user_id: str,
    memory_id: int,
) -> bool:
    """Delete one memory the caller owns.

    Ownership is enforced in the SQL ``WHERE`` (``user_id`` AND ``id``), so a
    row belonging to another user never matches and ``rowcount`` is 0 — the
    endpoint maps that to 404 (not 403) so a memory id can't be probed for
    existence. Returns ``True`` when a row was deleted.
    """
    result = await db.execute(
        delete(ChatbotUserMemory).where(
            ChatbotUserMemory.user_id == user_id,
            ChatbotUserMemory.id == memory_id,
        )
    )
    if result.rowcount and result.rowcount > 0:
        await db.commit()
        return True
    return False


async def list_memory_lines(
    db: AsyncSession,
    user_id: str,
    *,
    limit: int = 50,
) -> list[str]:
    """Return the caller's memory contents as plain strings for injection.

    Newest first, capped at ``limit``. The chatbot service folds these into
    the system prompt each turn; the read is best-effort (the caller wraps it
    in try/except) so a DB hiccup never sinks a chat.
    """
    result = await db.execute(
        select(ChatbotUserMemory.content)
        .where(ChatbotUserMemory.user_id == user_id)
        .order_by(
            ChatbotUserMemory.created_at.desc(),
            ChatbotUserMemory.id.desc(),
        )
        .limit(limit)
    )
    return [row[0] for row in result.all()]
