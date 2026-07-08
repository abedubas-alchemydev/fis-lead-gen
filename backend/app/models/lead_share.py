"""DOX Share — password-protected public share links for curated lead lists.

Two tables:

* ``lead_share`` — one row per share link. ``token`` is the unguessable URL
  component (``secrets.token_urlsafe(32)``), stored **plaintext** so the
  public endpoint can look a share up directly by ``WHERE token = ...`` —
  the token is a *locator*, not a secret on its own; the scrypt-hashed
  password (returned exactly once at create/rotate time, never stored)
  gates the content. ``password_generation`` increments on every rotation
  so previously minted unlock cookies (which embed the generation) die
  instantly. ``failed_unlocks`` + ``failed_unlock_window_started_at`` back
  a fixed-window brute-force throttle on the public unlock endpoint.
  ``created_by`` / ``source_favorite_list_id`` are provenance only (``ON
  DELETE SET NULL``) — a share is a snapshot that must outlive both its
  creator's account and its source list.

* ``lead_share_item`` — the snapshot membership, polymorphic across the
  three shareable entity kinds. Each row sets exactly one of
  ``broker_dealer_id`` / ``advisor_id`` / ``bank_id`` — never two, never
  none. DB-side guard is the ``ck_lead_share_item_exactly_one_target``
  3-way XOR check (same pattern as
  ``ck_favorite_list_item_exactly_one_target``), plus per-entity partial
  unique indexes so the same entity can't appear twice in one share.

Why ``BigInteger`` primary keys (and not UUIDs): the rest of the schema
(including ``user.id``, which is ``String(255)`` from BetterAuth) does not
use UUIDs anywhere — sticking with ``BigInteger`` keeps the schema uniform
(see ``models/favorite_list.py`` for the original rationale).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LeadShare(Base):
    """A password-protected public share link over a snapshot of leads."""

    __tablename__ = "lead_share"
    __table_args__ = (UniqueConstraint("token", name="uq_lead_share_token"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Self-describing scrypt string (``scrypt$N$r$p$<b64salt>$<b64dk>``); the
    # plaintext is returned exactly once at create/rotate time, never stored.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    password_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_favorite_list_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("favorite_list.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    view_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_viewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_unlocks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    failed_unlock_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    # DB-side ON DELETE CASCADE on lead_share_item.share_id does the actual
    # cascading (passive_deletes) so deleting a share never loads 80 items.
    items: Mapped[list[LeadShareItem]] = relationship(
        back_populates="share",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="LeadShareItem.position",
    )


class LeadShareItem(Base):
    """One shared lead inside a ``lead_share`` snapshot.

    Polymorphic XOR across broker_dealer / advisor / bank (exactly one FK
    set — DB check ``ck_lead_share_item_exactly_one_target``). ``position``
    is the curated 1..N display order, unique per share; entity FKs are
    ``ON DELETE CASCADE`` so a firm deleted from DOX simply disappears from
    the share (positions keep their gaps — they order, they don't number).
    """

    __tablename__ = "lead_share_item"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
            "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END "
            "+ CASE WHEN bank_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_lead_share_item_exactly_one_target",
        ),
        UniqueConstraint(
            "share_id", "position", name="uq_lead_share_item_share_position"
        ),
        # Per-entity dedupe: partial unique indexes (a plain composite unique
        # over a nullable column never fires — NULLs compare distinct).
        Index(
            "uq_lead_share_item_share_bd",
            "share_id",
            "broker_dealer_id",
            unique=True,
            postgresql_where=text("broker_dealer_id IS NOT NULL"),
        ),
        Index(
            "uq_lead_share_item_share_advisor",
            "share_id",
            "advisor_id",
            unique=True,
            postgresql_where=text("advisor_id IS NOT NULL"),
        ),
        Index(
            "uq_lead_share_item_share_bank",
            "share_id",
            "bank_id",
            unique=True,
            postgresql_where=text("bank_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    share_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("lead_share.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    broker_dealer_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("broker_dealers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    advisor_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("investment_advisors.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    bank_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("banks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    share: Mapped[LeadShare] = relationship(back_populates="items")
