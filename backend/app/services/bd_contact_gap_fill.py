"""Broker-dealer contact gap-fill runner.

Per-firm runner that walks existing ``executive_contacts`` rows for a
broker-dealer, re-queries the discovery chain for any row missing
LinkedIn/email/phone, and merges new hits in place without overwriting
existing data. Mirrors :func:`app.services.advisor_refresh_orchestrator._run_gap_fill_contacts`
but targets ``ExecutiveContact`` rows by ``bd_id`` and stamps
``BrokerDealer.last_gap_fill_attempt_at`` instead of the advisor column.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.models.pipeline_run import PipelineRun
from app.services.advisor_refresh_orchestrator import (
    _canonicalize_domain,
    _domain_from_website,
    _normalize_org_name,
    _split_officer_name,
)
from app.services.contact_discovery.gap_fill_common import (
    apply_gap_fill,
    is_gap_row,
)
from app.services.contact_discovery.orchestrator import _walk_chain
from app.services.contact_discovery.web_fallback import (
    GapPerson,
    discover_web_fallback,
)

logger = logging.getLogger(__name__)


GAP_FILL_BD_CONTACTS_PIPELINE_NAME = "broker_dealer_gap_fill_contacts"
GAP_FILL_COOLDOWN_DAYS = 30


async def run_gap_fill_bd_contacts_background(
    run_id: int, bd_id: int, trigger_source: str
) -> None:
    """Defensive wrapper so an unhandled exception in the background task
    doesn't leave the PipelineRun stuck on ``queued``."""
    try:
        await _run_gap_fill_bd_contacts(run_id, bd_id, trigger_source)
    except Exception:
        logger.exception(
            "broker-dealer gap-fill-contacts background task failed "
            "(run_id=%s bd_id=%s)",
            run_id,
            bd_id,
        )


async def _run_gap_fill_bd_contacts(
    run_id: int, bd_id: int, trigger_source: str
) -> None:
    """Run the per-broker-dealer contact gap-fill job to terminal state."""
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is not None:
            run.status = "running"
            await db.commit()

    try:
        async with SessionLocal() as db:
            bd = await db.get(BrokerDealer, bd_id)
            if bd is None:
                await _finalize_run(
                    run_id,
                    status="failed",
                    success=0,
                    failure=1,
                    summary="Broker-dealer row disappeared between queue and run.",
                )
                return
            firm_name = bd.name
            domain = _canonicalize_domain(_domain_from_website(bd.website))
            chain_org_name = _normalize_org_name(firm_name)

            rows_stmt = (
                select(ExecutiveContact)
                .where(ExecutiveContact.bd_id == bd_id)
                .order_by(ExecutiveContact.id.asc())
            )
            rows = list((await db.execute(rows_stmt)).scalars().all())

        gap_rows = [r for r in rows if is_gap_row(r)]
        if not rows:
            summary = "No contacts on file; nothing to gap-fill."
            await _stamp_attempt(bd_id)
            await _finalize_run(
                run_id, status="completed", success=1, failure=0, summary=summary
            )
            return
        if not gap_rows:
            summary = f"All {len(rows)} contact(s) already complete."[:180]
            await _stamp_attempt(bd_id)
            await _finalize_run(
                run_id, status="completed", success=1, failure=0, summary=summary
            )
            return

        filled = 0
        chain_hits = 0
        async with SessionLocal() as db:
            row_ids = [r.id for r in gap_rows]
            db_rows_stmt = (
                select(ExecutiveContact)
                .where(ExecutiveContact.id.in_(row_ids))
                .order_by(ExecutiveContact.id.asc())
            )
            db_rows = list((await db.execute(db_rows_stmt)).scalars().all())
            for row in db_rows:
                split = _split_officer_name(row.name)
                if split is None:
                    continue
                first_name, last_name = split
                merged = await _walk_chain(
                    "person",
                    first_name=first_name,
                    last_name=last_name,
                    org_name=chain_org_name or row.name,
                    domain=domain,
                    cache_name=row.name,
                )
                if merged is None:
                    continue
                chain_hits += 1
                if apply_gap_fill(row, merged):
                    row.enriched_at = datetime.now(timezone.utc)
                    filled += 1
            if filled:
                await db.commit()

        # PHASE 3 — last-resort web fallback for rows STILL missing a channel
        # after the chain. Off by default and SILENT (no summary clause): merged
        # channels appear on the People icons and the count is logged for ops.
        # Mirrors the advisor gap-fill Phase 3.
        if settings.web_fallback_enabled:
            async with SessionLocal() as db:
                wf_rows = list(
                    (
                        await db.execute(
                            select(ExecutiveContact)
                            .where(ExecutiveContact.bd_id == bd_id)
                            .order_by(ExecutiveContact.id.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
                wf_people = [
                    GapPerson(row_id=r.id, first_name=split[0], last_name=split[1])
                    for r in wf_rows
                    if is_gap_row(r) and (split := _split_officer_name(r.name)) is not None
                ]
                if wf_people:
                    wf_results = await discover_web_fallback(
                        domain=domain,
                        org_name=chain_org_name or firm_name,
                        people=wf_people,
                    )
                    wf_filled = 0
                    for wf_row_id, wf_merged in wf_results.items():
                        row = await db.get(ExecutiveContact, wf_row_id)
                        if row is not None and apply_gap_fill(row, wf_merged):
                            row.enriched_at = datetime.now(timezone.utc)
                            wf_filled += 1
                            await db.commit()
                    if wf_filled:
                        logger.info(
                            "broker-dealer %s: web fallback filled %d of %d gap row(s)",
                            bd_id,
                            wf_filled,
                            len(wf_people),
                        )

        await _stamp_attempt(bd_id)
        summary = (
            f"Gap-filled {filled} of {len(gap_rows)} contact(s) "
            f"({chain_hits} chain hit(s), {len(rows)} total on file)."
        )[:180]
        await _finalize_run(
            run_id, status="completed", success=1, failure=0, summary=summary
        )
    except Exception as exc:
        logger.exception("broker-dealer gap-fill-contacts failed for bd %s", bd_id)
        await _finalize_run(
            run_id,
            status="failed",
            success=0,
            failure=1,
            summary=f"{type(exc).__name__}: {str(exc)[:180]}",
        )


async def _stamp_attempt(bd_id: int) -> None:
    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is not None:
            bd.last_gap_fill_attempt_at = datetime.now(timezone.utc)
            await db.commit()


async def _finalize_run(
    run_id: int,
    *,
    status: str,
    success: int,
    failure: int,
    summary: str,
) -> None:
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            return
        run.status = status
        run.processed_items = success + failure
        run.success_count = success
        run.failure_count = failure
        run.completed_at = datetime.now(timezone.utc)
        existing_notes: dict = {}
        if run.notes:
            try:
                existing_notes = json.loads(run.notes)
            except json.JSONDecodeError:
                existing_notes = {}
        existing_notes["summary"] = summary
        run.notes = json.dumps(existing_notes)
        await db.commit()
