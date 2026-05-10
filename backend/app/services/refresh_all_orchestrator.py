"""Per-firm refresh-all orchestrator.

Backs ``POST /broker-dealers/{id}/refresh-all`` — fires the subset of
the four existing per-firm pipelines whose target fields are still
NULL (or, for ``enrich``, whose target table is empty for this firm).
The endpoint creates a parent ``PipelineRun`` and a FastAPI
BackgroundTask invokes :func:`run_refresh_all` to drive the children
in parallel via :func:`asyncio.gather`.

Sub-pipelines and their gate predicates:

- ``refresh-financials``  — runs when ``latest_net_capital``,
  ``yoy_growth``, OR ``health_status`` is NULL on the BD row.
  Cost: ~2 Gemini calls. Reuses
  :meth:`FocusReportService.load_financial_metrics_for_broker_dealer`.
- ``resolve-website``     — runs when ``website`` is NULL.
  Cost: 1 Apollo call + cascading Hunter/SerpAPI fallback. Reuses
  :func:`resolve_website` directly (NOT the HTTP handler, which is
  admin-gated; the orchestrator-driven path is open to any
  authenticated user per the plan).
- ``health-check``        — runs when ``current_clearing_type`` OR
  ``current_clearing_partner`` is NULL. Free (FINRA only).
- ``enrich``              — runs when no ``executive_contacts`` rows
  exist for this BD. Cost: ~2 Apollo + ~1 Hunter via the company-only
  search (no per-officer fan-out — that has its own dedicated FE
  button and stays separate).

Each child gets its own ``PipelineRun`` row with ``parent_run_id``
pointing at the orchestrator's parent row. The parent's terminal
``notes.summary`` is a short human-readable string the FE surfaces
in a toast verbatim.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.models.pipeline_run import PipelineRun
from app.services.apollo import ApolloClient
from app.services.contacts import (
    ContactEnrichmentUnavailableError,
    ExecutiveContactService,
)
from app.services.finra import FinraService
from app.services.focus_reports import FocusReportService
from app.services.hunter import HunterClient
from app.services.serpapi import SerpAPIClient
from app.services.service_models import FinraBrokerDealerRecord
from app.services.website_resolver import resolve_website

logger = logging.getLogger(__name__)


REFRESH_ALL_PIPELINE_NAME = "broker_dealer_refresh_all"

# Sub-pipeline names — these match the child rows' ``pipeline_name`` column
# values. ``refresh-financials`` keeps the legacy single-firm name so the
# 409 guard on the standalone endpoint and the new orchestrator both query
# the same row by name.
SUB_REFRESH_FINANCIALS = "financial_pdf_pipeline_single"
SUB_RESOLVE_WEBSITE = "broker_dealer_resolve_website"
SUB_HEALTH_CHECK = "broker_dealer_health_check"
SUB_ENRICH = "broker_dealer_enrich_contacts"

# Display labels used in the parent's notes.summary toast string.
_SUB_LABEL = {
    SUB_REFRESH_FINANCIALS: "financials",
    SUB_RESOLVE_WEBSITE: "website",
    SUB_HEALTH_CHECK: "clearing",
    SUB_ENRICH: "contacts",
}


@dataclass(frozen=True)
class GateDecision:
    """Which sub-pipelines the orchestrator should fire and which to skip."""

    to_run: tuple[str, ...]
    to_skip: tuple[str, ...]


def decide_pipelines(broker_dealer: BrokerDealer, has_contacts: bool) -> GateDecision:
    """Inspect the BD and return the (run, skip) split.

    The caller queries ``has_contacts`` separately because the BD row
    doesn't carry an ``executive_contacts`` count column — we count the
    relationship explicitly instead of joining, to avoid the cost of
    fetching every row when all we need is "any?".
    """
    to_run: list[str] = []
    to_skip: list[str] = []

    needs_financials = (
        broker_dealer.latest_net_capital is None
        or broker_dealer.yoy_growth is None
        or broker_dealer.health_status is None
    )
    (to_run if needs_financials else to_skip).append(SUB_REFRESH_FINANCIALS)

    needs_website = not broker_dealer.website
    (to_run if needs_website else to_skip).append(SUB_RESOLVE_WEBSITE)

    needs_health = (
        broker_dealer.current_clearing_type is None
        or broker_dealer.current_clearing_partner is None
    )
    (to_run if needs_health else to_skip).append(SUB_HEALTH_CHECK)

    needs_contacts = not has_contacts
    (to_run if needs_contacts else to_skip).append(SUB_ENRICH)

    return GateDecision(to_run=tuple(to_run), to_skip=tuple(to_skip))


def required_provider_keys(pipelines: Iterable[str]) -> list[str]:
    """Return missing provider-key labels for the pipelines we're about to
    fire. Used by the endpoint to decide whether to refuse with 503 before
    queuing a parent run that can't complete."""
    pipelines = set(pipelines)
    missing: list[str] = []

    if SUB_REFRESH_FINANCIALS in pipelines:
        if settings.llm_provider == "openai":
            if not settings.openai_api_key:
                missing.append("OpenAI")
        elif not settings.gemini_api_key:
            missing.append("Gemini")

    if SUB_RESOLVE_WEBSITE in pipelines:
        # The chain runs Apollo → Hunter → SerpAPI; if all three are missing
        # the chain has no way to land a candidate. One of the three is
        # enough to proceed (the existing endpoint allows missing fallbacks).
        if not (
            settings.apollo_api_key
            or settings.hunter_api_key
            or settings.serpapi_api_key
        ):
            missing.append("Apollo/Hunter/SerpAPI (none configured)")

    if SUB_ENRICH in pipelines and not settings.apollo_api_key:
        missing.append("Apollo (required for contact enrichment)")

    return missing


