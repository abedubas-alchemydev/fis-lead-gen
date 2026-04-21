from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IntroducingArrangement(Base):
    __tablename__ = "introducing_arrangements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bd_id: Mapped[int] = mapped_column(ForeignKey("broker_dealers.id", ondelete="CASCADE"), index=True, nullable=False)
    statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
