"""RAG layer for Doxie — semantic firm search backed by pgvector.

Phase 2 follow-up #5. The existing function-calling tools answer precise
"who is firm X" / "find me BDs in California" questions well, but they
miss conceptual queries like "find broker-dealers similar to Acme" or
"firms that focus on high-net-worth retail." This module:

- ``backfill_broker_dealers(db)`` / ``backfill_investment_advisors(db)``
  walk every BD / IA row, build a compact summary string, embed it with
  ``gemini-embedding-001`` (the same model the Vault RAG already uses),
  and upsert into ``chatbot_firm_embedding``. SHA-256 of the summary lets
  re-runs skip rows whose underlying data didn't change.
  ``run_post_pipeline_embed_backfill()`` chains both incrementally after
  the populate-all pipeline so the index keeps itself fresh.

- ``search(db, query, ...)`` embeds the user's query and runs a cosine
  top-K against the HNSW index. Returns hits with similarity scores so
  the tool layer can present them in rank order.

We don't subclass the existing Vault embedding helpers — both modules
just call ``vault_embeddings.embed_chunks`` / ``embed_query`` directly.
That keeps the shared Gemini-client surface flat and avoids tangling
two RAG domains in one abstraction.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer
from app.models.chatbot_firm_embedding import (
    ENTITY_TYPE_BROKER_DEALER,
    ENTITY_TYPE_INVESTMENT_ADVISOR,
    ChatbotFirmEmbedding,
)
from app.models.investment_advisor import InvestmentAdvisor
from app.services.vault_embeddings import embed_chunks, embed_query

logger = logging.getLogger(__name__)


# Backfill pages BD rows in this batch size. Kept under the embedding
# API's 100-per-batch cap and small enough that one failed batch doesn't
# wipe out a huge amount of in-flight work.
_BACKFILL_BATCH_SIZE = 50
# The embedded text is bounded by the embedding API's input length and
# by the VARCHAR(4000) column. We're well under both with the projection
# below — explicit cap mostly defends against an unusually long
# firm_operations_text.
_MAX_CONTENT_CHARS = 3500


@dataclass(frozen=True)
class SemanticSearchHit:
    """One result row from :meth:`ChatbotSemanticService.search`."""

    entity_type: str
    entity_id: int
    content: str
    # Cosine similarity in [-1, 1]; pgvector's ``<=>`` returns distance
    # (0=identical, 2=opposite) and we present ``1 - distance`` as the
    # more intuitive "1.0 = perfect match" number.
    similarity: float


@dataclass(frozen=True)
class BackfillResult:
    embedded: int
    skipped: int
    failed: int


class ChatbotSemanticService:
    """Embed + retrieve firm summaries for Doxie's RAG tool."""

    async def backfill_broker_dealers(self, db: AsyncSession) -> BackfillResult:
        """Embed every BD that's new or whose summary text changed."""
        return await self._backfill_entities(
            db,
            model=BrokerDealer,
            entity_type=ENTITY_TYPE_BROKER_DEALER,
            content_builder=_build_broker_dealer_content,
        )

    async def backfill_investment_advisors(
        self, db: AsyncSession
    ) -> BackfillResult:
        """Embed every IA that's new or whose summary text changed."""
        return await self._backfill_entities(
            db,
            model=InvestmentAdvisor,
            entity_type=ENTITY_TYPE_INVESTMENT_ADVISOR,
            content_builder=_build_investment_advisor_content,
        )

    async def _backfill_entities(
        self,
        db: AsyncSession,
        *,
        model: type[Any],
        entity_type: str,
        content_builder: Callable[[Any], str],
    ) -> BackfillResult:
        """Shared incremental backfill loop, parametrized per entity.

        Pages through the entity table in ``_BACKFILL_BATCH_SIZE`` chunks.
        Per batch:
          1. Build the content string for each row.
          2. Hash + look up existing embeddings; skip rows whose hash is
             unchanged.
          3. Embed only the changed/new rows via ``embed_chunks``.
          4. Upsert into ``chatbot_firm_embedding`` keyed on
             ``(entity_type, entity_id)``.

        A batch that fails embedding counts every row in it as ``failed``
        and the loop moves on. That keeps a single transient Gemini error
        from killing the whole backfill — the next run picks up the
        unembedded rows.
        """
        embedded_total = 0
        skipped_total = 0
        failed_total = 0
        offset = 0

        while True:
            page_stmt = (
                select(model)
                .order_by(model.id.asc())
                .offset(offset)
                .limit(_BACKFILL_BATCH_SIZE)
            )
            page = list((await db.execute(page_stmt)).scalars().all())
            if not page:
                break

            page_ids = [row.id for row in page]
            existing_stmt = select(
                ChatbotFirmEmbedding.entity_id,
                ChatbotFirmEmbedding.content_hash,
            ).where(
                ChatbotFirmEmbedding.entity_type == entity_type,
                ChatbotFirmEmbedding.entity_id.in_(page_ids),
            )
            existing_hashes = {
                row.entity_id: row.content_hash
                for row in (await db.execute(existing_stmt)).all()
            }

            to_embed_ids: list[int] = []
            to_embed_contents: list[str] = []
            for entity in page:
                content = content_builder(entity)
                if not content:
                    skipped_total += 1
                    continue
                h = _content_hash(content)
                if existing_hashes.get(entity.id) == h:
                    skipped_total += 1
                    continue
                to_embed_ids.append(entity.id)
                to_embed_contents.append(content)

            if to_embed_contents:
                try:
                    vectors = await embed_chunks(to_embed_contents)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Doxie embedding batch failed entity_type=%s "
                        "offset=%d size=%d",
                        entity_type,
                        offset,
                        len(to_embed_contents),
                    )
                    failed_total += len(to_embed_contents)
                else:
                    for entity_id, content, vector in zip(
                        to_embed_ids, to_embed_contents, vectors
                    ):
                        await self._upsert_embedding(
                            db,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            content=content,
                            content_hash=_content_hash(content),
                            embedding=vector,
                        )
                    await db.commit()
                    embedded_total += len(to_embed_contents)

            offset += _BACKFILL_BATCH_SIZE

        return BackfillResult(
            embedded=embedded_total,
            skipped=skipped_total,
            failed=failed_total,
        )

    async def search(
        self,
        db: AsyncSession,
        *,
        query: str,
        entity_types: Sequence[str] | None = None,
        limit: int = 5,
    ) -> list[SemanticSearchHit]:
        """Cosine top-K against the HNSW index.

        ``entity_types`` filters which rows are eligible; ``None`` lets
        any entity through (v1 only populates broker-dealers, so the
        filter is effectively a no-op there).
        """
        clean_query = query.strip()
        if not clean_query:
            return []
        limit = max(1, min(limit, 25))

        query_vector = await embed_query(clean_query)

        # pgvector exposes ``<=>`` for cosine distance. We bind the vector
        # as a stringified literal that pgvector parses on the server —
        # simpler than registering a custom param type just for one call.
        # The HNSW index is selected by the ``ORDER BY embedding <=>`` shape.
        vector_literal = "[" + ",".join(f"{v:.6f}" for v in query_vector) + "]"
        sql_lines = [
            "SELECT entity_type, entity_id, content, ",
            "       1 - (embedding <=> CAST(:vec AS vector)) AS similarity",
            "FROM chatbot_firm_embedding",
        ]
        params: dict[str, object] = {"vec": vector_literal, "limit": limit}
        if entity_types:
            sql_lines.append("WHERE entity_type = ANY(:types)")
            params["types"] = list(entity_types)
        sql_lines.append("ORDER BY embedding <=> CAST(:vec AS vector)")
        sql_lines.append("LIMIT :limit")
        sql = text("\n".join(sql_lines))

        rows = (await db.execute(sql, params)).all()
        return [
            SemanticSearchHit(
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                content=row.content,
                similarity=float(row.similarity),
            )
            for row in rows
        ]

    @staticmethod
    async def _upsert_embedding(
        db: AsyncSession,
        *,
        entity_type: str,
        entity_id: int,
        content: str,
        content_hash: str,
        embedding: list[float],
    ) -> None:
        """ON CONFLICT (entity_type, entity_id) DO UPDATE.

        Postgres ON CONFLICT is the cleanest way to express upsert here —
        a "select then update or insert" pair would race under concurrent
        backfill triggers.
        """
        stmt = pg_insert(ChatbotFirmEmbedding).values(
            entity_type=entity_type,
            entity_id=entity_id,
            content=content,
            content_hash=content_hash,
            embedding=embedding,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_chatbot_firm_embedding_entity",
            set_={
                "content": stmt.excluded.content,
                "content_hash": stmt.excluded.content_hash,
                "embedding": stmt.excluded.embedding,
            },
        )
        await db.execute(stmt)