async def has_executive_contacts(db: AsyncSession, bd_id: int) -> bool:
    """Cheap "any row?" check against ``executive_contacts``.

    Using ``select(1).limit(1)`` so Postgres short-circuits on first hit
    instead of counting all rows.
    """
    stmt = select(ExecutiveContact.id).where(ExecutiveContact.bd_id == bd_id).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _create_child_run(
    db: AsyncSession,
    *,
    pipeline_name: str,
    parent_run_id: int,
    bd_id: int,
    trigger_source: str,
) -> int:
    """Persist a queued child row and return its id."""
    child = PipelineRun(
        pipeline_name=pipeline_name,
        trigger_source=trigger_source,
        status="queued",
        total_items=1,
        processed_items=0,
        success_count=0,
        failure_count=0,
        notes=json.dumps({"bd_id": bd_id, "stage": "queued"}),
        parent_run_id=parent_run_id,
    )
    db.add(child)
    await db.commit()
    await db.refresh(child)
    return child.id


async def _finalize_child(
    run_id: int,
    *,
    status: str,
    success: int,
    failure: int,
    summary: str,
) -> None:
    """Mark a child terminal in its own session/transaction."""
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            logger.error("refresh-all: child run %d disappeared mid-flight", run_id)
            return
        run.status = status
        run.processed_items = 1
        run.success_count = success
        run.failure_count = failure
        run.completed_at = datetime.now(timezone.utc)
        run.notes = json.dumps({"summary": summary[:500]})
        await db.commit()


