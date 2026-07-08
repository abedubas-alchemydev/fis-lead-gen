"""User-owned snapshot of a discovered contact ("Save Contact").

A ``saved_contact`` row lets a user pin an individual contact surfaced by the
email extractor and revisit it later. The contact's fields are SNAPSHOTTED at
save time -- name / title / email / company / phone / linkedin_url are copied
onto the row -- so the saved record survives deletion of the originating scan.

``contact_id`` is therefore a bare integer reference, deliberately WITHOUT a
foreign key, because:

* the scan-side row (``discovered_email``) cascade-deletes when its
  ``extraction_run`` is pruned -- an FK here would either vanish the save
  (``ON DELETE CASCADE``) or block the prune (``RESTRICT``); an FK-less
  reference plus the on-row snapshot keeps the save intact and self-contained;
* ``source`` is a discriminator so the same store can pin contacts from other
  origins later (polymorphic by ``(source, contact_id)``) without one FK
  column per source.

The ``user_id`` ownership column and ``BigInteger`` primary key mirror
``favorite_list``: the rest of the schema (including BetterAuth's ``user.id``,
a ``String(255)``) uses no UUIDs, so a ``BigInteger`` surrogate keeps the PK
style uniform rather than introducing a hybrid scheme for one table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SavedContact(Base):
    """A user-pinned snapshot of a single discovered contact."""

    __tablename__ = "saved_contact"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "contact_id",
            name="uq_saved_contact_user_source_contact",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Origin discriminator for the snapshotted contact (e.g. "discovered_email").
    # Paired with contact_id it identifies the source row. String(32) mirrors
    # discovered_email.source rather than being an enum, so a new source can
    # land without a type migration.
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # Bare reference to the source row's id -- intentionally NOT a foreign key
    # so the snapshot outlives the source's cascade-delete (see module docstring).
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Snapshot columns. Widths match their discovered_email sources so copying
    # the value in can never truncate it: name/title/company <- enriched_* (255),
    # email <- email (320), phone <- enriched_phone (64),
    # linkedin_url <- enriched_linkedin_url (512).
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
