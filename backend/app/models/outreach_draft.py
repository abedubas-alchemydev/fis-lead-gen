"""A saved-but-unsent outreach email draft owned by one user.

Unlike ``outreach_sends`` (an immutable per-attempt audit record written
only on send), a draft is **mutable**: the Create-Outreach composer saves
the in-progress message here so it survives a reload, and Doxie-generated
emails land here for a human to review before sending. Drafts are private
to their owner and hard-deleted once sent or discarded — there's no audit
requirement to retain them (the send itself is audited in ``outreach_sends``).

The recipient columns mirror the ``POST /outreach/compose-send`` payload so
a draft round-trips losslessly back into the composer: ``to_recipients`` is
a JSON list of ``{"email": str, "name": str | None}`` and ``cc_emails`` /
``bcc_emails`` are JSON lists of plain address strings. Addresses are stored
loosely (not validated as deliverable) so a half-typed draft still saves;
validation happens on the send path, not here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutreachDraft(Base):
    __tablename__ = "outreach_drafts"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Subject/body are nullable so an in-progress draft with nothing typed
    # yet still saves. 998 mirrors the send-path subject cap (RFC 5322 line
    # limit); body matches the composer textarea ceiling.
    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Recipient buckets mirror the compose-send payload so the draft can be
    # loaded straight back into the To/Cc/Bcc composer. ``to_recipients`` is
    # a list of {"email", "name"}; cc/bcc are lists of address strings.
    to_recipients: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    cc_emails: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    bcc_emails: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Optional service folder for RAG context, same role as on a send.
    # ON DELETE SET NULL so deleting a vault folder doesn't drop the draft.
    folder_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("vault_folder.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Soft reference to ``account.id`` (no FK; mirrors outreach_sends). The
    # mailbox the user intends to send from when they pick this draft up.
    sender_account_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    # Where the draft came from: "manual" (composer Save Draft) or "doxie"
    # (AI-generated, pending human review). Kept a free string so adding a
    # new origin later is a no-migration change.
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'manual'"),
        default="manual",
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
