"""Integration tests for ``ChatbotSemanticService`` against Postgres + pgvector.

Embedding API calls are stubbed (we don't hit Gemini in CI), but the SQL
side runs for real — including the HNSW index path and the
``ON CONFLICT`` upsert that backs incremental backfill.

Marked ``integration`` so it only runs under ``pytest -m integration``
(the CI Backend Integration job, which pulls in Postgres 15 with pgvector).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer
from app.models.chatbot_firm_embedding import (
    ENTITY_TYPE_BROKER_DEALER,
    ChatbotFirmEmbedding,
)
from app.services import chatbot_semantic
from app.services.chatbot_semantic import ChatbotSemanticService

pytestmark = pytest.mark.integration


# Deterministic 768-dim fake vectors keyed on the SHA-256 of the input
# text. Identical text -> identical vector (so a search by content text
# matches the seeded row exactly at cosine 1.0); distinct text -> vectors
# whose collision probability is ~2^-256 in practice.
#
# The previous mapping (``abs(hash(t)) % 1000``) collapsed every input to
# 1 of 1000 buckets, which the birthday paradox turns into a ~50% tie
# probability once ~40 BDs accumulate in the shared integration DB. That
# made ``test_search_returns_top_k_with_similarity`` non-deterministically
# flaky: two different firms hashing to the same bucket got identical
# vectors and therefore identical similarity, so the top-1 ranking was
# at the mercy of insertion order.
def _fake_vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()  # 32 bytes
    vec = [0.0] * 768
    # Spread the 32 hash bytes across the leading 32 dims as floats in
    # [0, 1). Trailing dims stay zero — cosine similarity is dominated by
    # the leading non-zero dims, so distinct hashes produce vectors that
    # point in measurably different directions.
    for i, byte in enumerate(digest):
        vec[i] = byte / 255.0
    return vec


async def _seed_bd(name: str, *, firm_operations_text: str | None = None) -> int:
    """Insert one minimal BrokerDealer, return its id."""
    async with SessionLocal() as session:
        bd = BrokerDealer(
            name=name,
            city="New York",
            state="NY",
            status="active",
            matched_source="finra",
            is_deficient=False,
            current_clearing_is_competitor=False,
            firm_operations_text=firm_operations_text,
        )
        session.add(bd)
        await session.commit()
        await session.refresh(bd)
        return bd.id


@pytest.fixture
def patch_embedding_api(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace embed_chunks/embed_query with deterministic fakes.

    Records the inputs each call received so tests can assert how many
    rows were actually embedded.
    """
    record: dict[str, list[Any]] = {"chunks_calls": [], "query_calls": []}

    async def fake_embed_chunks(texts: list[str]) -> list[list[float]]:
        record["chunks_calls"].append(list(texts))
        return [_fake_vector(t) for t in texts]

    async def fake_embed_query(text: str) -> list[float]:
        record["query_calls"].append(text)
        return _fake_vector(text)

    monkeypatch.setattr(chatbot_semantic, "embed_chunks", fake_embed_chunks)
    monkeypatch.setattr(chatbot_semantic, "embed_query", fake_embed_query)
    return record


# ── Backfill ────────────────────────────────────────────────────────────


async def test_backfill_inserts_embeddings_for_each_bd(
    patch_embedding_api: dict[str, list[Any]],
) -> None:
    name_a = f"acme-{secrets.token_hex(4)}"
    name_b = f"beta-{secrets.token_hex(4)}"
    bd_a = await _seed_bd(name_a)
    bd_b = await _seed_bd(name_b)

    service = ChatbotSemanticService()
    async with SessionLocal() as session:
        result = await service.backfill_broker_dealers(session)

    assert result.embedded >= 2
    assert result.failed == 0

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ChatbotFirmEmbedding).where(
                    ChatbotFirmEmbedding.entity_id.in_([bd_a, bd_b])
                )
            )
        ).scalars().all()
    by_id = {r.entity_id: r for r in rows}
    assert set(by_id.keys()) == {bd_a, bd_b}
    assert all(r.entity_type == ENTITY_TYPE_BROKER_DEALER for r in rows)
    assert all(len(r.embedding) == 768 for r in rows)
    assert all(r.content_hash for r in rows)
    # Content snippet includes the firm name.
    assert name_a in by_id[bd_a].content
    assert name_b in by_id[bd_b].content


