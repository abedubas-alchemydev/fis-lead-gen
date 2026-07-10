"""Bulk-enrich a favorite list — sequential, resilient background worker.

Backs ``POST /favorite-lists/{list_id}/enrich``. The endpoint owner-scopes
the list, enumerates every ``favorite_list_item`` (all five polymorphic
entity types), creates ONE parent :class:`PipelineRun`, and dispatches a
FastAPI BackgroundTask into :func:`run_favorite_list_enrich`.

Progress model — no new table. The parent ``PipelineRun`` row IS the
progress record; the live "which firm are we on" pointer lives in its
``notes`` JSON so the FE can poll ``GET /pipeline/run/{run_id}`` and render
a loader that survives a tab close. The notes shape while running is::

    {
      "list_id": 42,
      "stage": "running",              # queued | running | done
      "total": 12,
      "current_index": 3,              # 0-based pointer, item being processed
      "current_name": "Acme Securities LLC",
      "current_entity_type": "broker_dealer",
      "last_progress_at": "2026-07-10T12:34:56.789+00:00",  # heartbeat
      "items": [
        {"id": 101, "type": "broker_dealer", "name": "Acme",
         "status": "done",             # done | failed | skipped
         "reason": "refresh-all: completed; contacts: ok; ..."},
        ...
      ],
      "summary": "9 enriched, 2 skipped, 1 failed. ..."   # added on finalize
    }

``processed_items`` / ``success_count`` / ``failure_count`` are bumped per
item so a client that only reads the numeric counters still sees progress.

``last_progress_at`` is a heartbeat with TWO writers: every per-firm progress
write re-stamps it (driving the UI cadence), AND a lifetime periodic task
(:func:`_heartbeat_loop`) re-stamps it every ``_HEARTBEAT_INTERVAL_SECONDS`` for
as long as the worker is alive. A whole-list bulk run routinely runs longer than
``STALE_REFRESH_RUN_AGE`` (10 min), and a SINGLE firm's chain — or a single leaf
op like the email site scan — can itself exceed it, so liveness must NOT be
judged from ``started_at`` nor from the per-firm cadence alone. The lifetime beat
guarantees the heartbeat is never stale while the worker lives, no matter how
long any one firm/op takes; the in-flight dispatch guard and the startup reaper
both read it (via ``pipeline_reaper.is_run_stale``) so a live run always reads as
alive, while a genuinely orphaned run (worker/instance dead → no beat for the
threshold) is reaped. This is what stops the FE's re-attach POST from spawning a
SECOND worker that re-bills every provider. The two writers are serialized by an
``asyncio.Lock`` and the beat is a read-modify-write, so they never clobber each
other's notes.

Per-entity sequences (all gap-filled by reusing each kind's own
gate/orchestrator, and ALWAYS ending with email DISCOVERY — never
SMTP-verify or the email "enrich" step):

- ``broker_dealer`` — reuse
  :func:`refresh_all_orchestrator.run_refresh_all` (scope=all, gap-gated),
  then the ``/enrich`` contacts fan-out (officers seeded from the FINRA
  roster, ``refresh=True``), then ``extract-focus-ceo``, then an
  email-extractor scan on the resolved website.
- ``advisor`` — reuse
  :func:`advisor_refresh_orchestrator.run_advisor_refresh` (gap-gated),
  then ``_run_gap_fill_contacts``, then an email scan (advisor_id).
- ``bank`` — reuse
  :func:`bank_contact_gap_fill.run_gap_fill_bank_contacts_background`,
  then an email scan (bank_id, registrable domain).
- ``institutional_investor`` / ``reporting_owner`` — no enrichment flow
  exists yet, so they are SKIPPED with a noted reason (Deshorn's lists are
  BD/advisor/bank-heavy; these must not block the run).

Resilience: every item and every step is wrapped so a failure records a
reason and the worker keeps going. Feature gates are honored per the
calling user with admin bypass (MASTER_LIST for BD, INVESTMENT_ADVISORS
for advisor, BANKS for bank, EMAIL_EXTRACTOR for email discovery) as a
per-item skip-with-reason, and the 30s/(user, BD) refresh-all cooldown is
shared with the per-firm endpoint so bulk + detail-page clicks pay into one
budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.core.feature_permissions import (
    BANKS,
    EMAIL_EXTRACTOR,
    INVESTMENT_ADVISORS,
    MASTER_LIST,
)
from app.db.session import SessionLocal
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.services import advisor_refresh_orchestrator as adv_orch
from app.services import bank_contact_gap_fill as bank_gap
from app.services import refresh_all_orchestrator as bd_orch
from app.services.pipeline_names import FAVORITE_LIST_ENRICH_PIPELINE

logger = logging.getLogger(__name__)

# Canonical pipeline name, single-sourced from app.services.pipeline_names so a
# rename can't desync the reaper's staleness family / the /enrich in-flight guard
# (both match on it) from the parent runs this worker stamps. Re-exported under
# the historical ``*_NAME`` alias the endpoint + tests already import.
FAVORITE_LIST_ENRICH_PIPELINE_NAME = FAVORITE_LIST_ENRICH_PIPELINE

# Lifetime heartbeat cadence. A background task re-stamps notes.last_progress_at
# this often for as long as the worker is alive, so liveness (pipeline_reaper.
# is_run_stale) can NEVER go stale mid-run — even when a single firm's chain, or
# a single leaf op like the email site scan, runs longer than the reaper's
# STALE_REFRESH_RUN_AGE (10 min). Kept well under that threshold so even a couple
# of missed beats (transient DB blip) still can't trip the reaper/guard.
_HEARTBEAT_INTERVAL_SECONDS = 120

# The BD refresh-all cooldown window the per-firm endpoint enforces. Mirrored
# here (not imported — it's a module-private const on the endpoint) so bulk
# enrich and the detail-page button share one per-(user, BD) budget.
_BD_REFRESH_COOLDOWN_SECONDS = 30

# Bound BD per-officer discovery spend, matching the FE "Generate More Details"
# seed cap (broker-dealer-detail-client.tsx slices FINRA owners+officers to 10).
_MAX_BD_OFFICERS = 10

# Entity types that have no enrichment flow yet — skipped with a noted reason
# rather than blocking a BD/advisor/bank-heavy list.
_UNSUPPORTED_REASON = {
    "institutional_investor": "institutional_investor enrichment not supported yet",
    "reporting_owner": "reporting_owner enrichment not supported yet",
}


@dataclass(frozen=True)
class EnrichItem:
    """One resolved favorite-list row to enrich.

    ``entity_id`` is the surrogate id of the underlying firm (bd/advisor/
    investor/reporting_owner/bank); ``item_id`` is the ``favorite_list_item``
    row id (stable pointer the FE keys per-row status on).
    """

    item_id: int
    entity_type: str
    entity_id: int
    name: str


@dataclass
class _ItemOutcome:
    """Accumulates per-step results for a single item."""

    reasons: list[str] = field(default_factory=list)
    ran_ok: int = 0
    failed: int = 0

    def step(self, label: str, ok: bool) -> None:
        self.reasons.append(label)
        if ok:
            self.ran_ok += 1
        else:
            self.failed += 1

    def note(self, label: str) -> None:
        """Record a neutral (non-counted) step outcome, e.g. a clean skip."""
        self.reasons.append(label)

    @property
    def status(self) -> str:
        # A firm counts as "failed" only when every counted step failed and
        # none succeeded; otherwise it's "done" (gap-fill-and-continue: a
        # single provider miss shouldn't red-flag the whole firm).
        if self.failed > 0 and self.ran_ok == 0:
            return "failed"
        return "done"

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)[:300]


# ───────────────────────────────────────────────────────────────────────────
# Feature gates (admin bypass mirrors services.auth.ensure_feature)
# ───────────────────────────────────────────────────────────────────────────


def _feature_allowed(user: AuthenticatedUser, feature_key: str) -> bool:
    return user.role == "admin" or feature_key in user.feature_permissions


# ───────────────────────────────────────────────────────────────────────────
# Small run-row helpers
# ───────────────────────────────────────────────────────────────────────────


async def _create_child_run(
    *, pipeline_name: str, trigger_source: str, notes: dict, parent_run_id: int
) -> int:
    """Persist a queued child PipelineRun and return its id."""
    async with SessionLocal() as db:
        run = PipelineRun(
            pipeline_name=pipeline_name,
            trigger_source=trigger_source,
            status="queued",
            total_items=1,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes=json.dumps(notes),
            parent_run_id=parent_run_id,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return run.id


async def _read_run_status(run_id: int) -> str:
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        return run.status if run is not None else "unknown"


def _terminal_ok(status: str) -> bool:
    """A child run's terminal status that should count as "ran" (not failed)."""
    return status in ("completed", "completed_with_errors", "skipped")


