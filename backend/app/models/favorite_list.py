"""Custom favorites lists (#17 phase 1) — playlist-style user-owned lists.

Two tables:

* ``favorite_list`` — one row per (user, list-name). ``is_default`` flags the
  per-user default "Favorites" list created by the backfill so phase 2 can
  protect it from rename/delete.
* ``favorite_list_item`` — items in a list, M:N between ``favorite_list`` and
  ``broker_dealers``.

Why ``BigInteger`` primary keys (and not UUIDs as the design brief sketched):
the rest of the schema (including ``user.id``, which is ``String(255)`` from
BetterAuth) does not use UUIDs anywhere. Adding UUIDs here alone would mean
enabling ``uuid-ossp`` in this migration and introducing a hybrid PK style
across the codebase. Sticking with ``BigInteger`` keeps the schema uniform.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FavoriteList(Base):
    """A user-owned named list of broker-dealers (playlist-style)."""

    __tablename__ = "favorite_list"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_favorite_list_user_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
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


class FavoriteListItem(Base):
    """A broker-dealer pinned to a favorite_list."""

    __tablename__ = "favorite_list_item"
    __table_args__ = (
        UniqueConstraint(
            "list_id", "broker_dealer_id", name="uq_favorite_list_item_list_bd"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    list_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("favorite_list.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    broker_dealer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("broker_dealers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
