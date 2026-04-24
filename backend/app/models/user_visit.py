from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserVisit(Base):
    """Per-user visit history for a broker-dealer detail page.

    One row per ``(user_id, bd_id)`` pair. ``record_visit`` upserts on that pair
    and bumps ``visit_count`` + ``last_visited_at`` so the Visited Firms list
    stays bounded in size while preserving first-visit telemetry.
    """

    __tablename__ = "user_visit"
    __table_args__ = (
        UniqueConstraint("user_id", "bd_id", name="uq_user_visit_user_bd"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bd_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("broker_dealers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    visit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    first_visited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_visited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