async def _run_resolve_website(parent_run_id: int, bd_id: int, trigger_source: str) -> tuple[str, str]:
    """Returns (status, summary) — status is one of ``completed``,
    ``completed_with_errors``, ``failed``."""
    async with SessionLocal() as db:
        child_id = await _create_child_run(
            db,
            pipeline_name=SUB_RESOLVE_WEBSITE,
            parent_run_id=parent_run_id,
            bd_id=bd_id,
            trigger_source=trigger_source,
        )

    try:
        async with SessionLocal() as run_db:
            run = await run_db.get(PipelineRun, child_id)
            if run is not None:
                run.status = "running"
                await run_db.commit()

        async with SessionLocal() as db:
            broker_dealer = await db.get(BrokerDealer, bd_id)
            if broker_dealer is None:
                raise RuntimeError(f"Broker-dealer {bd_id} not found mid-flight.")
            if broker_dealer.website:
                summary = f"Website already set ({broker_dealer.website_source or 'unknown source'})."
                await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
                return "completed", summary

            apollo = ApolloClient(settings.apollo_api_key) if settings.apollo_api_key else None
            hunter = HunterClient(settings.hunter_api_key) if settings.hunter_api_key else None
            serpapi = SerpAPIClient(settings.serpapi_api_key) if settings.serpapi_api_key else None

            if apollo is None and hunter is None and serpapi is None:
                summary = "No website-resolver provider keys configured."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary

            website, source, reason = await resolve_website(
                broker_dealer.name,
                broker_dealer.crd_number,
                apollo,
                hunter,
                serpapi,
            )

            if website and source:
                stmt = (
                    update(BrokerDealer)
                    .where(BrokerDealer.id == bd_id)
                    .where(BrokerDealer.website.is_(None))
                    .values(website=website, website_source=source)
                )
                await db.execute(stmt)
                await db.commit()
                summary = f"Resolved via {source}: {website}"
                await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
                return "completed", summary

            summary = f"No website resolved ({reason})." if reason else "No website resolved."
            # Treat clean miss as completed_with_errors so the parent toast
            # reflects "we tried, found nothing" rather than a hard failure.
            await _finalize_child(
                child_id, status="completed_with_errors", success=0, failure=1, summary=summary
            )
            return "completed_with_errors", summary

    except Exception as exc:
        logger.exception("refresh-all/resolve-website failed for bd %s", bd_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


async def _run_health_check(parent_run_id: int, bd_id: int, trigger_source: str) -> tuple[str, str]:
    async with SessionLocal() as db:
        child_id = await _create_child_run(
            db,
            pipeline_name=SUB_HEALTH_CHECK,
            parent_run_id=parent_run_id,
            bd_id=bd_id,
            trigger_source=trigger_source,
        )

    try:
        async with SessionLocal() as run_db:
            run = await run_db.get(PipelineRun, child_id)
            if run is not None:
                run.status = "running"
                await run_db.commit()

        from app.services.classification import (
            classify_niche_restricted,
            determine_clearing_classification,
        )

        finra_service = FinraService()
        async with SessionLocal() as db:
            broker_dealer = await db.get(BrokerDealer, bd_id)
            if broker_dealer is None:
                raise RuntimeError(f"Broker-dealer {bd_id} not found mid-flight.")

            changes: list[str] = []
            if broker_dealer.crd_number:
                record = FinraBrokerDealerRecord(
                    crd_number=broker_dealer.crd_number,
                    name=broker_dealer.name,
                    sec_file_number=broker_dealer.sec_file_number,
                    registration_status=broker_dealer.status,
                    branch_count=broker_dealer.branch_count,
                    address_city=broker_dealer.city,
                    address_state=broker_dealer.state,
                    business_type=broker_dealer.business_type,
                )
                enriched = await finra_service.enrich_with_detail([record])
                if enriched:
                    enriched_record = enriched[0]
                    if enriched_record.types_of_business and enriched_record.types_of_business != broker_dealer.types_of_business:
                        broker_dealer.types_of_business = enriched_record.types_of_business
                        changes.append("types_of_business")
                    if enriched_record.direct_owners and enriched_record.direct_owners != broker_dealer.direct_owners:
                        broker_dealer.direct_owners = enriched_record.direct_owners
                        changes.append("direct_owners")
                    if enriched_record.executive_officers and enriched_record.executive_officers != broker_dealer.executive_officers:
                        broker_dealer.executive_officers = enriched_record.executive_officers
                        changes.append("executive_officers")
                    if enriched_record.firm_operations_text and enriched_record.firm_operations_text != broker_dealer.firm_operations_text:
                        broker_dealer.firm_operations_text = enriched_record.firm_operations_text
                        changes.append("firm_operations_text")
                    if enriched_record.website and enriched_record.website != broker_dealer.website:
                        broker_dealer.website = enriched_record.website
                        changes.append("website")

            new_classification = determine_clearing_classification(broker_dealer.firm_operations_text)
            if broker_dealer.clearing_classification != new_classification:
                broker_dealer.clearing_classification = new_classification
                changes.append("clearing_classification")

            new_niche = classify_niche_restricted(broker_dealer.types_of_business)
            if broker_dealer.is_niche_restricted != new_niche:
                broker_dealer.is_niche_restricted = new_niche
                changes.append("is_niche_restricted")

            await db.commit()

        summary = (
            f"Refreshed {len(changes)} field(s): {', '.join(changes)}." if changes else "No FINRA changes detected."
        )
        await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
        return "completed", summary

    except Exception as exc:
        logger.exception("refresh-all/health-check failed for bd %s", bd_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


async def _run_enrich(parent_run_id: int, bd_id: int, trigger_source: str) -> tuple[str, str]:
    async with SessionLocal() as db:
        child_id = await _create_child_run(
            db,
            pipeline_name=SUB_ENRICH,
            parent_run_id=parent_run_id,
            bd_id=bd_id,
            trigger_source=trigger_source,
        )

    try:
        async with SessionLocal() as run_db:
            run = await run_db.get(PipelineRun, child_id)
            if run is not None:
                run.status = "running"
                await run_db.commit()

        contact_service = ExecutiveContactService()
        async with SessionLocal() as db:
            broker_dealer = await db.get(BrokerDealer, bd_id)
            if broker_dealer is None:
                raise RuntimeError(f"Broker-dealer {bd_id} not found mid-flight.")
            try:
                contacts = await contact_service.enrich_contacts(db, broker_dealer)
            except ContactEnrichmentUnavailableError as exc:
                summary = f"Enrichment unavailable: {exc}"
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary

        summary = f"Discovered {len(contacts)} contact(s)." if contacts else "No new contacts found."
        # Empty result is "completed" not "failed" — Apollo just didn't have anyone.
        # Cooldown stamping inside enrich_contacts already prevents re-run thrash.
        await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
        return "completed", summary

    except Exception as exc:
        logger.exception("refresh-all/enrich failed for bd %s", bd_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


async def _run_refresh_financials(parent_run_id: int, bd_id: int, trigger_source: str) -> tuple[str, str]:
    """Wrap the existing single-firm financials service so its self-managed
    PipelineRun row becomes a child of the orchestrator's parent."""
    async with SessionLocal() as db:
        child_id = await _create_child_run(
            db,
            pipeline_name=SUB_REFRESH_FINANCIALS,
            parent_run_id=parent_run_id,
            bd_id=bd_id,
            trigger_source=trigger_source,
        )

    service = FocusReportService()
    try:
        await service.load_financial_metrics_for_broker_dealer(
            bd_id,
            trigger_source=trigger_source,
            pipeline_run_id=child_id,
        )
    except Exception as exc:
        # The service already calls _mark_pipeline_run_failed on the child;
        # we just need to surface the message to the parent's aggregation.
        logger.exception("refresh-all/refresh-financials failed for bd %s", bd_id)
        return "failed", f"{type(exc).__name__}: {str(exc)[:200]}"

    # Reload the child to get the terminal status the service stamped on it
    # (completed, completed_with_errors, or failed) plus the summary line.
    async with SessionLocal() as db:
        child = await db.get(PipelineRun, child_id)
        if child is None:
            return "failed", "Child run row disappeared after extraction."
        try:
            payload = json.loads(child.notes or "{}")
            summary = payload.get("summary") or "Financials extraction complete."
        except (TypeError, ValueError):
            summary = "Financials extraction complete."
        return child.status, summary[:500]


_RUNNERS = {
    SUB_REFRESH_FINANCIALS: _run_refresh_financials,
    SUB_RESOLVE_WEBSITE: _run_resolve_website,
    SUB_HEALTH_CHECK: _run_health_check,
    SUB_ENRICH: _run_enrich,
}


async def run_refresh_all(
    parent_run_id: int,
    bd_id: int,
    *,
    trigger_source: str,
    pipelines_to_run: tuple[str, ...],
    pipelines_to_skip: tuple[str, ...],
) -> None:
    """Drive the parent run through ``running → completed`` (or
    ``completed_with_errors`` / ``failed``) by firing each child pipeline
    in parallel via ``asyncio.gather`` and aggregating their terminal
    states into the parent's notes."""

    if not pipelines_to_run:
        async with SessionLocal() as db:
            run = await db.get(PipelineRun, parent_run_id)
            if run is not None:
                run.status = "skipped"
                run.completed_at = datetime.now(timezone.utc)
                run.notes = json.dumps(
                    {"summary": "Already complete.", "ran": [], "skipped": list(pipelines_to_skip)}
                )
                await db.commit()
        return

    async with SessionLocal() as db:
        parent = await db.get(PipelineRun, parent_run_id)
        if parent is None:
            logger.error("refresh-all: parent run %d disappeared before start", parent_run_id)
            return
        parent.status = "running"
        parent.total_items = len(pipelines_to_run)
        parent.notes = json.dumps(
            {
                "bd_id": bd_id,
                "stage": "running",
                "ran": list(pipelines_to_run),
                "skipped": list(pipelines_to_skip),
            }
        )
        await db.commit()

    coros = [_RUNNERS[name](parent_run_id, bd_id, trigger_source) for name in pipelines_to_run]
    results = await asyncio.gather(*coros, return_exceptions=True)

    children_summary: dict[str, dict[str, object]] = {}
    success = 0
    failure = 0
    label_ran: list[str] = []
    label_failed: list[str] = []

    for name, result in zip(pipelines_to_run, results):
        if isinstance(result, BaseException):
            child_status = "failed"
            child_summary = f"{type(result).__name__}: {str(result)[:200]}"
        else:
            child_status, child_summary = result

        children_summary[name] = {"status": child_status, "summary": child_summary}
        label = _SUB_LABEL.get(name, name)

        if child_status in ("completed", "completed_with_errors"):
            success += 1
            label_ran.append(label)
        else:
            failure += 1
            label_failed.append(label)

    if failure == 0:
        parent_status = "completed"
    elif success == 0:
        parent_status = "failed"
    else:
        parent_status = "completed_with_errors"

    summary_parts: list[str] = []
    if label_ran:
        summary_parts.append(f"Refreshed: {', '.join(label_ran)}")
    if label_failed:
        summary_parts.append(f"Failed: {', '.join(label_failed)}")
    if pipelines_to_skip:
        skipped_labels = [_SUB_LABEL.get(name, name) for name in pipelines_to_skip]
        summary_parts.append(f"Skipped: {', '.join(skipped_labels)}")
    summary = ". ".join(summary_parts) + "." if summary_parts else "No-op."
    summary = summary[:180]

    async with SessionLocal() as db:
        parent = await db.get(PipelineRun, parent_run_id)
        if parent is None:
            return
        parent.status = parent_status
        parent.processed_items = success + failure
        parent.success_count = success
        parent.failure_count = failure
        parent.completed_at = datetime.now(timezone.utc)
        parent.notes = json.dumps(
            {
                "summary": summary,
                "ran": list(pipelines_to_run),
                "skipped": list(pipelines_to_skip),
                "children": children_summary,
            }
        )
        await db.commit()


__all__ = [
    "GateDecision",
    "REFRESH_ALL_PIPELINE_NAME",
    "SUB_ENRICH",
    "SUB_HEALTH_CHECK",
    "SUB_REFRESH_FINANCIALS",
    "SUB_RESOLVE_WEBSITE",
    "decide_pipelines",
    "has_executive_contacts",
    "required_provider_keys",
    "run_refresh_all",
]