async def _run_step(outcome: "_ItemOutcome", name: str, coro) -> None:
    """Await one enrichment step and fold its ``(label, ok)`` into ``outcome``.

    Any exception is contained here and recorded as a failed step so a single
    step blowing up never aborts the remaining steps for the same firm (nor
    the rest of the list) — the "catch per step, continue" contract.
    """
    try:
        label, ok = await coro
    except Exception as exc:  # noqa: BLE001 — one step must never abort the firm
        logger.exception("favorite-list enrich: %s step raised", name)
        outcome.step(f"{name}: error: {type(exc).__name__}", False)
        return
    outcome.step(label, ok)


# ───────────────────────────────────────────────────────────────────────────
# Domain helpers for the email-discovery step
# ───────────────────────────────────────────────────────────────────────────


def _domain_from_url(website: str | None) -> str | None:
    """Bare ``example.com`` from a firm website (strip scheme, path, ``www.``)."""
    website = (website or "").strip()
    if not website:
        return None
    candidate = website
    if "://" in candidate:
        candidate = candidate.split("://", 1)[1]
    candidate = candidate.split("/", 1)[0].strip().lower()
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate or None


# ───────────────────────────────────────────────────────────────────────────
# Shared email-discovery step (discovery ONLY — never verify / enrich-all)
# ───────────────────────────────────────────────────────────────────────────


