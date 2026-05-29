from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.email_verification import EmailVerification
    from app.models.extraction_run import ExtractionRun


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[ExtractionRun] = relationship(back_populates="discovered_emails")
    verifications: Mapped[list[EmailVerification]] = relationship(
        back_populates="discovered_email", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def phone_reveal_pending(self) -> bool:
        """True while an async Apollo phone-reveal callback is still expected.

        The row enriched and captured an ``apollo_person_id`` but has no phone
        yet, and the reveal flow is configured (so a webhook will arrive). Lets
        the UI poll for the number without polling forever when reveal isn't
        wired -- then no callback ever comes and this stays False.
        """
        from app.services.contact_discovery._shared import (
            apollo_phone_reveal_configured,
        )

        return (
            self.enrichment_status == "enriched"
            and self.enriched_phone is None
            and self.apollo_person_id is not None
            and apollo_phone_reveal_configured()
        )
