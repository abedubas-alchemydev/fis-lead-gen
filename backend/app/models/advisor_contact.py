from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdvisorContact(Base):
    """Sibling of ``ExecutiveContact`` for the Investment Advisor list.

    Kept separate (rather than polymorphic on ``executive_contacts``) so
    the BD enrichment code path stays unchanged — adding an
    ``entity_type/entity_id`` discriminator to ``executive_contacts``
    would touch every BD repository, scoring, and alerting call site,
    and the duplication here is ~150 LOC of repository code that's far
    cheaper than that refactor.
    """

    __tablename__ = "advisor_contacts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    advisor_id: Mapped[int] = mapped_column(
        ForeignKey("investment_advisors.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="provider")
    discovery_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discovery_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    emails: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    phones: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Apollo's MongoDB-style person.id from the sync /people/match response.
    # Stamped on rows whose initial chain hit came from Apollo so the async
    # phone-reveal webhook (which only carries Apollo's person_id, not our
    # row id) can locate the row to merge phones into.
    apollo_person_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    enriched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
