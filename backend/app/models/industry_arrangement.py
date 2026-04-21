from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IndustryArrangement(Base):
    """FINRA BrokerCheck 'Firm Operations → Industry Arrangements' row.

    Three kinds per firm, each a yes/no statement with an optional partner
    block. Together they answer whether a firm is truly self-clearing or relies
    on a third party for books/records, firm-level asset custody, or customer-
    level asset custody. Populated by the brokercheck_extractor bridge.
    """

    __tablename__ = "industry_arrangements"
    __table_args__ = (UniqueConstraint("bd_id", "kind", name="uq_industry_arrangement_bd_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bd_id: Mapped[int] = mapped_column(
        ForeignKey("broker_dealers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # books_records | accounts_funds | customer_accounts
    has_arrangement: Mapped[bool] = mapped_column(Boolean, nullable=False)
    partner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    partner_crd: Mapped[str | None] = mapped_column(String(64), nullable=True)
    partner_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
