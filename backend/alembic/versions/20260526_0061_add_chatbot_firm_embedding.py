"""Add chatbot_firm_embedding table + HNSW index for Doxie's RAG tool.

Revision ID: 20260526_0061
Revises: 20260526_0060
Create Date: 2026-05-26

Backs the ``semantic_firm_search`` tool. Polymorphic on
``entity_type`` (``broker_dealer`` / ``investment_advisor`` /
``institutional_investor``) so a single table hosts every entity
Doxie can semantically search; v1 only backfills broker-dealers.

pgvector is already enabled by migration ``20260505_0028`` (vault
RAG) — no extension setup needed here. The CREATE TABLE uses raw SQL
via ``op.execute`` for the same reason ``vault_folder_chunk`` did:
the pgvector custom type isn't reachable from alembic env without a
pip import we'd rather not have at boot time.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260526_0061"
down_revision: str | None = "20260526_0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE chatbot_firm_embedding (
            id BIGSERIAL PRIMARY KEY,
            entity_type VARCHAR(32) NOT NULL,
            entity_id BIGINT NOT NULL,
            content VARCHAR(4000) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            embedding vector(768) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_chatbot_firm_embedding_entity
                UNIQUE (entity_type, entity_id)
        )
        """
    )
    op.create_index(
        "ix_chatbot_firm_embedding_entity_type",
        "chatbot_firm_embedding",
        ["entity_type"],
        unique=False,
    )
    # HNSW cosine index — same family as vault_folder_chunk. Cosine is
    # the standard metric for sentence-level Gemini embeddings; pgvector
    # exposes it via ``vector_cosine_ops``. HNSW gives better recall at
    # small corpora than IVFFlat without needing a separate train step.
    op.execute(
        """
        CREATE INDEX ix_chatbot_firm_embedding_hnsw
        ON chatbot_firm_embedding
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chatbot_firm_embedding_hnsw")
    op.drop_index(
        "ix_chatbot_firm_embedding_entity_type",
        table_name="chatbot_firm_embedding",
    )
    op.execute("DROP TABLE IF EXISTS chatbot_firm_embedding")
    # pgvector extension intentionally left in place — other tables
    # (vault_folder_chunk, plus future RAG additions) depend on it.
