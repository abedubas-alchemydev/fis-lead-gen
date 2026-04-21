from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.extraction_run import RunStatus


class VerificationRun(Base):
    """One row per SMTP verification batch.

    Mirrors `ExtractionRun`'s status/counter shape so the frontend polling
    loop can reuse the same state machine. `email_ids` is the input record —
    actual results are read by joining `EmailVerification` on
    `discovered_email_id IN (email_ids)` and taking the latest per id.

    No FK to DiscoveredEmail: the JSON list is the canonical input snapshot.
    Status is `String(32)` (not a Postgres enum) to match `ExtractionRun`.
    """

    __tablename__ = "verification_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.queued.value, nullable=False, index=True)
    total_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
