"""One persisted Doxie chat turn (user or assistant).

Ordered by ``created_at, id`` so two turns in the same wall-clock
microsecond replay deterministically. Page-context path + title are
captured per user message so an audit replay can reconstruct what the
user was looking at when they asked.

Assistant turns additionally carry the Gemini ``usageMetadata`` token
counts plus the turn's tool-call count and wall-clock latency (all
nullable — NULL means "not captured", which covers every user turn,
every pre-migration row, and any assistant turn where Gemini omitted
``usageMetadata``). These power the admin Doxie Usage / cost dashboard.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatbotMessage(Base):
    __tablename__ = "chatbot_message"
    __table_args__ = (
        Index(
            "ix_chatbot_message_conversation_id_created_at",
            "conversation_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chatbot_conversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    # "user" | "assistant" — Pydantic schema enforces enum at the API layer,
    # column is a plain short string to keep migrations cheap (no native
    # enum that future roles would need migration steps to extend).
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # Text (not String) because Gemini replies can be long-form. No length
    # cap at the column level; the schema-side per-message cap (8000 chars)
    # is the rate-limiting guard for inbound user messages.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Captured per user-message so an audit replay can reconstruct what the
    # user was viewing when they asked. Optional and nullable for assistant
    # turns (which don't have a page context of their own).
    page_context_path: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    page_context_title: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    # Gemini usage + turn telemetry, set on assistant turns only. All
    # nullable: NULL = "not captured" (user turns, legacy rows, or a
    # streamed turn where Gemini omitted usageMetadata on the final
    # chunk), distinct from a real 0. Aggregated by the admin Doxie Usage
    # dashboard via COALESCE(col, 0).
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_call_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
