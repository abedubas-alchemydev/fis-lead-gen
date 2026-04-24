from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserFavorite(Base):
    """Per-user "hearted" broker-dealer.

    Unique on (user_id, bd_id) so ``POST /favorite`` can stay idempotent via
    ``INSERT ... ON CONFLICT DO NOTHING``. Both FKs cascade so deleting a user
    or a broker-dealer tears down their favorites automatically -- no orphans.
    """

    __tablename__ = "user_favorite"
    __table_args__ = (
        UniqueConstraint("user_id", "bd_id", name="uq_user_favorite_user_bd"),
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
