from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScoringSetting(Base):
    __tablename__ = "scoring_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    settings_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True, default="default")
    net_capital_growth_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=35)
    clearing_arrangement_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    financial_health_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    registration_recency_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