async def _email_discovery_step(
    *,
    domain: str | None,
    bd_id: int | None = None,
    advisor_id: int | None = None,
    bank_id: int | None = None,
) -> tuple[str, bool]:
    """Create an ``ExtractionRun`` and drive the scan to terminal in place.

    Discovery only: we call the aggregator scan and stop. We never touch
    ``/scans/{id}/enrich-all`` or SMTP verify. A missing domain is a clean
    skip (not a failure) so a firm with no website doesn't red-flag the run.
    """
    if not domain:
        return "email: no domain, skipped", True

    # Imported lazily to keep this module's import graph flat and avoid
    # pulling the provider stack at import time (mirrors the refresh-all
    # orchestrator's lazy service imports).
    from app.models.discovered_email import DiscoveredEmail
    from app.models.extraction_run import ExtractionRun, RunStatus
    from app.services.email_extractor import aggregator

    async with SessionLocal() as db:
        scan = ExtractionRun(
            domain=domain,
            bd_id=bd_id,
            advisor_id=advisor_id,
            bank_id=bank_id,
            status=RunStatus.queued.value,
        )
        db.add(scan)
        await db.commit()
        await db.refresh(scan)
        scan_id = scan.id

    await aggregator.run(scan_id)

    async with SessionLocal() as db:
        scan = await db.get(ExtractionRun, scan_id)
        status = scan.status if scan is not None else "unknown"
        found = int(
            (
                await db.execute(
                    select(func.count(DiscoveredEmail.id)).where(
                        DiscoveredEmail.run_id == scan_id
                    )
                )
            ).scalar_one()
        )
    ok = status == RunStatus.completed.value
    return f"email: {status} ({found} found)", ok


# ───────────────────────────────────────────────────────────────────────────
# Broker-dealer sequence
# ───────────────────────────────────────────────────────────────────────────


