from __future__ import annotations

from datetime import datetime

from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExecutiveContact(Base):
    __tablename__ = "executive_contacts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bd_id: Mapped[int] = mapped_column(ForeignKey("broker_dealers.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="provider")
    discovery_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discovery_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    enriched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
