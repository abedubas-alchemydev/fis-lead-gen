"""Semantic-search embeddings for Doxie's RAG tool.

One row per (entity_type, entity_id) holding a 768-dim
``gemini-embedding-001`` vector of the firm's summary text. Polymorphic
on ``entity_type`` so the same table can host broker-dealer, investment-
advisor, and institutional-investor embeddings as we expand the RAG
surface; v1 only backfills broker-dealers.

``content_hash`` is a SHA-256 of the embedded text. The backfill skips
rows whose hash matches the stored value so re-runs are cheap and only
touch firms whose underlying summary changed.

Mirrors the schema in Alembic migration ``20260526_0061``. The HNSW
cosine-ops index on ``embedding`` is created via raw SQL there to dodge
the pgvector-Python import dependency in the alembic env.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# Pinned at 768 to match the existing ``vault_folder_chunk`` corpus —
# both tables use the same embedding model so future cross-table
# similarity calls (e.g. "find vault docs related to firm X") stay
# vector-compatible. Bumping the dim would force a re-embed of every
# entity AND a follow-up alembic migration for the column type +
# index rebuild.
EMBEDDING_DIM = 768

ENTITY_TYPE_BROKER_DEALER = "broker_dealer"
ENTITY_TYPE_INVESTMENT_ADVISOR = "investment_advisor"
ENTITY_TYPE_INSTITUTIONAL_INVESTOR = "institutional_investor"


class ChatbotFirmEmbedding(Base):
    __tablename__ = "chatbot_firm_embedding"
    __table_args__ = (
        # One embedding per (entity_type, entity_id). Doubles as the
        # backfill upsert conflict target.
        UniqueConstraint(
            "entity_type",
            "entity_id",
            name="uq_chatbot_firm_embedding_entity",
        ),
        # Filter-then-rerank queries hit on entity_type first, so a
        # plain btree index there keeps the HNSW step on a small
        # candidate set when the tool restricts to one entity type.
        Index(
            "ix_chatbot_firm_embedding_entity_type",
            "entity_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # The exact text fed to the embedding model. Kept for two reasons:
    # (1) the tool can surface a short snippet next to each hit so the
    # user knows why Doxie chose it, and (2) future debugging when
    # rerank quality drifts.
    content: Mapped[str] = mapped_column(String(4000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=False
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
