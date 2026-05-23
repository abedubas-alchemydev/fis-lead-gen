"""Per-advisor refresh-all orchestrator (IA analog of
``refresh_all_orchestrator.py``).

Backs ``POST /investment-advisors/{id}/refresh-all`` -- fires the subset
of per-advisor sub-pipelines whose target fields are still NULL on the
``InvestmentAdvisor`` row. The endpoint creates a parent ``PipelineRun``
and a FastAPI BackgroundTask invokes :func:`run_advisor_refresh` to
drive the children in parallel via :func:`asyncio.gather`.

Sub-pipelines in this initial scope:

- ``refresh_owners_officers`` -- runs when ``executive_officers`` is NULL
  on the IA row. Fetches the FINRA BrokerCheck PDF for the advisor's CRD
  and reuses ``brokercheck_pdf.fetch_form_bd_detail`` / ``_parse_form_bd_pdf``
  to extract the Direct Owners and Executive Officers section. SEC's
  Form ADV uses the same section header as Form BD per regulator
  alignment, so the parser anchors match for dual-registered firms and
  IA-only firms whose BrokerCheck PDF carries the section.
- ``resolve_advisor_website`` -- runs when ``website`` is NULL. Reuses
  ``services.website_resolver.resolve_website`` directly. Writes
  ``website`` + ``website_source`` to the IA row.

Each sub-pipeline writes its own child ``PipelineRun`` row with
``parent_run_id`` pointing at the orchestrator's parent row. Failure
modes (no PDF on file, parser returned nothing, website resolver
exhausted its fallbacks) are absorbed into the child's terminal
status string and never abort the parent run.

Mirrors the request/response contract of ``refresh_all_orchestrator``
exactly so the FE detail client at
``frontend/components/advisor-list/advisor-detail-client.tsx`` can
reuse the same polling + 409-conflict + 429-cooldown handling as the
BD detail client at
``frontend/components/master-list/broker-dealer-detail-client.tsx``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.investment_advisor import InvestmentAdvisor
from app.models.pipeline_run import PipelineRun
from app.services.apollo import ApolloClient
from app.services.brokercheck_pdf import (
    FinraPdfFetchError,
    FinraPdfNotFound,
    fetch_form_bd_detail,
)
from app.services.serpapi import SerpAPIClient
from app.services.serper import SerperClient
from app.services.website_resolver import resolve_website


RefreshScope = Literal["all"]

logger = logging.getLogger(__name__)


# Parent / child pipeline_name values. Distinct namespace from BD so the
# 409 "already in flight" check and audit trail stay clean.
REFRESH_ADVISOR_ALL_PIPELINE_NAME = "investment_advisor_refresh_all"
SUB_REFRESH_OWNERS_OFFICERS = "investment_advisor_refresh_owners_officers"
SUB_RESOLVE_ADVISOR_WEBSITE = "investment_advisor_resolve_website"

# Short human labels used in the parent's notes.summary toast string.
_SUB_LABEL: dict[str, str] = {
    SUB_REFRESH_OWNERS_OFFICERS: "owners",
    SUB_RESOLVE_ADVISOR_WEBSITE: "website",
}


@dataclass(frozen=True)
class GateDecision:
    """Which sub-pipelines the orchestrator should fire and which to skip."""

    to_run: tuple[str, ...]
    to_skip: tuple[str, ...]


def decide_pipelines(advisor: InvestmentAdvisor) -> GateDecision:
    """Inspect an ``InvestmentAdvisor`` row and decide which sub-pipelines
    have an open gate (their target column is NULL/empty) and which are
    already filled. Same per-firm gate predicate model as
    ``refresh_all_orchestrator.decide_pipelines``."""
    to_run: list[str] = []
    to_skip: list[str] = []

    needs_owners = not advisor.executive_officers
    if needs_owners:
        to_run.append(SUB_REFRESH_OWNERS_OFFICERS)
    else:
        to_skip.append(SUB_REFRESH_OWNERS_OFFICERS)

    needs_website = not advisor.website
    if needs_website:
        to_run.append(SUB_RESOLVE_ADVISOR_WEBSITE)
    else:
        to_skip.append(SUB_RESOLVE_ADVISOR_WEBSITE)

    return GateDecision(to_run=tuple(to_run), to_skip=tuple(to_skip))


def required_provider_keys(pipelines: Iterable[str]) -> list[str]:
    """Return the list of provider-key names the orchestrator needs but
    aren't configured. The endpoint surfaces this as 503 so the FE can
    show a sensible error instead of letting the BG task fail mid-run.

    ``resolve_advisor_website`` uses the same provider cascade as the BD
    website resolver (Apollo + Hunter/SerpAPI fallback). The owners/officers
    pipeline only hits FINRA's public BrokerCheck endpoint, which needs no
    key beyond the same SEC-compliant User-Agent the rest of the app already
    sets via ``settings.sec_user_agent``.
    """
    missing: list[str] = []
    if SUB_RESOLVE_ADVISOR_WEBSITE in pipelines:
        if not settings.apollo_api_key:
            missing.append("APOLLO_API_KEY")
        # Hunter / SerpAPI keys are soft fallbacks -- the resolver degrades
        # gracefully if they're absent, so we don't gate on them.
    return missing


# ---------------------------------------------------------------------------
# Child PipelineRun bookkeeping
# ---------------------------------------------------------------------------

async def _create_child_run(
    parent_run_id: int,
    advisor_id: int,
    pipeline_name: str,
    trigger_source: str,
) -> int:
    """Persist a child PipelineRun and return its id. Each sub-pipeline
    writes its own child so the audit trail mirrors BD's structure."""
    async with SessionLocal() as db:
        run = PipelineRun(
            pipeline_name=pipeline_name,
            trigger_source=trigger_source,
            status="running",
            total_items=1,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes=json.dumps({"advisor_id": advisor_id, "parent_run_id": parent_run_id}),
            parent_run_id=parent_run_id,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return run.id


async def _finalize_child(
    child_id: int,
    *,
    status: str,
    success: int,
    failure: int,
    summary: str,
) -> None:
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, child_id)
        if run is None:
            return
        run.status = status
        run.processed_items = success + failure
        run.success_count = success
        run.failure_count = failure
        run.completed_at = datetime.now(timezone.utc)
        existing_notes = {}
        if run.notes:
            try:
                existing_notes = json.loads(run.notes)
            except json.JSONDecodeError:
                existing_notes = {}
        existing_notes["summary"] = summary
        run.notes = json.dumps(existing_notes)
        await db.commit()


