"""Doxie's private per-user memory.

When a user states a durable fact or preference ("I focus on self-clearing
firms in TX", "our fee is 25bps", "always draft formally"), Doxie can call
the ``remember_fact`` tool to write a row here. Every future chat loads the
caller's memories and folds them into the system prompt, so what a user
tells Doxie carries forward across conversations.

Strictly **per-user and private** — unlike ``chatbot_learned_term`` (a
global glossary), every row is scoped to one ``user_id`` and never shared
or surfaced cross-user. The ``user_id`` FK CASCADEs so a user's memories
are deleted with their account; ``source_message_id`` is a light back-link
to the chat turn that prompted the capture and SET NULLs if that message
is later pruned.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatbotUserMemory(Base):
    __tablename__ = "chatbot_user_memory"
    __table_args__ = (
        # Covers the per-user list query (newest-first) the management page
        # and the per-turn prompt injection both run.
        Index(
            "ix_chatbot_user_memory_user_id_created_at",
            "user_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Owner of the memory. CASCADE so a deleted user's memories go with them
    # — these can hold client/deal specifics, so they must not outlive the
    # account.
    user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The durable fact/preference in Doxie's words. Text (not String) so a
    # longer preference isn't truncated at the column level; the service
    # enforces the real length + per-user row caps.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional classifier the model may pass: "fact" | "preference". Free
    # short string — no native enum so future kinds don't need a migration.
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Light back-link to the chat turn that prompted the capture. SET NULL
    # so pruning an old message doesn't take the learned memory with it.
    source_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chatbot_message.id", ondelete="SET NULL"),
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