async def test_backfill_second_run_skips_unchanged_rows(
    patch_embedding_api: dict[str, list[Any]],
) -> None:
    """Hash-based skip — re-running on the same data shouldn't re-embed
    any rows that haven't changed."""
    name = f"acme-{secrets.token_hex(4)}"
    bd_id = await _seed_bd(name)

    service = ChatbotSemanticService()
    async with SessionLocal() as session:
        first = await service.backfill_broker_dealers(session)
    assert first.embedded >= 1

    # Snapshot of how many embed_chunks calls landed (any pre-existing
    # BDs in the test DB contributed to first.embedded too).
    first_call_count = sum(
        1 for c in patch_embedding_api["chunks_calls"] if c
    )

    async with SessionLocal() as session:
        second = await service.backfill_broker_dealers(session)

    # Every row should have been skipped — no new embed_chunks calls.
    second_call_count = sum(
        1 for c in patch_embedding_api["chunks_calls"] if c
    )
    assert second_call_count == first_call_count
    # The row this test cares about was skipped on the second run.
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(ChatbotFirmEmbedding).where(
                    ChatbotFirmEmbedding.entity_id == bd_id,
                    ChatbotFirmEmbedding.entity_type == ENTITY_TYPE_BROKER_DEALER,
                )
            )
        ).scalar_one()
    # The row still exists from the first run.
    assert row is not None


async def test_backfill_reembeds_only_changed_row(
    patch_embedding_api: dict[str, list[Any]],
) -> None:
    name = f"acme-{secrets.token_hex(4)}"
    bd_id = await _seed_bd(name, firm_operations_text="initial summary")

    service = ChatbotSemanticService()
    async with SessionLocal() as session:
        await service.backfill_broker_dealers(session)

    # Mutate the BD so its embedding text changes.
    async with SessionLocal() as session:
        bd = await session.get(BrokerDealer, bd_id)
        assert bd is not None
        bd.firm_operations_text = "completely different summary now"
        await session.commit()

    pre_chunks_count = len(patch_embedding_api["chunks_calls"])

    async with SessionLocal() as session:
        result = await service.backfill_broker_dealers(session)
    assert result.embedded >= 1

    # The changed row appears in a fresh embed_chunks call.
    new_calls = patch_embedding_api["chunks_calls"][pre_chunks_count:]
    embedded_texts = [t for batch in new_calls for t in batch]
    assert any(
        "completely different summary now" in t for t in embedded_texts
    )


# ── Search ──────────────────────────────────────────────────────────────


async def test_search_returns_top_k_with_similarity(
    patch_embedding_api: dict[str, list[Any]],
) -> None:
    """End-to-end: backfill a couple of BDs, then run a search whose
    embedding matches one of them. The matched row should rank first."""
    target_name = f"target-{secrets.token_hex(4)}"
    target_id = await _seed_bd(
        target_name,
        firm_operations_text="introducing broker for HNW retail clients",
    )
    decoy_id = await _seed_bd(f"decoy-{secrets.token_hex(4)}")

    service = ChatbotSemanticService()
    async with SessionLocal() as session:
        await service.backfill_broker_dealers(session)

    # Force the query embedding to match the target's vector by reusing
    # its content text as the query. The fake encoder hashes the text
    # so identical inputs produce identical outputs.
    async with SessionLocal() as session:
        target_row = (
            await session.execute(
                select(ChatbotFirmEmbedding).where(
                    ChatbotFirmEmbedding.entity_id == target_id,
                    ChatbotFirmEmbedding.entity_type == ENTITY_TYPE_BROKER_DEALER,
                )
            )
        ).scalar_one()
        target_content = target_row.content

    async with SessionLocal() as session:
        hits = await service.search(
            session,
            query=target_content,
            entity_types=[ENTITY_TYPE_BROKER_DEALER],
            limit=5,
        )

    assert hits, "search returned no results"
    # Target row should be #1 — identical vector → similarity ≈ 1.0.
    assert hits[0].entity_id == target_id
    assert hits[0].entity_type == ENTITY_TYPE_BROKER_DEALER
    assert hits[0].similarity > 0.99
    # Decoy with a different vector ranks lower (or is filtered by limit).
    if any(h.entity_id == decoy_id for h in hits):
        decoy_hit = next(h for h in hits if h.entity_id == decoy_id)
        assert decoy_hit.similarity < hits[0].similarity


async def test_search_empty_query_short_circuits(
    patch_embedding_api: dict[str, list[Any]],
) -> None:
    service = ChatbotSemanticService()
    async with SessionLocal() as session:
        hits = await service.search(session, query="   ")
    assert hits == []
    # embed_query must not have been called for a blank input.
    assert patch_embedding_api["query_calls"] == []


# Avoid unused-symbol lints when integration mode is off.
_ = (datetime, date)
