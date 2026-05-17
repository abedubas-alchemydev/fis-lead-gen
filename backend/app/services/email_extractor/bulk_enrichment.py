"""Background task: enrich every not-yet-enriched email in a scan.

Walks ``DiscoveredEmail`` rows where ``run_id == scan_id`` and
``enrichment_status != "enriched"``, then defers per-row enrichment to
``apollo_enrichment.enrich_discovered_email`` -- which already commits
``enrichment_status="error"`` on its own failure path. Per-row exceptions
are caught here so one bad row never aborts the batch.

Apollo ``/people/match`` is ~1-3 s per call. A small ``asyncio.sleep``
between rows keeps us comfortably under provider rate limits without
needing a token bucket. The endpoint hands this function to FastAPI's
``BackgroundTasks`` so it runs after the 202 response has been written.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.discovered_email import DiscoveredEmail
from app.models.extraction_run import ExtractionRun
from app.services.email_extractor.apollo_enrichment import (
    EnrichmentError,
    enrich_discovered_email,
)

logger = logging.getLogger(__name__)

INTER_ROW_PAUSE_SECONDS = 0.5
ENRICHED_STATUS = "enriched"


async def run_bulk_enrichment(scan_id: int) -> None:
    """Enrich every not-yet-enriched ``DiscoveredEmail`` for ``scan_id``.

    Opens a fresh ``AsyncSession`` because background tasks don't inherit
    the request-scoped session. Per-row failures are logged but do not
    abort the batch; each ``enrich_discovered_email`` call commits its
    own status update so the in-progress state is visible to clients
    polling ``GET /scans/{scan_id}``.

    Cancellation: before each row we re-read
    ``extraction_run.enrich_cancelled_at``; when set, we stop dispatching
    new rows and exit. Already-enriched rows stay enriched -- the loop
    never rolls back, and the in-flight Apollo call (if any) finishes
    before the cancel check fires for the next id.
    """
    async with SessionLocal() as db:
        candidate_ids = await _resolve_candidate_ids(db, scan_id)
        logger.info(
            "bulk_enrichment scan_id=%s candidates=%s", scan_id, len(candidate_ids)
        )
        processed = 0
        for email_id in candidate_ids:
            if await _is_cancelled(db, scan_id):
                logger.info(
                    "bulk_enrichment scan_id=%s cancelled after %s of %s rows",
                    scan_id,
                    processed,
                    len(candidate_ids),
                )
                return
            try:
                await enrich_discovered_email(db, email_id)
            except EnrichmentError as exc:
                logger.warning(
                    "bulk_enrichment scan_id=%s email_id=%s failed: %s",
                    scan_id,
                    email_id,
                    exc,
                )
            processed += 1
            await asyncio.sleep(INTER_ROW_PAUSE_SECONDS)


async def _resolve_candidate_ids(db: AsyncSession, scan_id: int) -> list[int]:
    """Return the ids of unenriched discovered_email rows for the scan.

    Filtered to rows where ``enrichment_status != "enriched"`` so a re-run
    of enrich-all only touches rows that haven't completed successfully
    yet (failed/no-match/not-enriched rows are all retried).
    """
    stmt = select(DiscoveredEmail.id).where(
        DiscoveredEmail.run_id == scan_id,
        DiscoveredEmail.enrichment_status != ENRICHED_STATUS,
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _is_cancelled(db: AsyncSession, scan_id: int) -> bool:
    """Fresh SELECT of ``extraction_run.enrich_cancelled_at``.

    Re-queried per row so the cancel endpoint's commit (in a separate
    session) becomes visible to this loop within one iteration. Cost is
    a single indexed PK lookup -- negligible next to the Apollo call.
    """
    stmt = select(ExtractionRun.enrich_cancelled_at).where(ExtractionRun.id == scan_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None
