from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.discovered_email import DiscoveredEmail


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class ExtractionRun(Base):
    """One row per email-extraction scan.

    Mirrors fis-lead-gen's `PipelineRun` shape so that on merge this becomes
    a row in `pipeline_runs` with `pipeline_name="email_extractor"` and a one-line
    rename. Status is stored as a `String(32)` (not a Postgres enum) to match the
    parent's pattern — keeps migrations simple and avoids type-rename pain.
    """

    __tablename__ = "extraction_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pipeline_name: Mapped[str] = mapped_column(String(120), default="email_extractor", nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    person_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.queued.value, nullable=False, index=True)
    total_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    discovered_emails: Mapped[list[DiscoveredEmail]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
