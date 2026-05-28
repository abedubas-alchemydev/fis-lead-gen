"""Institutional-investor contact gap-fill runner.

Per-investor runner that walks existing ``investor_contacts`` rows for
an institutional investor, re-queries the discovery chain for any row
missing LinkedIn/email/phone, and merges new hits in place without
overwriting existing data.

Differs from the broker-dealer variant by an early entity-filer
short-circuit: an investor row whose contact ``name`` parses as an
entity (LLC / LP / GP / Fund / ...) per
:func:`app.services.form4_apollo.looks_like_entity` cannot be resolved
by Apollo's / PDL's person-only APIs, so the runner skips the discovery
chain for that row and records the skip in the run summary. Mirror of
the Form 4 path's pre-call guard added in PR #580.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investor_contact import InvestorContact
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
from app.services.form4_apollo import looks_like_entity

logger = logging.getLogger(__name__)


GAP_FILL_II_CONTACTS_PIPELINE_NAME = "institutional_investor_gap_fill_contacts"
GAP_FILL_COOLDOWN_DAYS = 30


async def run_gap_fill_ii_contacts_background(
    run_id: int, investor_id: int, trigger_source: str
) -> None:
    """Defensive wrapper so an unhandled exception in the background task
    doesn't leave the PipelineRun stuck on ``queued``."""
    try:
        await _run_gap_fill_ii_contacts(run_id, investor_id, trigger_source)
    except Exception:
        logger.exception(
            "institutional-investor gap-fill-contacts background task failed "
            "(run_id=%s investor_id=%s)",
            run_id,
            investor_id,
        )


async def _run_gap_fill_ii_contacts(
    run_id: int, investor_id: int, trigger_source: str
) -> None:
    """Run the per-investor contact gap-fill job to terminal state."""
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is not None:
            run.status = "running"
            await db.commit()

    try:
        async with SessionLocal() as db:
            investor = await db.get(InstitutionalInvestor, investor_id)
            if investor is None:
                await _finalize_run(
                    run_id,
                    status="failed",
                    success=0,
                    failure=1,
                    summary="Investor row disappeared between queue and run.",
                )
                return
            firm_name = investor.name
            domain = _canonicalize_domain(_domain_from_website(investor.website))
            chain_org_name = _normalize_org_name(firm_name)

            rows_stmt = (
                select(InvestorContact)
                .where(InvestorContact.investor_id == investor_id)
                .order_by(InvestorContact.id.asc())
            )
            rows = list((await db.execute(rows_stmt)).scalars().all())

        gap_rows = [r for r in rows if is_gap_row(r)]
        if not rows:
            summary = "No contacts on file; nothing to gap-fill."
            await _stamp_attempt(investor_id)
            await _finalize_run(
                run_id, status="completed", success=1, failure=0, summary=summary
            )
            return
        if not gap_rows:
            summary = f"All {len(rows)} contact(s) already complete."[:180]
            await _stamp_attempt(investor_id)
            await _finalize_run(
                run_id, status="completed", success=1, failure=0, summary=summary
            )
            return

        filled = 0
        chain_hits = 0
        entity_skipped = 0
        async with SessionLocal() as db:
            row_ids = [r.id for r in gap_rows]
            db_rows_stmt = (
                select(InvestorContact)
                .where(InvestorContact.id.in_(row_ids))
                .order_by(InvestorContact.id.asc())
            )
            db_rows = list((await db.execute(db_rows_stmt)).scalars().all())
            for row in db_rows:
                # Entity-filer guard: Apollo/PDL are person-only APIs, so a
                # row whose name reads as an LLC/LP/GP/Fund/... can never
                # resolve. Skip before burning a credit. Mirror of the Form 4
                # path's pre-call guard (see services/form4_apollo.py).
                if looks_like_entity(row.name):
                    entity_skipped += 1
                    continue
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

        await _stamp_attempt(investor_id)
        skip_clause = (
            f", {entity_skipped} entity-shaped skipped" if entity_skipped else ""
        )
        summary = (
            f"Gap-filled {filled} of {len(gap_rows)} contact(s) "
            f"({chain_hits} chain hit(s), {len(rows)} total on file"
            f"{skip_clause})."
        )[:180]
        await _finalize_run(
            run_id, status="completed", success=1, failure=0, summary=summary
        )
    except Exception as exc:
        logger.exception(
            "institutional-investor gap-fill-contacts failed for investor %s",
            investor_id,
        )
        await _finalize_run(
            run_id,
            status="failed",
            success=0,
            failure=1,
            summary=f"{type(exc).__name__}: {str(exc)[:180]}",
        )


async def _stamp_attempt(investor_id: int) -> None:
    async with SessionLocal() as db:
        investor = await db.get(InstitutionalInvestor, investor_id)
        if investor is not None:
            investor.last_gap_fill_attempt_at = datetime.now(timezone.utc)
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