async def _bd_refresh_on_cooldown(email: str, bd_id: int) -> bool:
    """True if this (user, BD) ran refresh-all within the 30s window.

    Shares the per-firm endpoint's budget: it stamps the same
    ``trigger_source`` (``manual_single:<email>``) and ``"bd_id": <id>``
    notes marker, so a detail-page click seconds earlier suppresses a
    redundant bulk refresh-all (and vice-versa).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_BD_REFRESH_COOLDOWN_SECONDS)
    marker = f'"bd_id": {bd_id}'
    async with SessionLocal() as db:
        stmt = (
            select(PipelineRun.id)
            .where(PipelineRun.pipeline_name == bd_orch.REFRESH_ALL_PIPELINE_NAME)
            .where(PipelineRun.trigger_source == f"manual_single:{email}")
            .where(
                or_(
                    PipelineRun.notes.ilike(f"%{marker},%"),
                    PipelineRun.notes.ilike(f"%{marker}" + "}%"),
                )
            )
            .where(PipelineRun.started_at >= cutoff)
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _bd_refresh_all_step(
    bd_id: int, user: AuthenticatedUser, bulk_run_id: int
) -> tuple[str, bool]:
    """Gap-gated refresh-all for a BD, honoring the 30s per-(user, BD) cooldown."""
    from app.models.broker_dealer import BrokerDealer

    if await _bd_refresh_on_cooldown(user.email, bd_id):
        return "refresh-all: cooldown, skipped", True

    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return "refresh-all: BD missing", False
        has_contacts = await bd_orch.has_executive_contacts(db, bd_id)
        decision = bd_orch.decide_pipelines(bd, has_contacts=has_contacts, scope="all")

    if not decision.to_run:
        return "refresh-all: already complete", True

    missing = bd_orch.required_provider_keys(decision.to_run)
    if missing:
        return f"refresh-all: providers missing ({', '.join(missing)})", False

    trigger = f"manual_single:{user.email}"
    child_id = await _create_child_run(
        pipeline_name=bd_orch.REFRESH_ALL_PIPELINE_NAME,
        trigger_source=trigger,
        notes={
            "bd_id": bd_id,
            "stage": "queued",
            "scope": "all",
            "ran": list(decision.to_run),
            "skipped": list(decision.to_skip),
        },
        parent_run_id=bulk_run_id,
    )
    await bd_orch.run_refresh_all(
        child_id,
        bd_id,
        trigger_source=trigger,
        pipelines_to_run=decision.to_run,
        pipelines_to_skip=decision.to_skip,
    )
    status = await _read_run_status(child_id)
    return f"refresh-all: {status}", _terminal_ok(status)


def _build_bd_officer_requests(bd) -> list:
    """FINRA owners + officers → deduped ``EnrichOfficerRequest`` seed list.

    Mirrors the FE "Generate More Details" seeding
    (broker-dealer-detail-client.tsx): merge ``direct_owners`` +
    ``executive_officers``, parse "LAST, FIRST" roster names into person
    entities (falling back to organization entities when there's no comma),
    dedupe, and cap at ``_MAX_BD_OFFICERS`` to bound provider spend.
    """
    from app.api.v1.endpoints.broker_dealers import EnrichOfficerRequest

    out: list = []
    seen: set[str] = set()
    for record in [*(bd.direct_owners or []), *(bd.executive_officers or [])]:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "").strip()
        if not name:
            continue
        title = str(record.get("title") or "").strip() or None
        split = adv_orch._split_officer_name(name) if "," in name else None
        if split is not None:
            first, last = split
            key = f"p|{first.lower()}|{last.lower()}"
            if key in seen:
                continue
            seen.add(key)
            out.append(
                EnrichOfficerRequest(
                    type="person", first_name=first, last_name=last, title=title
                )
            )
        else:
            key = f"o|{name.lower()}"
            if key in seen:
                continue
            seen.add(key)
            out.append(
                EnrichOfficerRequest(type="organization", org_name=name, title=title)
            )
        if len(out) >= _MAX_BD_OFFICERS:
            break
    return out


async def _bd_contacts_step(bd_id: int, user: AuthenticatedUser) -> tuple[str, bool]:
    """Officer fan-out via the existing ``/enrich`` handler (refresh=True).

    Reuses the endpoint function directly (in-process, not over HTTP) so the
    full Phase-1 company search + Phase-2 per-officer discovery + Phase-3 web
    fallback stay single-source-of-truth. The MASTER_LIST gate the handler
    normally enforces via ``Depends`` is checked by the caller before dispatch.
    """
    from fastapi import HTTPException

    from app.api.v1.endpoints.broker_dealers import (
        EnrichRequestBody,
        enrich_broker_dealer_contacts,
    )
    from app.models.broker_dealer import BrokerDealer

    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return "contacts: BD missing", False
        officers = _build_bd_officer_requests(bd)
        try:
            await enrich_broker_dealer_contacts(
                bd_id,
                EnrichRequestBody(officers=officers, refresh=True),
                user,
                db,
            )
        except HTTPException as exc:
            return f"contacts: {exc.detail}", False
    return "contacts: ok", True


async def _bd_focus_step(bd_id: int) -> tuple[str, bool]:
    """FOCUS "PERSON TO CONTACT" extraction (extract-focus-ceo)."""
    from app.models.broker_dealer import BrokerDealer
    from app.services.focus_ceo_extraction import FocusCeoExtractionService

    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return "focus: BD missing", False
        result = await FocusCeoExtractionService().extract(db, bd)
        await db.commit()
    status = result.extraction_status
    # A clean miss (no X-17A-5 PDF / low confidence) is not a hard failure.
    ok = status in ("success", "no_pdf", "low_confidence")
    return f"focus: {status}", ok


async def _bd_email_step(bd_id: int) -> tuple[str, bool]:
    """Email discovery on the BD's resolved website (fetched post-refresh)."""
    from app.models.broker_dealer import BrokerDealer

    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        website = bd.website if bd is not None else None
    return await _email_discovery_step(domain=_domain_from_url(website), bd_id=bd_id)


async def _enrich_broker_dealer(
    item: EnrichItem, user: AuthenticatedUser, bulk_run_id: int
) -> tuple[str, str]:
    from app.models.broker_dealer import BrokerDealer

    bd_id = item.entity_id
    outcome = _ItemOutcome()

    async with SessionLocal() as db:
        if await db.get(BrokerDealer, bd_id) is None:
            return "failed", "broker-dealer not found"

    await _run_step(outcome, "refresh-all", _bd_refresh_all_step(bd_id, user, bulk_run_id))
    await _run_step(outcome, "contacts", _bd_contacts_step(bd_id, user))
    await _run_step(outcome, "focus", _bd_focus_step(bd_id))
    if _feature_allowed(user, EMAIL_EXTRACTOR):
        await _run_step(outcome, "email", _bd_email_step(bd_id))
    else:
        outcome.note("email: email_extractor not granted, skipped")

    return outcome.status, outcome.reason


# ───────────────────────────────────────────────────────────────────────────
# Advisor sequence
# ───────────────────────────────────────────────────────────────────────────


async def _advisor_refresh_step(
    advisor_id: int, user: AuthenticatedUser, bulk_run_id: int
) -> tuple[str, bool]:
    """Gap-gated per-advisor refresh-all (owners/officers, website, filings,
    contacts, IAPD summary — only the sub-pipelines whose fields are empty)."""
    from app.models.investment_advisor import InvestmentAdvisor

    async with SessionLocal() as db:
        advisor = await db.get(InvestmentAdvisor, advisor_id)
        if advisor is None:
            return "refresh: advisor missing", False
        decision = adv_orch.decide_pipelines(advisor)

    if not decision.to_run:
        return "refresh: already complete", True

    missing = adv_orch.required_provider_keys(decision.to_run)
    if missing:
        return f"refresh: providers missing ({', '.join(missing)})", False

    trigger = f"manual_single:{user.email}"
    child_id = await _create_child_run(
        pipeline_name=adv_orch.REFRESH_ADVISOR_ALL_PIPELINE_NAME,
        trigger_source=trigger,
        notes={
            "advisor_id": advisor_id,
            "stage": "queued",
            "ran": list(decision.to_run),
            "skipped": list(decision.to_skip),
        },
        parent_run_id=bulk_run_id,
    )
    await adv_orch.run_advisor_refresh(
        child_id,
        advisor_id,
        trigger_source=trigger,
        pipelines_to_run=decision.to_run,
        pipelines_to_skip=decision.to_skip,
    )
    status = await _read_run_status(child_id)
    return f"refresh: {status}", _terminal_ok(status)


async def _advisor_contacts_step(
    advisor_id: int, user: AuthenticatedUser, bulk_run_id: int
) -> tuple[str, bool]:
    """Contact gap-fill: roster-seed discovery + re-query gap rows in place."""
    gap_trigger = f"manual_gap_fill:{user.email}"
    gap_id = await _create_child_run(
        pipeline_name=adv_orch.GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME,
        trigger_source=gap_trigger,
        notes={"advisor_id": advisor_id, "stage": "queued"},
        parent_run_id=bulk_run_id,
    )
    await adv_orch._run_gap_fill_contacts(gap_id, advisor_id, gap_trigger)
    status = await _read_run_status(gap_id)
    return f"contacts: {status}", _terminal_ok(status)


async def _advisor_email_step(advisor_id: int) -> tuple[str, bool]:
    from app.models.investment_advisor import InvestmentAdvisor

    async with SessionLocal() as db:
        advisor = await db.get(InvestmentAdvisor, advisor_id)
        website = advisor.website if advisor is not None else None
    return await _email_discovery_step(
        domain=adv_orch._domain_from_website(website), advisor_id=advisor_id
    )


async def _enrich_advisor(
    item: EnrichItem, user: AuthenticatedUser, bulk_run_id: int
) -> tuple[str, str]:
    from app.models.investment_advisor import InvestmentAdvisor

    advisor_id = item.entity_id
    outcome = _ItemOutcome()

    async with SessionLocal() as db:
        if await db.get(InvestmentAdvisor, advisor_id) is None:
            return "failed", "advisor not found"

    await _run_step(outcome, "refresh", _advisor_refresh_step(advisor_id, user, bulk_run_id))
    await _run_step(outcome, "contacts", _advisor_contacts_step(advisor_id, user, bulk_run_id))
    if _feature_allowed(user, EMAIL_EXTRACTOR):
        await _run_step(outcome, "email", _advisor_email_step(advisor_id))
    else:
        outcome.note("email: email_extractor not granted, skipped")

    return outcome.status, outcome.reason


# ───────────────────────────────────────────────────────────────────────────
# Bank sequence
# ───────────────────────────────────────────────────────────────────────────


async def _bank_contacts_step(
    bank_id: int, user: AuthenticatedUser, bulk_run_id: int
) -> tuple[str, bool]:
    """Contact discovery + gap-fill ("Generate More Details")."""
    gap_trigger = f"manual_gap_fill:{user.email}"
    gap_id = await _create_child_run(
        pipeline_name=bank_gap.GAP_FILL_BANK_CONTACTS_PIPELINE_NAME,
        trigger_source=gap_trigger,
        notes={"bank_id": bank_id, "stage": "queued"},
        parent_run_id=bulk_run_id,
    )
    await bank_gap.run_gap_fill_bank_contacts_background(gap_id, bank_id, gap_trigger)
    status = await _read_run_status(gap_id)
    return f"contacts: {status}", _terminal_ok(status)


async def _bank_email_step(bank_id: int) -> tuple[str, bool]:
    from app.models.bank import Bank
    from app.services.email_extractor.domain import bank_domain_from_website

    async with SessionLocal() as db:
        bank = await db.get(Bank, bank_id)
        website = bank.website if bank is not None else None
    return await _email_discovery_step(
        domain=bank_domain_from_website(website), bank_id=bank_id
    )


async def _enrich_bank(
    item: EnrichItem, user: AuthenticatedUser, bulk_run_id: int
) -> tuple[str, str]:
    from app.models.bank import Bank

    bank_id = item.entity_id
    outcome = _ItemOutcome()

    async with SessionLocal() as db:
        if await db.get(Bank, bank_id) is None:
            return "failed", "bank not found"

    await _run_step(outcome, "contacts", _bank_contacts_step(bank_id, user, bulk_run_id))
    if _feature_allowed(user, EMAIL_EXTRACTOR):
        await _run_step(outcome, "email", _bank_email_step(bank_id))
    else:
        outcome.note("email: email_extractor not granted, skipped")

    return outcome.status, outcome.reason


# ───────────────────────────────────────────────────────────────────────────
# Per-item dispatch
# ───────────────────────────────────────────────────────────────────────────


async def _dispatch_item(
    item: EnrichItem, *, user: AuthenticatedUser, bulk_run_id: int
) -> tuple[str, str]:
    """Route one item to its enrichment sequence. Returns (status, reason)
    where status is ``done`` | ``failed`` | ``skipped``."""
    entity_type = item.entity_type

    if entity_type == "broker_dealer":
        if not _feature_allowed(user, MASTER_LIST):
            return "skipped", "master_list access not granted"
        return await _enrich_broker_dealer(item, user, bulk_run_id)

    if entity_type == "advisor":
        if not _feature_allowed(user, INVESTMENT_ADVISORS):
            return "skipped", "investment_advisors access not granted"
        return await _enrich_advisor(item, user, bulk_run_id)

    if entity_type == "bank":
        if not _feature_allowed(user, BANKS):
            return "skipped", "banks access not granted"
        return await _enrich_bank(item, user, bulk_run_id)

    reason = _UNSUPPORTED_REASON.get(entity_type, f"unknown entity type: {entity_type}")
    return "skipped", reason


# ───────────────────────────────────────────────────────────────────────────
# Parent progress bookkeeping
# ───────────────────────────────────────────────────────────────────────────


def _progress_notes(
    *,
    list_id: int,
    total: int,
    current_index: int | None,
    current: EnrichItem | None,
    items: list[dict],
    stage: str,
    summary: str | None = None,
) -> dict:
    notes: dict = {
        "list_id": list_id,
        "stage": stage,
        "total": total,
        "current_index": current_index,
        "current_name": current.name if current is not None else None,
        "current_entity_type": current.entity_type if current is not None else None,
        # Heartbeat: stamped on every progress write so liveness is judged from
        # "last advanced" (see is_run_stale), never from started_at — a long
        # but healthy bulk run must keep reading as in-flight.
        "last_progress_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    if summary is not None:
        notes["summary"] = summary
    return notes


async def _write_parent(
    run_id: int,
    *,
    notes: dict,
    processed: int | None = None,
    success: int | None = None,
    failure: int | None = None,
    status: str | None = None,
    completed: bool = False,
) -> None:
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            logger.error("favorite-list enrich: parent run %d disappeared", run_id)
            return
        run.notes = json.dumps(notes)
        if processed is not None:
            run.processed_items = processed
        if success is not None:
            run.success_count = success
        if failure is not None:
            run.failure_count = failure
        if status is not None:
            run.status = status
        if completed:
            run.completed_at = datetime.now(timezone.utc)
        await db.commit()


# ───────────────────────────────────────────────────────────────────────────
# Lifetime heartbeat (liveness backstop, independent of per-firm cadence)
# ───────────────────────────────────────────────────────────────────────────


async def _touch_heartbeat(run_id: int) -> None:
    """Re-stamp ONLY ``notes.last_progress_at`` on the parent run, leaving every
    per-firm progress field (pointer, items, counters, summary) intact.

    Read-modify-write on the notes JSON: load current notes, patch just the
    heartbeat, write back — so the lifetime beat keeps liveness fresh without
    clobbering the pointer/items the per-firm writes own. Uses its own dedicated
    session (mirrors every other background DB write in this module), opened per
    beat and closed immediately. A vanished/deleted run is a no-op.
    """
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            return
        try:
            notes = json.loads(run.notes) if run.notes else {}
            if not isinstance(notes, dict):
                notes = {}
        except (json.JSONDecodeError, TypeError):
            notes = {}
        notes["last_progress_at"] = datetime.now(timezone.utc).isoformat()
        run.notes = json.dumps(notes)
        await db.commit()


async def _heartbeat_loop(run_id: int, lock: asyncio.Lock) -> None:
    """Re-stamp the parent run's heartbeat every ``_HEARTBEAT_INTERVAL_SECONDS``
    for the worker's whole lifetime — the required backstop so liveness never
    goes stale mid-firm (a single firm's chain, or a single leaf op like the
    email site scan, can itself exceed STALE_REFRESH_RUN_AGE; step-level touches
    alone can't cover that).

    Started when the worker starts and cancelled in the worker's ``finally`` (so
    a finished, failed, OR process-killed worker stops beating and the run then
    correctly goes stale for the reaper). Shares ``lock`` with the per-firm
    writes so this read-modify-write and their full-notes writes never clobber
    one another. Each beat's exceptions are contained — a transient DB error must
    never fail the run — while cancellation propagates out cleanly to stop it.
    """
    while True:
        # Sleep FIRST: the queue-time seed + the worker's initial progress write
        # already establish liveness, so a fast run never triggers a DB write
        # here (it is cancelled during this sleep). The backstop only matters
        # once a beat's worth of wall-clock has elapsed inside the run.
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        try:
            async with lock:
                await _touch_heartbeat(run_id)
        except Exception:  # noqa: BLE001 — the heartbeat must never kill the run
            logger.warning(
                "favorite-list enrich: lifetime heartbeat write failed (run=%s)",
                run_id,
                exc_info=True,
            )


# ───────────────────────────────────────────────────────────────────────────
# Worker entrypoint
# ───────────────────────────────────────────────────────────────────────────


async def run_favorite_list_enrich(
    parent_run_id: int,
    list_id: int,
    items: list[EnrichItem],
    *,
    user: AuthenticatedUser,
) -> None:
    """Drive the parent run through ``running → completed`` (or
    ``completed_with_errors`` / ``failed``), enriching each item sequentially
    and publishing a live "which firm" pointer into the parent's notes.

    A lifetime periodic heartbeat task (see :func:`_heartbeat_loop`) runs
    alongside the loop for the worker's whole life, re-stamping
    ``last_progress_at`` every ``_HEARTBEAT_INTERVAL_SECONDS`` so liveness stays
    fresh even while a single firm's chain — or a single leaf op like the email
    site scan — runs longer than ``STALE_REFRESH_RUN_AGE``. Without it the
    heartbeat would advance only BETWEEN firms and could go stale mid-firm,
    wrongly flipping the LIVE run to "orphaned" so a user re-click spawns a
    SECOND worker that re-bills every provider (and the startup reaper could fail
    the live run). It is cancelled in ``finally`` so a finished, failed, or
    process-killed worker stops beating and the run then correctly goes stale.
    """
    total = len(items)
    results: list[dict] = []

    # One event loop, two writers of ``notes``: the per-firm full-notes writes
    # below and the lifetime heartbeat's read-modify-write. Serialize them with a
    # lock so neither clobbers the other — the heartbeat only ever patches
    # ``last_progress_at``; the per-firm writes own the pointer + items + counters.
    notes_lock = asyncio.Lock()

    async def _publish(**kwargs) -> None:
        async with notes_lock:
            await _write_parent(parent_run_id, **kwargs)

    # Start the lifetime heartbeat and guarantee it is torn down in ``finally``
    # (never leaked, even if the body raises). It shares ``notes_lock`` and holds
    # it only for its brief read-modify-write — the long per-firm dispatch runs
    # OUTSIDE any lock, so a beat can land mid-firm and keep liveness fresh.
    heartbeat_task = asyncio.create_task(_heartbeat_loop(parent_run_id, notes_lock))
    try:
        await _publish(
            notes=_progress_notes(
                list_id=list_id,
                total=total,
                current_index=0 if total else None,
                current=items[0] if items else None,
                items=results,
                stage="running",
            ),
            status="running",
            processed=0,
            success=0,
            failure=0,
        )

        done = skipped = failed = 0
        for index, item in enumerate(items):
            # Publish the pointer BEFORE processing so a poll mid-item shows the
            # firm currently in flight.
            await _publish(
                notes=_progress_notes(
                    list_id=list_id,
                    total=total,
                    current_index=index,
                    current=item,
                    items=results,
                    stage="running",
                ),
            )

            try:
                status, reason = await _dispatch_item(
                    item, user=user, bulk_run_id=parent_run_id
                )
            except Exception as exc:  # noqa: BLE001 — one firm must never abort the run
                logger.exception(
                    "favorite-list enrich: item failed (list=%s item=%s type=%s)",
                    list_id,
                    item.item_id,
                    item.entity_type,
                )
                status, reason = "failed", f"{type(exc).__name__}: {str(exc)[:180]}"

            results.append(
                {
                    "id": item.item_id,
                    "type": item.entity_type,
                    "name": item.name,
                    "status": status,
                    "reason": reason,
                }
            )
            if status == "done":
                done += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1

            await _publish(
                notes=_progress_notes(
                    list_id=list_id,
                    total=total,
                    current_index=index,
                    current=item,
                    items=results,
                    stage="running",
                ),
                processed=index + 1,
                success=done,
                failure=failed,
            )

        if failed == 0:
            parent_status = "completed"
        elif done == 0 and skipped == 0:
            parent_status = "failed"
        else:
            parent_status = "completed_with_errors"

        summary = _build_summary(done, skipped, failed, results)
        await _publish(
            notes=_progress_notes(
                list_id=list_id,
                total=total,
                current_index=None,
                current=None,
                items=results,
                stage="done",
                summary=summary,
            ),
            status=parent_status,
            processed=total,
            success=done,
            failure=failed,
            completed=True,
        )
    finally:
        # Stop the heartbeat once the run is terminal (or the body raised): a
        # dead worker MUST let the run go stale so the reaper can reclaim it.
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


def _build_summary(
    done: int, skipped: int, failed: int, results: list[dict]
) -> str:
    """'X enriched, Y skipped, Z failed' plus a compact tail of the firms that
    didn't fully enrich, capped so the notes column stays small."""
    head = f"{done} enriched, {skipped} skipped, {failed} failed."
    troubled = [
        f"{r['name']} ({r['status']})"
        for r in results
        if r["status"] in ("skipped", "failed")
    ]
    if not troubled:
        return head
    tail = "; ".join(troubled[:10])
    if len(troubled) > 10:
        tail += f"; +{len(troubled) - 10} more"
    return f"{head} {tail}"[:480]


async def run_favorite_list_enrich_background(
    parent_run_id: int,
    list_id: int,
    items: list[EnrichItem],
    *,
    user: AuthenticatedUser,
) -> None:
    """Defensive wrapper so an unhandled exception in the BackgroundTask never
    leaves the parent PipelineRun stuck on ``queued``."""
    try:
        await run_favorite_list_enrich(parent_run_id, list_id, items, user=user)
    except Exception:
        logger.exception(
            "favorite-list enrich background task failed (parent_run_id=%s list_id=%s)",
            parent_run_id,
            list_id,
        )
        # Best-effort: flip the parent to failed so the FE stops polling.
        try:
            async with SessionLocal() as db:
                run = await db.get(PipelineRun, parent_run_id)
                if run is not None and run.status in ("queued", "running"):
                    run.status = "failed"
                    run.completed_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception:
            logger.exception(
                "favorite-list enrich: failed to mark parent %d failed", parent_run_id
            )


__all__ = [
    "EnrichItem",
    "FAVORITE_LIST_ENRICH_PIPELINE_NAME",
    "run_favorite_list_enrich",
    "run_favorite_list_enrich_background",
]
