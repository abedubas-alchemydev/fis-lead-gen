"""Recovery for refresh-on-visit pipeline runs orphaned by instance churn.

The per-firm / per-advisor "refresh-all" work runs as an in-process FastAPI
``BackgroundTask``. That task is tied to the single Cloud Run instance that
handled the POST. When the instance is replaced (every revision deploy) or
scaled in, an in-flight task is killed mid-execution: the orchestrator marks
its ``PipelineRun`` row ``running`` at the start but never reaches the
finalize step, so the row is stranded ``running`` forever.

Two consequences this module addresses, together with the in-flight guard in
the refresh-all endpoints:

  * The stranded row pollutes the audit trail and KPIs.
  * The detail page's refresh-on-visit guard re-attaches every later visit to
    the zombie run and polls a status that will never go terminal, so the
    loading screen spins for its full client deadline on every visit.

The reaper runs on app startup (lifespan hook). Because every deploy restarts
the app, any task this process lost is cleaned up within one deploy cycle.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun

logger = logging.getLogger(__name__)

# A refresh-family run still ``running``/``queued`` past this age is treated
# as dead. Every pipeline in REFRESH_FAMILY_PIPELINES is a single-firm
# operation that finishes in seconds-to-minutes when healthy, so the
# threshold sits comfortably above the realistic ceiling while staying well
# below "obviously orphaned". Shared by the startup reaper and by the
# refresh-all endpoints' in-flight guard so the two never disagree about
# whether a run is still alive.
STALE_REFRESH_RUN_AGE = timedelta(minutes=10)

# Parent + single-firm child pipelines spawned by the refresh-on-visit flow
# (and by the standalone refresh-all loop, which hits the same endpoint).
# Deliberately EXCLUDES bulk / scheduled pipelines — broker_dealer_gap_fill,
# initial_load, form4_watcher, clearing_pdf_pipeline, and the bulk
# financial_pdf_pipeline (note: not the per-firm financial_pdf_pipeline_single)
# — which can legitimately run far longer than the threshold and must not be
# reaped out from under a still-live instance during a scale-up.
REFRESH_FAMILY_PIPELINES: frozenset[str] = frozenset(
    {
        "broker_dealer_refresh_all",
        "investment_advisor_refresh_all",
        "financial_pdf_pipeline_single",
        "broker_dealer_resolve_website",
        "broker_dealer_health_check",
        "broker_dealer_enrich_contacts",
        "broker_dealer_refresh_filings",
        "broker_dealer_refresh_clearing",
        "investment_advisor_refresh_owners_officers",
        "investment_advisor_resolve_website",
        "investment_advisor_refresh_filings",
        "investment_advisor_enrich_contacts",
    }
)

_REAPED_SUMMARY = "Interrupted: the worker was replaced before the run finished."


async def reap_stale_refresh_runs(db: AsyncSession) -> int:
    """Mark refresh-family runs stuck ``running``/``queued`` past the staleness
    threshold as ``failed``. Returns the number of rows updated.

    Idempotent and safe to run on every startup: scoped to single-firm
    pipelines older than the threshold, it cannot reap a still-live run on a
    concurrently-starting instance.
    """

    cutoff = datetime.now(timezone.utc) - STALE_REFRESH_RUN_AGE
    stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_name.in_(REFRESH_FAMILY_PIPELINES))
        .where(PipelineRun.status.in_(("running", "queued")))
        .where(PipelineRun.started_at < cutoff)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return 0

    now = datetime.now(timezone.utc)
    for run in rows:
        run.status = "failed"
        run.completed_at = now
        try:
            notes = json.loads(run.notes) if run.notes else {}
            if not isinstance(notes, dict):
                notes = {}
        except (json.JSONDecodeError, TypeError):
            notes = {}
        notes["summary"] = _REAPED_SUMMARY
        notes["reaped"] = True
        run.notes = json.dumps(notes)

    await db.commit()
    logger.warning(
        "Reaped %d stale refresh run(s): %s",
        len(rows),
        ", ".join(str(run.id) for run in rows),
    )
    return len(rows)


__all__ = [
    "REFRESH_FAMILY_PIPELINES",
    "STALE_REFRESH_RUN_AGE",
    "reap_stale_refresh_runs",
]