# ── Content builder + hash ──────────────────────────────────────────────


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _build_broker_dealer_content(bd: BrokerDealer) -> str:
    """Assemble the compact summary string used for the embedding input.

    Kept dense — every line is one piece of information the embedding
    model can match a query against. Long-tail prose
    (``firm_operations_text``) is the last block so we can truncate
    cleanly without losing identifying fields.
    """
    parts: list[str] = []

    name_line = bd.name or ""
    dba = getattr(bd, "dba_names", None)
    if isinstance(dba, list) and dba:
        valid_aliases = [d for d in dba if isinstance(d, str) and d.strip()]
        if valid_aliases:
            name_line = f"{name_line} (also known as: {', '.join(valid_aliases[:5])})"
    if name_line:
        parts.append(f"Firm: {name_line}")

    location_bits = [b for b in (bd.city, bd.state) if b]
    if location_bits:
        parts.append("Location: " + ", ".join(location_bits))

    if bd.status:
        parts.append(f"Status: {bd.status}")
    if bd.business_type:
        parts.append(f"Business type: {bd.business_type}")
    if bd.lead_priority:
        parts.append(f"Lead priority: {bd.lead_priority}")
    if bd.current_clearing_partner:
        parts.append(f"Clearing partner: {bd.current_clearing_partner}")
    if bd.clearing_classification:
        parts.append(f"Clearing classification: {bd.clearing_classification}")

    types = getattr(bd, "types_of_business", None)
    if isinstance(types, list) and types:
        joined = ", ".join(t for t in types if isinstance(t, str))
        if joined:
            parts.append(f"Lines of business: {joined}")

    if bd.firm_operations_text:
        parts.append(f"Operations: {bd.firm_operations_text}")

    return _truncate_content("\n".join(p for p in parts if p))


