"""Doxie's learned-term glossary.

When Doxie hits a term it doesn't know, the ``research_term`` tool looks it
up here first; on a miss it researches the public web and writes the result
back so the definition is "learned" for every future chat. The glossary is
**global** — one row per normalized term, shared across users —
``created_by_user_id`` keeps a light audit of who first triggered the
lookup. Definitions are stable enough that there's no TTL today; a future
admin "refresh" action can re-research and update a row in place.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatbotLearnedTerm(Base):
    __tablename__ = "chatbot_learned_term"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Display form of the term as the user phrased it (for showing back).
    term: Mapped[str] = mapped_column(String(255), nullable=False)
    # Lookup key — lowercased / trimmed / whitespace-collapsed. Unique so the
    # research tool can ``ON CONFLICT`` upsert without a select-then-insert
    # race between two chats researching the same term at once.
    term_normalized: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    # Where the definition came from: the top source URL and which search
    # provider returned it. Both nullable — a future hand-curated entry may
    # have neither.
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Who first triggered the lookup. SET NULL on user delete so the shared
    # glossary entry survives the user that happened to surface it.
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
