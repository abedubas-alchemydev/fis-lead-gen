"""Per-user Doxie chatbot conversations.

A user has at most one ACTIVE conversation at a time (``archived_at IS
NULL``). The "New chat" button on the FE archives the current one
(sets ``archived_at``) and creates a fresh active row. Archived rows
stay in the DB for potential audit / history-browser features later,
but the FE only ever loads the active conversation.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatbotConversation(Base):
    __tablename__ = "chatbot_conversation"
    __table_args__ = (
        # Hot path: load the user's active conversation. Partial-index style
        # with archived_at IS NULL is created in the alembic migration as a
        # raw SQL DDL since SQLAlchemy's Index() doesn't easily express
        # partial indexes; this regular index here is the fallback used in
        # the unit-test in-memory engine path.
        Index(
            "ix_chatbot_conversation_user_id_archived_at",
            "user_id",
            "archived_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
