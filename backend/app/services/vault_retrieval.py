"""Cosine top-K retrieval over Vault folder chunks.

Embeds the caller-supplied query via ``vault_embeddings.embed_query``,
then runs a pgvector cosine-distance ORDER BY against
``vault_folder_chunk`` joined to ``vault_folder_file``. Results are
filtered to chunks belonging to ``ready`` files only — files still
extracting / embedding / failed are silently excluded so a draft never
references partial or broken content.

Returned ``RetrievedChunk`` rows preserve the original text plus enough
metadata for the Outreach prompt to surface a citation hint ("from <X>,
chunk #N") in a future iteration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.vault_embeddings import embed_query

logger = logging.getLogger(__name__)


DEFAULT_TOP_K = 5
MAX_TOP_K = 20


@dataclass(frozen=True)
class RetrievedChunk:
    """One chunk row plus its cosine similarity to the query."""

    chunk_id: int
    file_id: int
    chunk_index: int
    text: str
    original_filename: str
    # Cosine similarity in [0, 1] (1 = identical direction). Computed
    # from pgvector's ``<=>`` operator which returns a *distance* in
    # [0, 2]; we convert via 1 - (distance / 2) for a more intuitive
    # "higher = more relevant" score the FE/logging can display.
    similarity: float


async def retrieve_chunks(
    *,
    folder_id: int,
    query: str,
    db: AsyncSession,
    top_k: int = DEFAULT_TOP_K,
) -> list[RetrievedChunk]:
    """Return up to ``top_k`` chunks most similar to ``query`` for one folder.

    Empty list when the folder has no ``ready`` files yet — the caller
    (outreach endpoint) treats this as "no retrieval context" and falls
    back to description-only generation.
    """
    if not query or not query.strip():
        return []
    k = max(1, min(top_k, MAX_TOP_K))

    query_vec = await embed_query(query)

    # pgvector accepts the vector literal as a string of bracket-wrapped
    # comma-separated floats. Bind as a parameter so we don't have to
    # worry about SQL injection on the float repr.
    vec_literal = "[" + ",".join(repr(float(v)) for v in query_vec) + "]"

    sql = sa_text(
        """
        SELECT
            c.id            AS chunk_id,
            c.file_id       AS file_id,
            c.chunk_index   AS chunk_index,
            c.text          AS chunk_text,
            f.original_filename AS original_filename,
            (c.embedding <=> CAST(:qvec AS vector)) AS distance
        FROM vault_folder_chunk c
        JOIN vault_folder_file  f ON f.id = c.file_id
        WHERE f.folder_id = :folder_id
          AND f.processing_status = 'ready'
        ORDER BY c.embedding <=> CAST(:qvec AS vector)
        LIMIT :k
        """
    )

    result = await db.execute(
        sql,
        {"qvec": vec_literal, "folder_id": folder_id, "k": k},
    )
    rows = result.mappings().all()

    out: list[RetrievedChunk] = []
    for row in rows:
        # ``<=>`` returns cosine distance in [0, 2] for unit-norm
        # vectors. Convert to similarity in [0, 1] for caller ergonomics.
        distance = float(row["distance"])
        similarity = max(0.0, min(1.0, 1.0 - distance / 2.0))
        out.append(
            RetrievedChunk(
                chunk_id=int(row["chunk_id"]),
                file_id=int(row["file_id"]),
                chunk_index=int(row["chunk_index"]),
                text=str(row["chunk_text"]),
                original_filename=str(row["original_filename"]),
                similarity=similarity,
            )
        )
    return out
