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
from app.services.pipeline_names import FAVORITE_LIST_ENRICH_PIPELINE

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
# Deliberately EXCLUDES the OTHER bulk / scheduled pipelines —
# broker_dealer_gap_fill, initial_load, form4_watcher, clearing_pdf_pipeline,
# and the bulk financial_pdf_pipeline (note: not the per-firm
# financial_pdf_pipeline_single) — which can legitimately run far longer than
# the threshold, carry NO heartbeat, and must not be reaped out from under a
# still-live instance during a scale-up.
#
# ``favorite_list_enrich`` is the one bulk pipeline that IS in the family: it is
# also a long-runner, but unlike the others it stamps a heartbeat
# (notes.last_progress_at) — both per-firm progress writes AND a lifetime
# periodic background beat every couple of minutes — so ``is_run_stale`` reaps
# it ONLY once that heartbeat has actually gone quiet (worker/instance dead),
# never while it is healthily advancing or stuck inside one long firm/leaf op.
# Its canonical name is single-sourced from app.services.pipeline_names so a
# rename can't desync this matcher from the orchestrator that stamps it.
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
        "investment_advisor_refresh_iapd_summary",
        FAVORITE_LIST_ENRICH_PIPELINE,
    }
)

_REAPED_SUMMARY = "Interrupted: the worker was replaced before the run finished."


def _parse_heartbeat(notes: str | None) -> datetime | None:
    """Pull the ``last_progress_at`` heartbeat out of a run's ``notes`` JSON.

    The bulk favorite-list enrich worker stamps ``notes.last_progress_at`` on
    every per-firm progress write AND on a lifetime periodic beat (every couple
    of minutes for as long as the worker is alive), so the value stays fresh even
    while one firm or leaf op runs long. Returns a tz-aware datetime, or ``None``
    when the run carries no heartbeat — every single-firm refresh pipeline, which
    finalizes fast enough that ``started_at`` alone is a fine liveness proxy.
    """
    if not notes:
        return None
    try:
        data = json.loads(notes)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("last_progress_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def run_liveness_at(run: PipelineRun) -> datetime | None:
    """Most-recent proof-of-life for a run: its heartbeat when it has one, else
    ``started_at``.

    A long bulk run keeps a fresh heartbeat — from its lifetime periodic beat as
    well as its per-firm progress writes — so this stays recent no matter how
    long the whole run (or any single firm/leaf op) has been going. That is
    exactly what lets the in-flight dispatch guard and the reaper agree that a
    live run is alive, instead of judging it dead from ``started_at`` the moment
    it crosses the age threshold.
    """
    heartbeat = _parse_heartbeat(run.notes)
    started = run.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    candidates = [ts for ts in (heartbeat, started) if ts is not None]
    return max(candidates) if candidates else None


def is_run_stale(run: PipelineRun, *, now: datetime | None = None) -> bool:
    """True if the run has shown no proof-of-life within ``STALE_REFRESH_RUN_AGE``.

    Heartbeat-based liveness: a run whose worker is actively advancing (fresh
    ``last_progress_at``) is never stale even if it started hours ago; a run
    with no heartbeat falls back to ``started_at`` — unchanged behavior for the
    single-firm refresh pipelines. A run with no liveness signal at all is
    treated as stale.
    """
    at = run_liveness_at(run)
    if at is None:
        return True
    now = now or datetime.now(timezone.utc)
    return at < now - STALE_REFRESH_RUN_AGE


async def reap_stale_refresh_runs(db: AsyncSession) -> int:
    """Mark refresh-family runs stuck ``running``/``queued`` past the staleness
    threshold as ``failed``. Returns the number of rows updated.

    Idempotent and safe to run on every startup. The single-firm refresh
    pipelines finish fast, so ``started_at`` past the threshold is proof of an
    orphan. The one long-running member — bulk ``favorite_list_enrich`` — is
    protected by its heartbeat: ``is_run_stale`` only reaps it once
    ``notes.last_progress_at`` has actually gone quiet, and its lifetime periodic
    beat keeps that stamp fresh for the worker's whole life (even mid-firm), so a
    still-live bulk run on a concurrently-starting instance is never reaped
    mid-flight.
    """

    now = datetime.now(timezone.utc)
    cutoff = now - STALE_REFRESH_RUN_AGE
    stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_name.in_(REFRESH_FAMILY_PIPELINES))
        .where(PipelineRun.status.in_(("running", "queued")))
        .where(PipelineRun.started_at < cutoff)
    )
    candidates = list((await db.execute(stmt)).scalars().all())
    # ``started_at < cutoff`` is a cheap SQL prefilter; the heartbeat is the
    # real liveness signal. Keep only rows that are ALSO stale by heartbeat so a
    # still-live bulk favorite_list_enrich run (fresh notes.last_progress_at,
    # kept fresh by its lifetime periodic beat even mid-firm) is never reaped out
    # from under its worker. Single-firm runs carry no heartbeat, so
    # run_liveness_at falls back to started_at and they stay in — unchanged
    # behavior.
    rows = [run for run in candidates if is_run_stale(run, now=now)]
    if not rows:
        return 0

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
    "FAVORITE_LIST_ENRICH_PIPELINE",
    "REFRESH_FAMILY_PIPELINES",
    "STALE_REFRESH_RUN_AGE",
    "is_run_stale",
    "reap_stale_refresh_runs",
    "run_liveness_at",
]
