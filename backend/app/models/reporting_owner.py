from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReportingOwner(Base):
    """One row per SEC Form 4 reporting person (insider), keyed by CIK.

    Promotes the reporting owner -- previously only denormalized columns
    on ``form4_transactions`` -- to a first-class entity so it can be
    favorited the same way broker-dealers, advisors, and institutional
    investors are (the 4th arm of the ``favorite_list_item`` XOR added in
    migration 0055). The insider's per-filing metadata (title, address,
    director/officer flags) still lives denormalized on
    ``form4_transactions``; this table holds only the stable identity
    (CIK + canonical name).

    ``name`` is the most-recent name seen for that CIK -- insiders'
    displayed names vary slightly across filings ("SMITH, JOHN" vs
    "Smith John A."). The migration backfill picks the latest by
    ``filed_at``; the favorite endpoint refreshes it on add.

    ``cik`` matches the ``form4_transactions.reporting_owner_cik`` width
    (``String(16)``) so the join in the investors list query is clean.
    The unique index over ``cik`` lives in migration 0055.
    """

    __tablename__ = "reporting_owners"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