def _build_investment_advisor_content(advisor: InvestmentAdvisor) -> str:
    """IA counterpart of the BD builder — a Form ADV-derived summary.

    Same shape rules: dense identifying lines first, long-tail prose
    (``firm_operations_text``) last so truncation never costs the fields
    a query is most likely to match on.
    """
    parts: list[str] = []

    name_line = advisor.name or ""
    aliases = advisor.other_business_names
    if isinstance(aliases, list) and aliases:
        valid_aliases = [a for a in aliases if isinstance(a, str) and a.strip()]
        if valid_aliases:
            name_line = (
                f"{name_line} (also known as: {', '.join(valid_aliases[:5])})"
            )
    if name_line:
        parts.append(f"Investment advisor: {name_line}")

    location_bits = [b for b in (advisor.city, advisor.state) if b]
    if location_bits:
        parts.append("Location: " + ", ".join(location_bits))

    if advisor.status:
        parts.append(f"Status: {advisor.status}")
    if advisor.regulatory_aum is not None:
        parts.append(f"Regulatory AUM: {advisor.regulatory_aum:,.0f} USD")
    if advisor.files_13f:
        parts.append("Files 13F: yes")

    activities = advisor.advisory_activities
    if isinstance(activities, list) and activities:
        joined = ", ".join(str(a) for a in activities[:10] if a)
        if joined:
            parts.append(f"Advisory activities: {joined}")

    client_types = advisor.client_types
    if isinstance(client_types, list) and client_types:
        joined = ", ".join(str(c) for c in client_types[:10] if c)
        if joined:
            parts.append(f"Client types: {joined}")

    if advisor.firm_operations_text:
        parts.append(f"Operations: {advisor.firm_operations_text}")

    return _truncate_content("\n".join(p for p in parts if p))


def _truncate_content(text_blob: str) -> str:
    if len(text_blob) > _MAX_CONTENT_CHARS:
        return text_blob[: _MAX_CONTENT_CHARS - 1] + "…"
    return text_blob


# ── Post-pipeline incremental embed hook ────────────────────────────────


async def run_post_pipeline_embed_backfill() -> None:
    """Incremental embed pass fired after a data pipeline lands.

    Wired as the last statement of the populate-all background task so
    new/changed firms become semantically searchable without the manual
    admin backfill. Hash-skip makes re-runs cheap (only changed rows hit
    the embedding API).

    Failure-isolated by contract: opens its own session, swallows every
    exception, and returns early when the Gemini key is absent — it must
    never affect the pipeline run that triggered it.
    """
    if not settings.gemini_api_key:
        logger.info(
            "Doxie post-pipeline embed backfill skipped: GEMINI_API_KEY "
            "not configured"
        )
        return
    try:
        service = ChatbotSemanticService()
        async with SessionLocal() as db:
            bd = await service.backfill_broker_dealers(db)
            ia = await service.backfill_investment_advisors(db)
        logger.info(
            "Doxie post-pipeline embed backfill: BD embedded=%d skipped=%d "
            "failed=%d; IA embedded=%d skipped=%d failed=%d",
            bd.embedded,
            bd.skipped,
            bd.failed,
            ia.embedded,
            ia.skipped,
            ia.failed,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Doxie post-pipeline embed backfill failed")
