from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.email_verification import EmailVerification
    from app.models.extraction_run import ExtractionRun


# How long the "finding phone…" spinner may stay up after a reveal is
# requested. Apollo's async phone-reveal callback normally lands in seconds;
# this generous ceiling bounds the wait so a row stops spinning when the
# callback never arrives (ephemeral person id, no mobile on file, dropped
# webhook). Module-level for now -- could move to settings if it needs to be
# tunable per-environment.
PHONE_REVEAL_TTL = timedelta(minutes=15)


class DiscoverySource(StrEnum):
    hunter = "hunter"
    apollo = "apollo"
    snov = "snov"
    site_crawler = "site_crawler"
    theharvester = "theharvester"


class DiscoveredEmail(Base):
    """A single email discovered during a scan, attributed to its source provider."""

    __tablename__ = "discovered_email"
    __table_args__ = (UniqueConstraint("run_id", "email", name="uq_discovered_email_run_email"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("extraction_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    bd_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("broker_dealers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    advisor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("investment_advisors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    bank_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("banks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enriched_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enriched_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enriched_linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    enriched_company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Email surfaced by Apollo enrichment -- the revealed personal email when
    # reveal_personal_emails is on, else the matched work email. Distinct from
    # ``email`` (the discovered address that keyed the /people/match lookup).
    enriched_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    enriched_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_status: Mapped[str] = mapped_column(String(32), server_default="not_enriched", nullable=False)
    # Apollo person id from /people/match, stamped during enrichment so the
    # async phone-reveal webhook can correlate a revealed number back to this
    # row (mirrors the contact tables; see endpoints/webhooks_apollo.py).
    apollo_person_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # When the async Apollo phone-reveal was requested (person matched, reveal
    # configured, no inline phone). Stamped by the enrichment orchestrator and
    # read by ``phone_reveal_pending`` to bound the spinner with a TTL. NULL =
    # no reveal in flight (or a legacy row from before this column existed).
    phone_reveal_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[ExtractionRun] = relationship(back_populates="discovered_emails")
    verifications: Mapped[list[EmailVerification]] = relationship(
        back_populates="discovered_email", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def phone_reveal_pending(self) -> bool:
        """True while an async Apollo phone-reveal callback is still expected.

        The row enriched and captured an ``apollo_person_id`` but has no phone
        yet, the reveal flow is configured (so a webhook will arrive), AND the
        reveal was requested within the last :data:`PHONE_REVEAL_TTL`. Lets the
        UI show "finding phone…" while a number may still land, then stop once
        the window lapses instead of spinning forever when Apollo never calls
        back (ephemeral person id, no mobile on file, dropped webhook).

        A row with ``phone_reveal_requested_at IS NULL`` is never pending. That
        is what clears every currently-stuck row the instant this code deploys:
        legacy pending rows predate the timestamp column, so they have no
        request time and immediately read as resolved. Late webhooks still
        write the phone -- the TTL governs only the spinner, not the data.
        """
        from app.services.contact_discovery._shared import (
            apollo_phone_reveal_configured,
        )

        if not (
            self.enrichment_status == "enriched"
            and self.enriched_phone is None
            and self.apollo_person_id is not None
            and apollo_phone_reveal_configured()
        ):
            return False

        requested_at = self.phone_reveal_requested_at
        if requested_at is None:
            return False
        # Stored as tz-aware (DateTime(timezone=True)), but tolerate a tz-naive
        # value (some drivers/backends hand one back) by reading it as UTC so a
        # naive/aware subtraction can't raise.
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - requested_at < PHONE_REVEAL_TTL
