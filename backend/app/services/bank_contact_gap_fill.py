"""Bank contact discovery + gap-fill runner ("Generate More Details").

Per-bank runner backing ``POST /banks/{id}/gap-fill-contacts``. Mirrors
:func:`app.services.advisor_refresh_orchestrator._run_gap_fill_contacts` and
the BD sibling in ``services.bd_contact_gap_fill``, adapted to the fact that a
bank has **no officer roster** (no ``executive_officers`` blob like an advisor,
no FINRA roster like a BD). So the two phases are:

1. **org→people discovery from the domain.** Anchor the discovery chain on the
   bank's website domain + the bank name as the organization and run a single
   org-level lookup via :func:`discover_bank_contact`. This is what lets a
   state-chartered bank with zero extracted contacts still surface a public
   contact channel from "Generate More Details". Idempotent: the discovery
   cache + ``uq_bank_contacts_dedupe`` mean re-runs update the org row in place
   rather than duplicating it.
2. **gap-fill the existing ``bank_contacts`` rows** (the people the PDF
   extractor already found) that are missing LinkedIn / email / phone, by
   re-walking the discovery chain per row and merging any new hits in place via
   :func:`gap_fill_common.apply_gap_fill` — never overwriting existing data.

A last-resort web fallback (Phase 3) runs only when
``settings.web_fallback_enabled``, mirroring the advisor/BD runners.

The whole job runs to a terminal ``PipelineRun`` state; unhandled errors are
absorbed into a ``failed`` status so a crashed BackgroundTask never leaves the
run stuck on ``queued``. There is no per-bank cooldown stamp (banks carry no
``last_gap_fill_attempt_at`` column and no nightly bulk runner shares the
budget); the endpoint enforces the cooldown + in-flight guard off the
``PipelineRun`` history instead.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.bank import Bank, BankContact
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
from app.services.contact_discovery.orchestrator import (
    _walk_chain,
    discover_bank_contact,
)
from app.services.contact_discovery.web_fallback import (
    GapPerson,
    discover_web_fallback,
)

logger = logging.getLogger(__name__)


GAP_FILL_BANK_CONTACTS_PIPELINE_NAME = "bank_gap_fill_contacts"
GAP_FILL_COOLDOWN_DAYS = 30


async def run_gap_fill_bank_contacts_background(
    run_id: int, bank_id: int, trigger_source: str
) -> None:
    """Defensive wrapper so an unhandled exception in the background task
    doesn't leave the PipelineRun stuck on ``queued``."""
    try:
        await _run_gap_fill_bank_contacts(run_id, bank_id, trigger_source)
    except Exception:
        logger.exception(
            "bank gap-fill-contacts background task failed "
            "(run_id=%s bank_id=%s)",
            run_id,
            bank_id,
        )


async def _run_gap_fill_bank_contacts(
    run_id: int, bank_id: int, trigger_source: str
) -> None:
    """Run the per-bank discovery + contact gap-fill job to terminal state."""
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is not None:
            run.status = "running"
            await db.commit()

    try:
        async with SessionLocal() as db:
            bank = await db.get(Bank, bank_id)
            if bank is None:
                await _finalize_run(
                    run_id,
                    status="failed",
                    success=0,
                    failure=1,
                    summary="Bank row disappeared between queue and run.",
                )
                return
            firm_name = bank.name
            domain = _canonicalize_domain(_domain_from_website(bank.website))
            chain_org_name = _normalize_org_name(firm_name)

            rows_stmt = (
                select(BankContact)
                .where(BankContact.bank_id == bank_id)
                .order_by(BankContact.id.asc())
            )
            rows = list((await db.execute(rows_stmt)).scalars().all())

        # PHASE 1 — org→people discovery from the domain. Banks have no roster,
        # so seed the panel with a single org-level lookup anchored on the
        # bank's website + name. Discovered rows come straight from the chain,
        # so they are NOT re-queried in Phase 2.
        discovered = 0
        if firm_name:
            async with SessionLocal() as db:
                entity = {
                    "type": "organization",
                    "org_name": chain_org_name or firm_name,
                    "domain": domain,
                }
                row = await discover_bank_contact(
                    entity, bank_id=bank_id, session=db
                )
                if row is not None:
                    discovered += 1
                # Commit so the row's apollo_person_id is persisted before
                # Apollo's async phone-reveal callback can race in (the webhook
                # 200s on a no-match and Apollo won't retry).
                await db.commit()

        # PHASE 2 — gap-fill the PRE-EXISTING rows (PDF-extracted people) that
        # are missing a channel. Re-fetch + commit each row individually so a
        # prior per-row commit's expire_on_commit never touches a stale object,
        # same reveal-race reasoning as Phase 1.
        gap_rows = [r for r in rows if is_gap_row(r)]
        filled = 0
        chain_hits = 0
        if gap_rows:
            gap_targets = [(r.id, r.name) for r in gap_rows]
            async with SessionLocal() as db:
                for row_id, row_name in gap_targets:
                    split = _split_officer_name(row_name)
                    if split is None:
                        continue
                    first_name, last_name = split
                    merged = await _walk_chain(
                        "person",
                        first_name=first_name,
                        last_name=last_name,
                        org_name=chain_org_name or row_name,
                        domain=domain,
                        cache_name=row_name,
                    )
                    if merged is None:
                        continue
                    chain_hits += 1
                    row = await db.get(BankContact, row_id)
                    if row is None:
                        continue
                    if apply_gap_fill(row, merged):
                        row.enriched_at = datetime.now(timezone.utc)
                        filled += 1
                        await db.commit()

        # PHASE 3 — last-resort web fallback for people STILL missing a channel
        # after the provider chain. Off by default and SILENT (no summary
        # clause): merged channels simply appear on the People icons and the
        # count is logged for ops. Mirrors the advisor/BD gap-fill Phase 3.
        if settings.web_fallback_enabled:
            async with SessionLocal() as db:
                wf_rows = list(
                    (
                        await db.execute(
                            select(BankContact)
                            .where(BankContact.bank_id == bank_id)
                            .order_by(BankContact.id.asc())
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
                        row = await db.get(BankContact, wf_row_id)
                        if row is not None and apply_gap_fill(row, wf_merged):
                            row.enriched_at = datetime.now(timezone.utc)
                            wf_filled += 1
                            await db.commit()
                    if wf_filled:
                        logger.info(
                            "bank %s: web fallback filled %d of %d gap row(s)",
                            bank_id,
                            wf_filled,
                            len(wf_people),
                        )

        summary = (
            f"Discovered {discovered} org-level contact(s); "
            f"gap-filled {filled} of {len(gap_rows)} existing contact(s) "
            f"({chain_hits} chain hit(s), {len(rows)} on file)."
        )[:180]
        await _finalize_run(
            run_id, status="completed", success=1, failure=0, summary=summary
        )
    except Exception as exc:
        logger.exception("bank gap-fill-contacts failed for bank %s", bank_id)
        await _finalize_run(
            run_id,
            status="failed",
            success=0,
            failure=1,
            summary=f"{type(exc).__name__}: {str(exc)[:180]}",
        )


async def _finalize_run(
    run_id: int,
    *,
    status: str,
    success: int,
    failure: int,
    summary: str,
) -> None:
    """Write the terminal state onto the standalone gap-fill PipelineRun.
    Mirror of ``bd_contact_gap_fill._finalize_run``."""
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
