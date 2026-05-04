"""Uploaded file metadata for the Vault RAG feature.

One row per artifact a user uploads into a ``vault_folder``. Holds the
GCS object reference (so the bytes are reachable for download), the
extracted plain text (so we don't re-extract on every retrieval), and a
``processing_status`` walk that the FE polls until terminal.

Mirrors the schema in Alembic migration ``20260505_0028``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# Walk: extracting -> embedding -> ready (terminal-success), or
# extracting/embedding -> failed (terminal-fail).
PROCESSING_STATUSES = ("extracting", "embedding", "ready", "failed")
TERMINAL_PROCESSING_STATUSES = frozenset({"ready", "failed"})


class VaultFolderFile(Base):
    """A single uploaded artifact attached to a Vault folder."""

    __tablename__ = "vault_folder_file"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    folder_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("vault_folder.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # Post-upload byte count, used to enforce per-user storage caps without
    # re-stat'ing GCS on every list call.
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Object key in the env's GCS bucket. Includes the user_id prefix so a
    # leaked key can't be guessed cross-tenant. Globally unique because
    # the prefix already disambiguates and we want a hard guarantee
    # against accidental reuse.
    gcs_object_name: Mapped[str] = mapped_column(
        String(512), nullable=False, unique=True
    )
    # Plain text extracted from the uploaded bytes. Null while
    # ``processing_status='extracting'``; populated before embedding.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="extracting",
        index=True,
    )
    # Free-form error message rendered inline in the FE row when the file
    # is in ``failed`` state. Cleared on a successful retry.
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processing_finished_at: Mapped[datetime | None] = mapped_column(
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
