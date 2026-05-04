"""Async processing orchestrator for Vault file uploads.

Walks a single ``vault_folder_file`` row through:
    extracting -> embedding -> ready    (terminal-success)
or any of the above -> failed           (terminal-fail)

Designed to run inside a FastAPI ``BackgroundTasks`` slot kicked from
the upload endpoint. The function is intentionally idempotent on
``failed`` rows — the FE's "Retry" button calls the same orchestrator,
which clears the previous error and walks the row through the pipeline
again.

Failure mode: if the worker process dies mid-walk, the row stays in a
non-terminal status. v1 surfaces this as "stuck" in the FE and relies on
the user clicking Retry. A periodic sweeper cron is a v2 add when /
if it becomes a real ops problem.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models.vault_folder_chunk import VaultFolderChunk
from app.models.vault_folder_file import VaultFolderFile
from app.services.vault_chunker import chunk_text
from app.services.vault_embeddings import (
    VaultEmbeddingConfigurationError,
    VaultEmbeddingError,
    embed_chunks,
)
from app.services.vault_storage import VaultStorageError, download_bytes
from app.services.vault_text_extraction import (
    VaultExtractionError,
    extract_text,
)

logger = logging.getLogger(__name__)


async def _set_status(
    file_id: int,
    *,
    status: str,
    error: str | None = None,
    extracted_text: str | None = None,
    finished: bool = False,
) -> None:
    """Single-statement status transition. Uses a fresh session because
    BackgroundTasks slots don't inherit the request's AsyncSession."""
    values: dict[str, object] = {"processing_status": status}
    if error is not None:
        values["processing_error"] = error
    if extracted_text is not None:
        values["extracted_text"] = extracted_text
    if finished:
        values["processing_finished_at"] = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        await session.execute(
            update(VaultFolderFile)
            .where(VaultFolderFile.id == file_id)
            .values(**values)
        )
        await session.commit()


async def _load_file(file_id: int) -> VaultFolderFile | None:
    async with SessionLocal() as session:
        result = await session.execute(
            select(VaultFolderFile).where(VaultFolderFile.id == file_id)
        )
        return result.scalar_one_or_none()


async def _replace_chunks(file_id: int, chunks: list[str], embeddings: list[list[float]]) -> None:
    """Delete any prior chunks for this file (retry case), then insert
    the new embedded set in a single transaction."""
    async with SessionLocal() as session:
        await session.execute(
            VaultFolderChunk.__table__.delete().where(
                VaultFolderChunk.file_id == file_id
            )
        )
        for index, (text, vector) in enumerate(zip(chunks, embeddings, strict=True)):
            session.add(
                VaultFolderChunk(
                    file_id=file_id,
                    chunk_index=index,
                    text=text,
                    embedding=vector,
                )
            )
        await session.commit()


async def process_uploaded_file(file_id: int) -> None:
    """Walk a file row through extraction → embedding → ready.

    Catches every domain error individually so the row's
    ``processing_error`` reflects which stage failed. Any unexpected
    exception lands as a generic ``failed`` with a sanitized message —
    we never want a stack trace in user-facing JSON.
    """
    row = await _load_file(file_id)
    if row is None:
        logger.warning("vault_processing: file %s vanished before processing", file_id)
        return

    # Retry path: clear stale error so the FE shows "in flight" again.
    if row.processing_status == "failed":
        await _set_status(file_id, status="extracting", error=None)

    # ── Stage 1 — extract text from GCS bytes ──────────────────────────
    try:
        content = await download_bytes(row.gcs_object_name)
    except VaultStorageError as exc:
        await _set_status(
            file_id,
            status="failed",
            error=f"Could not read uploaded bytes: {exc}",
            finished=True,
        )
        return
    except Exception as exc:  # noqa: BLE001 — guard the worker boundary
        logger.exception("vault_processing: unexpected download error file=%s", file_id)
        await _set_status(
            file_id,
            status="failed",
            error="Could not read uploaded bytes (unexpected error).",
            finished=True,
        )
        return

    try:
        text = extract_text(content, row.mime_type)
    except VaultExtractionError as exc:
        await _set_status(
            file_id,
            status="failed",
            error=str(exc),
            finished=True,
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault_processing: unexpected extract error file=%s", file_id)
        await _set_status(
            file_id,
            status="failed",
            error="Extraction failed (unexpected error).",
            finished=True,
        )
        return

    await _set_status(
        file_id,
        status="embedding",
        extracted_text=text,
    )

    # ── Stage 2 — chunk + embed ────────────────────────────────────────
    chunks = chunk_text(text)
    if not chunks:
        # Extracted text was just whitespace after chunking heuristics.
        # File is technically "ready" — downloadable, listed — but the
        # corpus is empty so retrieval will skip it. Don't insert chunk
        # rows; mark ready with a soft note so the FE could surface "no
        # text indexed" if we want a yellow chip variant later.
        await _set_status(
            file_id,
            status="ready",
            error=None,
            finished=True,
        )
        return

    try:
        embeddings = await embed_chunks(chunks)
    except VaultEmbeddingConfigurationError as exc:
        await _set_status(
            file_id,
            status="failed",
            error=f"Embedding service not configured: {exc}",
            finished=True,
        )
        return
    except VaultEmbeddingError as exc:
        await _set_status(
            file_id,
            status="failed",
            error=str(exc),
            finished=True,
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault_processing: unexpected embed error file=%s", file_id)
        await _set_status(
            file_id,
            status="failed",
            error="Embedding failed (unexpected error).",
            finished=True,
        )
        return

    if len(embeddings) != len(chunks):
        # Defensive — embed_chunks should align by construction. If the
        # two lists drift it's a bug worth surfacing as a hard failure.
        await _set_status(
            file_id,
            status="failed",
            error=f"Embedding count mismatch: {len(embeddings)} vs {len(chunks)} chunks.",
            finished=True,
        )
        return

    try:
        await _replace_chunks(file_id, chunks, embeddings)
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault_processing: chunk persist error file=%s", file_id)
        await _set_status(
            file_id,
            status="failed",
            error=f"Could not persist embedded chunks: {exc}",
            finished=True,
        )
        return

    await _set_status(
        file_id,
        status="ready",
        error=None,
        finished=True,
    )
