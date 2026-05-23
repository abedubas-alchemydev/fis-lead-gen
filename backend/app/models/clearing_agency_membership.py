from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# Frozen set of clearing agencies / SROs we track membership in. Kept in
# sync with the frontend ``CLEARING_AGENCIES`` constant and the importer's
# filename→agency map. "DTC" is the DTC *member* directory; DTCC's "DTC
# Reference Directories" is supplementary reference data, not membership.
CLEARING_AGENCIES: tuple[str, ...] = ("OCC", "DTC", "NSCC", "FICC-GOV", "FICC-MBS")


class ClearingAgencyMembership(Base):
    """A firm's membership in a clearing agency / SRO (OCC, DTC, NSCC, FICC).

    Polymorphic across broker-dealers and investment advisers via two
    nullable FK columns + an XOR check (the ``FavoriteListItem`` pattern):
    exactly one of ``broker_dealer_id`` / ``advisor_id`` is set on a row.

    Distinct from ``clearing_arrangements`` (per-filing clearing *partner*)
    and ``broker_dealers.clearing_classification`` (self / fully-disclosed /
    omnibus). This is SRO *membership*, sourced from the OCC/DTCC member
    directories and matched to firms by normalized name — hence the
    provenance columns (``member_name_raw``, ``source_file``,
    ``match_method``, ``match_confidence``) and the ``needs_review`` status
    for ambiguous name matches that must not auto-apply.
    """

    __tablename__ = "clearing_agency_memberships"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
            "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_clearing_agency_membership_one_firm",
        ),
        CheckConstraint(
            "agency IN ('OCC', 'DTC', 'NSCC', 'FICC-GOV', 'FICC-MBS')",
            name="ck_clearing_agency_membership_agency",
        ),
        CheckConstraint(
            "match_method IN ('exact_normalized', 'dba', 'alias', 'manual')",
            name="ck_clearing_agency_membership_match_method",
        ),
        CheckConstraint(
            "status IN ('active', 'needs_review', 'rejected')",
            name="ck_clearing_agency_membership_status",
        ),
        # One membership row per (firm, agency). Partial-per-side because the
        # firm FK is nullable and Postgres treats NULLs as distinct, so a
        # single composite unique wouldn't enforce it. These are the
        # importer's upsert conflict targets.
        Index(
            "uq_clearing_membership_bd_agency",
            "broker_dealer_id",
            "agency",
            unique=True,
            postgresql_where=text("broker_dealer_id IS NOT NULL"),
        ),
        Index(
            "uq_clearing_membership_advisor_agency",
            "advisor_id",
            "agency",
            unique=True,
            postgresql_where=text("advisor_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    broker_dealer_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker_dealers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    advisor_id: Mapped[int | None] = mapped_column(
        ForeignKey("investment_advisors.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agency: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    member_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    member_name_raw: Mapped[str] = mapped_column(String(255), nullable=False)
    source_file: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    match_method: Mapped[str] = mapped_column(String(24), nullable=False)
    match_confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
