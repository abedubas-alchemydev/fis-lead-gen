"""Gemini ``gemini-embedding-001`` client for the Vault RAG path.

Two entry points:

* ``embed_chunks(texts)`` — batch-embed up to 100 chunks per request.
  Used by ``vault_processing.py`` after a successful extraction to
  populate ``vault_folder_chunk.embedding``.
* ``embed_query(text)`` — single-shot embed at draft time. Used by
  ``vault_retrieval.py`` to turn the firm-context query into the vector
  that feeds the cosine top-K SQL.

Both share the same key-shape guard the rest of the Gemini call sites
use (see ``services/outreach.py`` / ``services/gemini_responses.py``).
Rate-limit / quota errors are mapped to ``VaultEmbeddingError`` so the
processing orchestrator can mark the file ``failed`` without leaking
provider-specific exceptions to the FE.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


_GEMINI_KEY_SHAPE = re.compile(r"^AIzaSy[A-Za-z0-9_\-]{33}$")
# text-embedding-004 was retired 2026-01-14. gemini-embedding-001
# defaults to 3072 dims; we truncate to 768 via outputDimensionality
# (MRL) so the existing Vector(768) column + HNSW index still fit.
_EMBEDDING_MODEL = "gemini-embedding-001"
_EMBEDDING_DIM = 768
# Gemini's batchEmbedContents accepts up to 100 requests per call. We
# stay under that explicitly; uploaders chunked into >100 pieces just
# fan out into multiple batches sequentially.
_MAX_BATCH_SIZE = 100
_MAX_RETRIES = 3
_TIMEOUT_SECONDS = 60.0
_TRANSIENT_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})


class VaultEmbeddingConfigurationError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing or malformed."""


class VaultEmbeddingError(RuntimeError):
    """Raised when the embedding API returns an error or unparseable body."""


def _validate_api_key() -> str:
    api_key = settings.gemini_api_key
    if not api_key:
        raise VaultEmbeddingConfigurationError(
            "GEMINI_API_KEY is not configured."
        )
    if not _GEMINI_KEY_SHAPE.match(api_key):
        raise VaultEmbeddingConfigurationError(
            f"GEMINI_API_KEY has invalid shape (length={len(api_key)}). "
            f"Expected 39 chars matching ^AIzaSy[A-Za-z0-9_-]{{33}}$."
        )
    return api_key


async def _post(url: str, payload: dict[str, object], api_key: str) -> dict[str, object]:
    """POST helper with bounded transient retries."""
    headers = {"x-goog-api-key": api_key}
    last_error: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code not in _TRANSIENT_STATUSES or attempt == _MAX_RETRIES:
                detail = exc.response.text.strip()
                raise VaultEmbeddingError(
                    f"Embedding request failed with status {exc.response.status_code}: "
                    f"{detail or 'No response body.'}"
                ) from exc
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == _MAX_RETRIES:
                raise VaultEmbeddingError(
                    "Embedding request failed due to a network error."
                ) from exc

        await asyncio.sleep(min(2**attempt, 8))

    raise VaultEmbeddingError(
        "Embedding request failed after retries."
    ) from last_error


def _parse_single(payload: dict[str, object]) -> list[float]:
    """Pull the 768-float vector out of an ``:embedContent`` response."""
    embedding = payload.get("embedding")
    if not isinstance(embedding, dict):
        raise VaultEmbeddingError("embed response missing 'embedding'")
    values = embedding.get("values")
    if not isinstance(values, list) or not values:
        raise VaultEmbeddingError("embed response missing 'values'")
    if len(values) != _EMBEDDING_DIM:
        raise VaultEmbeddingError(
            f"unexpected embedding dim {len(values)} (want {_EMBEDDING_DIM})"
        )
    return [float(v) for v in values]


def _parse_batch(payload: dict[str, object]) -> list[list[float]]:
    """Pull the per-chunk vectors out of a ``:batchEmbedContents`` response."""
    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list) or not embeddings:
        raise VaultEmbeddingError("batch response missing 'embeddings'")
    out: list[list[float]] = []
    for index, entry in enumerate(embeddings):
        if not isinstance(entry, dict):
            raise VaultEmbeddingError(f"embedding[{index}] malformed")
        values = entry.get("values")
        if not isinstance(values, list) or len(values) != _EMBEDDING_DIM:
            raise VaultEmbeddingError(
                f"embedding[{index}] has unexpected shape"
            )
        out.append([float(v) for v in values])
    return out


async def embed_query(text: str) -> list[float]:
    """Single-shot embed for a retrieval query.

    The query is embedded with ``task_type=RETRIEVAL_QUERY`` so it
    aligns with the document-side vectors that were embedded with
    ``RETRIEVAL_DOCUMENT``. Gemini's recommended pattern.
    """
    api_key = _validate_api_key()
    base_url = settings.gemini_api_base.rstrip("/")
    url = f"{base_url}/models/{_EMBEDDING_MODEL}:embedContent"
    payload = {
        "model": f"models/{_EMBEDDING_MODEL}",
        "content": {"parts": [{"text": text}]},
        "taskType": "RETRIEVAL_QUERY",
        "outputDimensionality": _EMBEDDING_DIM,
    }
    response = await _post(url, payload, api_key)
    return _parse_single(response)


async def embed_chunks(texts: list[str]) -> list[list[float]]:
    """Batch-embed chunk texts, returns one 768-dim vector per input.

    Auto-fanning across batches of ``_MAX_BATCH_SIZE`` keeps each HTTP
    request under Gemini's per-call cap and bounds memory pressure for
    large file uploads.
    """
    if not texts:
        return []
    api_key = _validate_api_key()
    base_url = settings.gemini_api_base.rstrip("/")
    url = f"{base_url}/models/{_EMBEDDING_MODEL}:batchEmbedContents"

    out: list[list[float]] = []
    for start in range(0, len(texts), _MAX_BATCH_SIZE):
        batch = texts[start : start + _MAX_BATCH_SIZE]
        payload = {
            "requests": [
                {
                    "model": f"models/{_EMBEDDING_MODEL}",
                    "content": {"parts": [{"text": chunk}]},
                    "taskType": "RETRIEVAL_DOCUMENT",
                    "outputDimensionality": _EMBEDDING_DIM,
                }
                for chunk in batch
            ]
        }
        response = await _post(url, payload, api_key)
        out.extend(_parse_batch(response))
    return out