# ---------------------------------------------------------------------------
# Sub-pipeline runners
# ---------------------------------------------------------------------------

async def _run_refresh_owners_officers(
    parent_run_id: int, advisor_id: int, trigger_source: str
) -> tuple[str, str]:
    """Fetch the BrokerCheck PDF for this advisor's CRD and write the
    extracted executive_officers list to the IA row. The "Direct Owners
    and Executive Officers" section header is universal between Form BD
    and Form ADV so the existing parser handles both."""
    child_id = await _create_child_run(
        parent_run_id, advisor_id, SUB_REFRESH_OWNERS_OFFICERS, trigger_source
    )
    try:
        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between queue and run."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary
            crd = advisor.crd_number

        if not crd:
            summary = "No CRD on record; cannot fetch BrokerCheck PDF."
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        try:
            detail = await fetch_form_bd_detail(crd)
        except FinraPdfNotFound:
            # IA-only firms whose data lives at IAPD (not BrokerCheck) hit
            # this path. Documented as a follow-up in the plan.
            summary = "No BrokerCheck PDF on file for this CRD."
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary
        except FinraPdfFetchError as exc:
            summary = f"FINRA fetch failed: {str(exc)[:160]}"
            await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
            return "failed", summary

        if detail is None or not detail.executive_officers:
            summary = "BrokerCheck PDF parsed but contained no Officers/Owners section."
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between extract and write."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary
            advisor.executive_officers = detail.executive_officers
            # The parser returns a combined officers/owners list. For v1 we
            # populate executive_officers only; splitting into direct_owners
            # is tracked as a follow-up. The combined list still surfaces
            # owners (typically those with non-zero ownership_pct) on the
            # detail page's Executive Officers panel.
            await db.commit()

        summary = f"Wrote {len(detail.executive_officers)} officer/owner record(s)."
        await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
        return "completed", summary
    except Exception as exc:
        logger.exception("advisor-refresh/owners-officers failed for advisor %s", advisor_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


async def _run_resolve_advisor_website(
    parent_run_id: int, advisor_id: int, trigger_source: str
) -> tuple[str, str]:
    """Run the existing Apollo/Hunter waterfall to find a website for this
    advisor. Mirrors the BD sub-pipeline's behaviour: the resolver writes
    nothing on its own; we apply the returned URL to the IA row only on a
    confident hit."""
    child_id = await _create_child_run(
        parent_run_id, advisor_id, SUB_RESOLVE_ADVISOR_WEBSITE, trigger_source
    )
    try:
        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between queue and run."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary
            firm_name = advisor.name
            crd = advisor.crd_number

        if not firm_name:
            summary = "Advisor has no firm_name; cannot search providers."
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        # ApolloClient is required for the first tier; the SerpAPI/Serper
        # tiers are optional fallbacks the resolver skips when their keys
        # aren't set. ``required_provider_keys`` above already 503s when
        # APOLLO_API_KEY is missing so we won't hit this path without it.
        apollo = ApolloClient() if settings.apollo_api_key else None
        serpapi = SerpAPIClient() if getattr(settings, "serpapi_api_key", None) else None
        serper = SerperClient() if getattr(settings, "serper_api_key", None) else None

        website, source, reason = await resolve_website(
            firm_name=firm_name,
            crd=crd,
            apollo=apollo,
            serpapi=serpapi,
            serper=serper,
        )

        if not website:
            summary = f"Provider waterfall returned no website ({reason or 'unknown'})."
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between resolve and write."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary
            advisor.website = website
            advisor.website_source = source
            await db.commit()

        summary = f"Wrote website from {source}."
        await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
        return "completed", summary
    except Exception as exc:
        logger.exception("advisor-refresh/website failed for advisor %s", advisor_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


_RUNNERS = {
    SUB_REFRESH_OWNERS_OFFICERS: _run_refresh_owners_officers,
    SUB_RESOLVE_ADVISOR_WEBSITE: _run_resolve_advisor_website,
}


# ---------------------------------------------------------------------------
# Parent orchestrator
# ---------------------------------------------------------------------------

async def run_advisor_refresh(
    parent_run_id: int,
    advisor_id: int,
    *,
    trigger_source: str,
    pipelines_to_run: tuple[str, ...],
    pipelines_to_skip: tuple[str, ...],
) -> None:
    """Drive the parent run through ``running -> completed`` (or
    ``completed_with_errors`` / ``failed``) by firing each child
    pipeline in parallel via ``asyncio.gather`` and aggregating the
    terminal states into the parent's notes."""

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
            logger.error("advisor-refresh: parent run %d disappeared before start", parent_run_id)
            return
        parent.status = "running"
        parent.total_items = len(pipelines_to_run)
        parent.notes = json.dumps(
            {
                "advisor_id": advisor_id,
                "stage": "running",
                "ran": list(pipelines_to_run),
                "skipped": list(pipelines_to_skip),
            }
        )
        await db.commit()

    coros = [_RUNNERS[name](parent_run_id, advisor_id, trigger_source) for name in pipelines_to_run]
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
    "REFRESH_ADVISOR_ALL_PIPELINE_NAME",
    "SUB_REFRESH_OWNERS_OFFICERS",
    "SUB_RESOLVE_ADVISOR_WEBSITE",
    "decide_pipelines",
    "required_provider_keys",
    "run_advisor_refresh",
]
