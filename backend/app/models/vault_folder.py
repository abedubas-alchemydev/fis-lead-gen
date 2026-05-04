"""Per-user "Vault" folder model.

Backs the Vault feature on ``frontend/app/(app)/vault`` and the service
dropdown in the Outreach modal at ``/master-list/{id}``. Each row is a
named service (e.g. "Custody", "Stock Loan", "Margin Financing") plus
a freeform description used as context when generating outreach drafts.

The MVP intentionally has no file-upload columns — the description text
is what the LLM sees. File uploads + RAG embeddings are a follow-up
schema change.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VaultFolder(Base):
    """A named service description owned by a single user."""

    __tablename__ = "vault_folder"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_vault_folder_user_name"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    # Permanent per-service prompt guidance — fed verbatim to Gemini on
    # every Outreach draft generated against this folder. Examples:
    # "Keep emails under 80 words", "Always mention 24h turnaround",
    # "Tone: formal, never casual". Added 2026-05-05 (Deshorn's ask
    # via Slack 2026-05-04). Per-draft override is a v2 concern.
    outreach_instructions: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
