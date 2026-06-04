"""Unit tests for the cross-folder vault retrieval primitive.

The single-folder ``retrieve_chunks`` needs a live pgvector DB (covered by
the CI integration job). These tests cover the new
``retrieve_chunks_for_folders`` logic that does NOT need a DB: the
short-circuits (empty query / empty folder set) and the row →
``RetrievedVaultChunk`` projection — including the cosine-distance →
similarity conversion, the folder context, and the top-k clamp.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import vault_retrieval
from app.services.vault_retrieval import retrieve_chunks_for_folders


@pytest.mark.asyncio
async def test_empty_folder_ids_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No folders → []  without embedding the query or touching the DB."""
    embed = AsyncMock()
    monkeypatch.setattr(vault_retrieval, "embed_query", embed)
    db = MagicMock()
    db.execute = AsyncMock()

    out = await retrieve_chunks_for_folders(
        folder_ids=[], query="anything", db=db, top_k=5
    )
    assert out == []
    embed.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_blank_query_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embed = AsyncMock()
    monkeypatch.setattr(vault_retrieval, "embed_query", embed)
    db = MagicMock()
    db.execute = AsyncMock()

    out = await retrieve_chunks_for_folders(
        folder_ids=[1, 2], query="   ", db=db, top_k=5
    )
    assert out == []
    embed.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_projects_rows_with_folder_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vault_retrieval, "embed_query", AsyncMock(return_value=[0.1, 0.2, 0.3])
    )
    rows = [
        {
            "chunk_id": 1,
            "file_id": 10,
            "folder_id": 2,
            "folder_name": "Stock Loan",
            "chunk_index": 0,
            "chunk_text": "Rates updated quarterly.",
            "original_filename": "rate_sheet.xlsx",
            # cosine distance 0.3 → similarity 1 - 0.3/2 = 0.85
            "distance": 0.3,
        }
    ]
    result_obj = MagicMock()
    result_obj.mappings.return_value.all.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=result_obj)

    out = await retrieve_chunks_for_folders(
        folder_ids=[2, 3], query="lending rates", db=db, top_k=5
    )
    assert len(out) == 1
    chunk = out[0]
    assert chunk.folder_id == 2
    assert chunk.folder_name == "Stock Loan"
    assert chunk.original_filename == "rate_sheet.xlsx"
    assert chunk.similarity == pytest.approx(0.85)
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_top_k_clamped_to_max_and_folder_ids_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vault_retrieval, "embed_query", AsyncMock(return_value=[0.1])
    )
    result_obj = MagicMock()
    result_obj.mappings.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=result_obj)

    await retrieve_chunks_for_folders(
        folder_ids=[1], query="x", db=db, top_k=9999
    )
    # db.execute(sql, params) — assert the bound params carry the clamped k
    # and the folder-id set the caller resolved.
    params = db.execute.await_args.args[1]
    assert params["k"] == vault_retrieval.MAX_TOP_K
    assert params["folder_ids"] == [1]
