"""Persistence for Doxie's learned-term glossary (``chatbot_learned_term``).

Thin async helpers used by the ``research_term`` chatbot tool: normalize a
term to its lookup key, fetch a stored definition, and upsert a freshly
researched one. The upsert uses Postgres ``ON CONFLICT`` on the unique
``term_normalized`` index so two chats researching the same term at once
can't insert duplicate rows; the original ``created_by_user_id`` is
preserved on conflict while the definition is refreshed.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chatbot_learned_term import ChatbotLearnedTerm

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_term(raw: str) -> str:
    """Lowercase, trim, and collapse internal whitespace to a lookup key."""
    return _WHITESPACE_RE.sub(" ", (raw or "").strip().lower())


async def get_learned_term(
    db: AsyncSession, term_normalized: str
) -> ChatbotLearnedTerm | None:
    """Return the stored glossary row for a normalized term, or None."""
    if not term_normalized:
        return None
    result = await db.execute(
        select(ChatbotLearnedTerm).where(
            ChatbotLearnedTerm.term_normalized == term_normalized
        )
    )
    return result.scalar_one_or_none()


async def upsert_learned_term(
    db: AsyncSession,
    *,
    term: str,
    definition: str,
    source_url: str | None,
    source: str | None,
    user_id: str | None,
) -> None:
    """Insert a learned term, or refresh the definition if it already exists.

    Keyed on the unique ``term_normalized`` index. ``created_by_user_id`` is
    only set on the first insert — the original discoverer is kept on
    conflict. No-ops on an empty term or definition.
    """
    normalized = normalize_term(term)
    if not normalized or not definition:
        return
    stmt = pg_insert(ChatbotLearnedTerm).values(
        term=term.strip(),
        term_normalized=normalized,
        definition=definition,
        source_url=source_url,
        source=source,
        created_by_user_id=user_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["term_normalized"],
        set_={
            "definition": stmt.excluded.definition,
            "source_url": stmt.excluded.source_url,
            "source": stmt.excluded.source,
            "updated_at": func.now(),
        },
    )
    await db.execute(stmt)
    await db.commit()
