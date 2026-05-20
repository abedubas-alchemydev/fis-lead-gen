from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserActivity(Base):
    """Broad-surface user instrumentation: nav, search, input.

    Separate from ``AuditLog`` (security/auth events) so the high-volume
    write path doesn't bloat the audit_log index. See migration
    ``20260520_0053`` for the rationale.

    The ingest path is the Next.js route ``/api/activity/events`` (writes
    directly to Postgres via the shared BetterAuth db connection — same
    pattern as ``/api/security/event``). The read path is the FastAPI
    admin endpoint ``GET /api/v1/users/{user_id}/activities``.
    """

    __tablename__ = "user_activity"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
