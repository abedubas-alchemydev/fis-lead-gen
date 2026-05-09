"""add vault_folder_file + vault_folder_chunk + pgvector + outreach_instructions

Schema for the Vault RAG follow-up (see ``plans/vault-rag-2026-05-04.md``):

  * Enables the ``vector`` extension (pgvector) on the target Neon branch
    so the chunk table can store 768-dim Gemini embeddings
  * Adds a freeform ``outreach_instructions`` column on ``vault_folder``
    for permanent per-service prompt guidance (Deshorn's ask 2026-05-04)
  * Creates ``vault_folder_file`` — one row per uploaded artifact, holding
    the GCS object name, processing status, and extracted plain text
  * Creates ``vault_folder_chunk`` — chunked + embedded text, indexed by
    HNSW for cosine similarity retrieval

The CREATE EXTENSION call is wrapped in IF NOT EXISTS so re-running the
migration on an environment that already has pgvector is a no-op. Neon
ships pgvector in the default image — no per-branch install step.

Revision ID: 20260505_0028
Revises: 20260504_0027
Create Date: 2026-05-05

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260505_0028"
down_revision: str | None = "20260504_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. pgvector extension — Neon ships it preinstalled so this just
    # registers it on the current database. Idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Permanent per-service prompt guidance. Default '' so existing rows
    # backfill cleanly without violating NOT NULL.
    op.add_column(
        "vault_folder",
        sa.Column(
            "outreach_instructions",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )

    # 3. vault_folder_file — uploaded artifact metadata + extracted text.
    # processing_status walks: extracting -> embedding -> ready (or failed
    # at any step). The FE polls non-terminal rows on a 2s interval.
    op.create_table(
        "vault_folder_file",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "folder_id",
            sa.BigInteger(),
            sa.ForeignKey("vault_folder.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "gcs_object_name",
            sa.String(length=512),
            nullable=False,
            unique=True,
        ),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column(
            "processing_status",
            sa.String(length=16),
            nullable=False,
            server_default="extracting",
        ),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column(
            "processing_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "processing_finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_vault_folder_file_folder_id",
        "vault_folder_file",
        ["folder_id"],
    )
    # Status filter is the second-most-common predicate (FE list view +
    # the outreach service excluding non-ready files); a partial index on
    # the hot statuses pays for itself fast.
    op.create_index(
        "ix_vault_folder_file_status",
        "vault_folder_file",
        ["processing_status"],
    )

    # 4. vault_folder_chunk — chunk text + 768-dim Gemini embedding.
    # The Vector column type is provided by the pgvector extension; we
    # express it via op.execute since op.create_table doesn't know about
    # the extension's custom type without importing pgvector.sqlalchemy
    # at migration time (we don't want a hard import dependency on a
    # pip package inside the alembic env, since alembic is invoked at
    # container boot before site-packages may be fully resolved).
    op.execute(
        """
        CREATE TABLE vault_folder_chunk (
            id BIGSERIAL PRIMARY KEY,
            file_id BIGINT NOT NULL REFERENCES vault_folder_file(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding vector(768) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.create_index(
        "ix_vault_folder_chunk_file_id",
        "vault_folder_chunk",
        ["file_id"],
    )
    # HNSW chosen over IVFFlat: corpora are small (<100k chunks total
    # across all users), and HNSW gives better recall at small N
    # without needing the train-on-real-data step IVFFlat requires.
    op.execute(
        """
        CREATE INDEX ix_vault_folder_chunk_embedding_hnsw
        ON vault_folder_chunk
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vault_folder_chunk_embedding_hnsw")
    op.drop_index(
        "ix_vault_folder_chunk_file_id",
        table_name="vault_folder_chunk",
    )
    op.execute("DROP TABLE IF EXISTS vault_folder_chunk")

    op.drop_index(
        "ix_vault_folder_file_status",
        table_name="vault_folder_file",
    )
    op.drop_index(
        "ix_vault_folder_file_folder_id",
        table_name="vault_folder_file",
    )
    op.drop_table("vault_folder_file")

    op.drop_column("vault_folder", "outreach_instructions")
    # Intentionally do NOT drop the vector extension on downgrade — other
    # tables in the DB might depend on it, and DROP EXTENSION cascades
    # to dependent objects which is far too destructive for an Alembic
    # downgrade. Operators can drop it manually if truly needed.
