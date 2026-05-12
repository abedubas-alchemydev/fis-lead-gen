"""Embedded text chunks for the Vault RAG retrieval path.

One row per ~500-token slice of a ``vault_folder_file``'s extracted text,
plus the 768-dim Gemini embedding (`gemini-embedding-001`, MRL-truncated
from its native 3072 dims). The Outreach
draft path embeds the firm-context query and runs cosine top-K against
this table, scoped to the chunks belonging to files in the user-selected
folder.

Mirrors the schema in Alembic migration ``20260505_0028``.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# gemini-embedding-001 defaults to 3072 dims but supports MRL truncation
# via outputDimensionality. Pinned at 768 here so the Vector column +
# HNSW index stay stable; a future bump to 1536 / 3072 would force an
# Alembic migration AND a re-embed of the entire chunk corpus.
EMBEDDING_DIM = 768


class VaultFolderChunk(Base):
    """A chunk of an uploaded file's text plus its dense embedding."""

    __tablename__ = "vault_folder_chunk"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    file_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("vault_folder_file.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 0-based position in the source file. Useful for stable display
    # ordering when we surface citations in a future iteration.
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
