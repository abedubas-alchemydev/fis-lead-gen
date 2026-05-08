from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ClearingPartnerMergeSuggestion(Base):
    """Pending / accepted / rejected cluster of raw clearing-partner variants.

    The clustering pass groups distinct unmatched
    ``broker_dealers.current_clearing_partner`` values that look like the
    same firm in different shapes ("RBC Capital Markets, LLC" vs
    "RBC Capital Markets LLC") and persists each cluster here for admin
    review at /settings. Accepting a row creates a CompetitorProvider
    with the variants as aliases; rejecting flips the status.

    The unique ``cluster_signature`` (sha256 of sorted variant text) lets
    a re-run of the clustering pass detect "this exact cluster was
    already produced" and skip it, so a rejected suggestion never
    resurfaces unchanged. A different cluster (even a superset of a
    rejected one) will produce a new signature and surface as new
    pending. No machine-learning of past rejections — admin re-decides
    when the evidence changes.
    """

    __tablename__ = "clearing_partner_merge_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cluster_signature: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    variants: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    suggested_name: Mapped[str] = mapped_column(String(255), nullable=False)
    min_score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False, index=True
    )
    accepted_provider_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("competitor_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
