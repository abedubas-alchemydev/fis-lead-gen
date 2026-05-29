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
- ``health-check``        — runs when ``registration_date`` OR
  ``formation_date`` is NULL. FINRA Form BD enrichment (free, no
  LLM). Also re-derives ``clearing_classification`` and
  ``is_niche_restricted`` from the FINRA-side text fields.
- ``refresh-clearing``    — runs when ``current_clearing_partner``
  OR ``current_clearing_type`` is NULL. Cost: ~1 Gemini call on the
  X-17A-5 PDF. Reuses
  :meth:`ClearingPipelineService.extract_clearing_for_broker_dealer`.
- ``enrich``              — runs when no ``executive_contacts`` rows
  exist for this BD. Cost: ~2 Apollo + ~1 Hunter via the company-only
  search (no per-officer fan-out — that has its own dedicated FE
  button and stays separate).
- ``focus-contact``       — runs when the BD has no ExecutiveContact
  with ``source="focus_report"``. Extracts the X-17A-5 "PERSON TO
  CONTACT" block (name/title/phone/email) + net capital via Gemini.
  This is the only automated source of BD filing-contact emails.
  Opt-in via ``has_focus_contact`` — the bulk backfill enables it; the
  interactive endpoint and cost-sensitive callers leave it ``None`` so
  it never fires a per-request Gemini call.

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
from typing import Iterable, Literal

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
from app.services.edgar import EdgarService
from app.services.finra import FinraService
from app.services.firm_alias_enricher import ensure_resolver_aliases
from app.services.focus_reports import FocusReportService
from app.services.serpapi import SerpAPIClient
from app.services.serper import SerperClient
from app.services.service_models import FinraBrokerDealerRecord
from app.services.website_resolver import resolve_website


RefreshScope = Literal["all", "list_only"]

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
SUB_REFRESH_FILINGS = "broker_dealer_refresh_filings"
# Per-firm wrapper around ClearingPipelineService.extract_clearing_for_broker_dealer
# (X-17A-5 PDF + Gemini → clearing_partner / clearing_type + clearing_arrangements
# row). Pre-existing health-check gated on these fields but didn't fill them —
# this sub-pipeline closes that gap so per-firm gap-fill is truly self-contained.
SUB_REFRESH_CLEARING = "broker_dealer_refresh_clearing"
# Per-firm wrapper around FocusCeoExtractionService.extract — pulls the
# X-17A-5 "PERSON TO CONTACT" block (name/title/phone/email) + net capital
# and persists an ExecutiveContact(source="focus_report"). Folded into the
# orchestrator so the bulk backfill owns it after the dedicated detail-page
# "Extract FOCUS Data" button was retired.
SUB_FOCUS_CONTACT = "broker_dealer_focus_contact"

# Display labels used in the parent's notes.summary toast string.
_SUB_LABEL = {
    SUB_REFRESH_FINANCIALS: "financials",
    SUB_RESOLVE_WEBSITE: "website",
    SUB_HEALTH_CHECK: "finra",
    SUB_ENRICH: "contacts",
    SUB_REFRESH_FILINGS: "filings",
    SUB_REFRESH_CLEARING: "clearing",
    SUB_FOCUS_CONTACT: "focus contact",
}

# Sub-pipelines whose target fields drive a column the user can see in the
# /master-list grid. The row-level Refresh button passes scope="list_only"
# so it doesn't burn website + contacts calls fixing data the list view
# can't even render. The detail-page button still uses scope="all".
_LIST_ONLY_PIPELINES: frozenset[str] = frozenset(
    {SUB_REFRESH_FINANCIALS, SUB_HEALTH_CHECK, SUB_REFRESH_FILINGS, SUB_REFRESH_CLEARING}
)


@dataclass(frozen=True)
class GateDecision:
    """Which sub-pipelines the orchestrator should fire and which to skip."""

    to_run: tuple[str, ...]
    to_skip: tuple[str, ...]


def gap_report_for(
    broker_dealer: BrokerDealer,
    has_contacts: bool,
    *,
    aggressive: bool = False,
    has_focus_contact: bool | None = None,
) -> dict[str, list[str]]:
    """For each sub-pipeline, return the list of BD column names that
    currently have a gap. Non-empty list = the pipeline should fire.

    The bulk gap-fill script (``scripts/gap_fill_broker_dealers.py``)
    uses this in its pre-flight scan to produce the per-column summary
    table without firing any pipelines. ``decide_pipelines`` consumes
    the same report so the gating logic stays single-source-of-truth.

    With ``aggressive=False`` (default), only the legacy strict
    ``IS NULL`` predicates are applied — preserves backward compat for
    the per-firm refresh-all endpoint.

    With ``aggressive=True``, additional predicates catch sentinel
    values (e.g. ``current_clearing_type='unknown'``,
    ``clearing_classification='needs_review'``) and detail-page-only
    fields that the strict predicates skip. Used by the bulk gap-fill
    script to surface and retry rows that the original extraction
    flagged as "couldn't decide" but never re-attempted.
    """
    report: dict[str, list[str]] = {
        SUB_REFRESH_FINANCIALS: [],
        SUB_RESOLVE_WEBSITE: [],
        SUB_HEALTH_CHECK: [],
        SUB_REFRESH_CLEARING: [],
        SUB_ENRICH: [],
        SUB_REFRESH_FILINGS: [],
    }

    # ── refresh-financials ──
    if broker_dealer.latest_net_capital is None:
        report[SUB_REFRESH_FINANCIALS].append("latest_net_capital")
    if broker_dealer.yoy_growth is None:
        report[SUB_REFRESH_FINANCIALS].append("yoy_growth")
    if broker_dealer.health_status is None:
        report[SUB_REFRESH_FINANCIALS].append("health_status")
    if aggressive:
        if broker_dealer.latest_excess_net_capital is None:
            report[SUB_REFRESH_FINANCIALS].append("latest_excess_net_capital")
        if broker_dealer.three_year_cagr is None:
            report[SUB_REFRESH_FINANCIALS].append("three_year_cagr")
        if broker_dealer.total_assets_yoy is None:
            report[SUB_REFRESH_FINANCIALS].append("total_assets_yoy")
        # latest_total_assets + required_min_capital are filled by the
        # same refresh-financials pass as a side-effect (rolled up from
        # financial_metrics rows). Gate on them too so a BD whose other
        # financials are filled but these two are NULL still re-fires.
        if broker_dealer.latest_total_assets is None:
            report[SUB_REFRESH_FINANCIALS].append("latest_total_assets")
        if broker_dealer.required_min_capital is None:
            report[SUB_REFRESH_FINANCIALS].append("required_min_capital")

    # ── resolve-website ──
    if not broker_dealer.website:
        report[SUB_RESOLVE_WEBSITE].append("website")

    # ── health-check (FINRA Form BD enrichment) ──
    # Strict predicate (paired in the FINRA parse): registration_date +
    # formation_date.
    if broker_dealer.registration_date is None:
        report[SUB_HEALTH_CHECK].append("registration_date")
    if broker_dealer.formation_date is None:
        report[SUB_HEALTH_CHECK].append("formation_date")
    if aggressive:
        # Detail-page fields filled by the same FINRA parse. If
        # registration_date is set but these are missing, the strict gate
        # won't fire and the detail page stays sparse.
        if broker_dealer.dba_names is None:
            report[SUB_HEALTH_CHECK].append("dba_names")
        if broker_dealer.types_of_business is None:
            report[SUB_HEALTH_CHECK].append("types_of_business")
        if broker_dealer.direct_owners is None:
            report[SUB_HEALTH_CHECK].append("direct_owners")
        if broker_dealer.executive_officers is None:
            report[SUB_HEALTH_CHECK].append("executive_officers")
        if broker_dealer.firm_operations_text is None:
            report[SUB_HEALTH_CHECK].append("firm_operations_text")
        if broker_dealer.city is None:
            report[SUB_HEALTH_CHECK].append("city")
        if broker_dealer.state is None:
            report[SUB_HEALTH_CHECK].append("state")
        if broker_dealer.branch_count is None:
            report[SUB_HEALTH_CHECK].append("branch_count")

    # ── refresh-clearing (X-17A-5 PDF + Gemini) ──
    if broker_dealer.current_clearing_partner is None:
        report[SUB_REFRESH_CLEARING].append("current_clearing_partner")
    if broker_dealer.current_clearing_type is None:
        report[SUB_REFRESH_CLEARING].append("current_clearing_type")
    if aggressive:
        # Sentinel re-fire: rows the original extraction couldn't decide.
        # ``unknown`` is the LLM's "I saw a doc but it doesn't say who clears";
        # ``needs_review`` is the classifier's same verdict on the rollup.
        # PR #409 (resolver fix) likely changes the outcome for many of these.
        if broker_dealer.current_clearing_type == "unknown":
            report[SUB_REFRESH_CLEARING].append("current_clearing_type=unknown")
        if broker_dealer.clearing_classification in (None, "needs_review"):
            report[SUB_REFRESH_CLEARING].append("clearing_classification")
        # Note: clearing_raw_text was previously gated here but no service
        # in the codebase writes to it -- it's a vestigial column the FE
        # renders conditionally on the detail page (amber "raw clearing
        # text" callout when classification is NULL/unknown). Gating on
        # it IS NULL meant the clearing pipeline re-fired on every BD
        # forever, wasting Gemini calls. The clearing_classification
        # check above already covers the "uncertain" case meaningfully.

    # ── enrich (executive_contacts) ──
    if not has_contacts:
        report[SUB_ENRICH].append("executive_contacts")

    # ── filings (EDGAR) ──
    # No CIK → no way to query EDGAR. Don't open the gate.
    if bool(broker_dealer.cik) and broker_dealer.last_filing_date is None:
        report[SUB_REFRESH_FILINGS].append("last_filing_date")

    # ── focus-contact (X-17A-5 "PERSON TO CONTACT" → ExecutiveContact) ──
    # Opt-in: only evaluated when the caller supplies the signal. ``None``
    # (the default) leaves SUB_FOCUS_CONTACT out of the report entirely, so
    # existing callers — and the interactive refresh-all endpoint — see no
    # change and never fire an unbudgeted Gemini call. The bulk backfill
    # passes the real bool so it re-attempts every BD that still lacks a
    # focus_report contact.
    if has_focus_contact is not None:
        report[SUB_FOCUS_CONTACT] = (
            [] if has_focus_contact else ["focus_report_contact"]
        )

    return report


def decide_pipelines(
    broker_dealer: BrokerDealer,
    has_contacts: bool,
    scope: RefreshScope = "all",
    *,
    aggressive: bool = False,
    has_focus_contact: bool | None = None,
) -> GateDecision:
    """Inspect the BD and return the (run, skip) split.

    The caller queries ``has_contacts`` separately because the BD row
    doesn't carry an ``executive_contacts`` count column — we count the
    relationship explicitly instead of joining, to avoid the cost of
    fetching every row when all we need is "any?".

    ``scope="list_only"`` force-skips ``SUB_RESOLVE_WEBSITE`` and
    ``SUB_ENRICH`` regardless of their gate, because neither populates a
    column on the master-list grid. The other three sub-pipelines are
    evaluated normally — i.e. still skipped if their target fields are
    already set — so we never overwrite present data.

    ``aggressive=True`` widens each gate to catch sentinel values
    (``'unknown'`` / ``'needs_review'``) and detail-page-only fields.
    Off by default to preserve the per-firm refresh-all endpoint's
    legacy behavior; the bulk gap-fill script passes ``True``.

    ``has_focus_contact`` opts the FOCUS-contact sub-pipeline in or out.
    ``None`` (default) keeps ``SUB_FOCUS_CONTACT`` out of the decision
    entirely — the interactive endpoint and cheap/inspect callers stay on
    their current six-pipeline behavior. ``False`` opens the gate (no
    ``focus_report`` contact yet → extract one); ``True`` closes it. Only
    the bulk backfill passes a real bool, because each fire is a Gemini
    vision call on the X-17A-5 PDF.
    """
    report = gap_report_for(
        broker_dealer,
        has_contacts,
        aggressive=aggressive,
        has_focus_contact=has_focus_contact,
    )

    to_run: list[str] = []
    to_skip: list[str] = []

    (to_run if report[SUB_REFRESH_FINANCIALS] else to_skip).append(
        SUB_REFRESH_FINANCIALS
    )

    if scope == "list_only":
        # Force-skip — website is not a list-view field.
        to_skip.append(SUB_RESOLVE_WEBSITE)
    else:
        (to_run if report[SUB_RESOLVE_WEBSITE] else to_skip).append(
            SUB_RESOLVE_WEBSITE
        )

    (to_run if report[SUB_HEALTH_CHECK] else to_skip).append(SUB_HEALTH_CHECK)

    (to_run if report[SUB_REFRESH_CLEARING] else to_skip).append(
        SUB_REFRESH_CLEARING
    )

    if scope == "list_only":
        # Force-skip — contacts are detail-page only.
        to_skip.append(SUB_ENRICH)
    else:
        (to_run if report[SUB_ENRICH] else to_skip).append(SUB_ENRICH)

    (to_run if report[SUB_REFRESH_FILINGS] else to_skip).append(
        SUB_REFRESH_FILINGS
    )

    # focus-contact — detail-page-only, so force-skipped under list_only
    # exactly like enrich. Opt-in: absent from the decision entirely when
    # the caller leaves ``has_focus_contact`` at its ``None`` default.
    if has_focus_contact is not None:
        if scope == "list_only":
            to_skip.append(SUB_FOCUS_CONTACT)
        else:
            (to_run if report.get(SUB_FOCUS_CONTACT) else to_skip).append(
                SUB_FOCUS_CONTACT
            )

    return GateDecision(to_run=tuple(to_run), to_skip=tuple(to_skip))


def required_provider_keys(pipelines: Iterable[str]) -> list[str]:
    """Return missing provider-key labels for the pipelines we're about to
    fire. Used by the endpoint to decide whether to refuse with 503 before
    queuing a parent run that can't complete."""
    pipelines = set(pipelines)
    missing: list[str] = []

    if (
        SUB_REFRESH_FINANCIALS in pipelines
        or SUB_REFRESH_CLEARING in pipelines
        or SUB_FOCUS_CONTACT in pipelines
    ):
        # These sub-pipelines all drive the X-17A-5 PDF extraction stack and
        # need whichever LLM provider is configured. Reported once even when
        # several of them need the same key, so the toast doesn't double
        # up on "Gemini" / "OpenAI".
        if settings.llm_provider == "openai":
            if not settings.openai_api_key:
                missing.append("OpenAI")
        elif not settings.gemini_api_key:
            missing.append("Gemini")

    if SUB_RESOLVE_WEBSITE in pipelines:
        # The chain runs Apollo → serper.dev (optional) → SerpAPI; if
        # all three are missing the chain has no way to land a
        # candidate. Apollo alone is enough to proceed; serper.dev and
        # SerpAPI fall through silently when unset.
        if not (
            settings.apollo_api_key
            or settings.serper_api_key
            or settings.serpapi_api_key
        ):
            missing.append("Apollo/serper/SerpAPI (none configured)")

    if SUB_ENRICH in pipelines and not settings.apollo_api_key:
        missing.append("Apollo (required for contact enrichment)")

    return missing


async def has_executive_contacts(db: AsyncSession, bd_id: int) -> bool:
    """Cheap "any real person row?" check against ``executive_contacts``.

    Excludes the synthetic "Company (Organization Profile)" rows that the
    Apollo /organizations/enrich fallback emits when per-officer matching
    yields nothing. Those rows are useful for the UI (HQ phone, company
    LinkedIn) but they should NOT permanently close the enrich gate —
    otherwise a BD that first enriched before /people/match per-officer
    fan-out existed would never get upgraded to real per-person contacts.

    Using ``select(1).limit(1)`` so Postgres short-circuits on first hit
    instead of counting all rows.
    """
    stmt = (
        select(ExecutiveContact.id)
        .where(
            ExecutiveContact.bd_id == bd_id,
            ExecutiveContact.title != "Company (Organization Profile)",
        )
        .limit(1)
    )
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
            serper = SerperClient(settings.serper_api_key) if settings.serper_api_key else None
            serpapi = SerpAPIClient(settings.serpapi_api_key) if settings.serpapi_api_key else None

            if apollo is None and serper is None and serpapi is None:
                summary = "No website-resolver provider keys configured."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary

            # Populate resolver_aliases lazily — same contract as the
            # /resolve-website endpoint. ``[]`` on Gemini failure leaves
            # the column NULL for retry on the next request; resolver
            # still runs without the augmented tokens.
            aliases = await ensure_resolver_aliases(db, broker_dealer)

            website, source, reason = await resolve_website(
                broker_dealer.name,
                broker_dealer.crd_number,
                apollo,
                serpapi,
                serper,
                dba_names=broker_dealer.dba_names,
                resolver_aliases=aliases,
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
                    # registration_date + formation_date come off the same
                    # FINRA Form BD PDF (services/brokercheck_pdf.py) and are
                    # already plumbed onto FinraBrokerDealerRecord. They were
                    # silently dropped here, leaving the firm-detail page's
                    # "Registration Date" stat NULL on every refresh-all
                    # path. Mirror the truthiness-and-changed gate the other
                    # fields use so we never overwrite a present value with
                    # a fresh None from a partial parse.
                    if enriched_record.registration_date and enriched_record.registration_date != broker_dealer.registration_date:
                        broker_dealer.registration_date = enriched_record.registration_date
                        changes.append("registration_date")
                    if enriched_record.formation_date and enriched_record.formation_date != broker_dealer.formation_date:
                        broker_dealer.formation_date = enriched_record.formation_date
                        changes.append("formation_date")
                    # ``dba_names`` was wired through FinraService.enrich_with_detail
                    # in commit 4c658f8 but never applied here, so per-firm
                    # refresh-all couldn't backfill firms whose initial_load
                    # missed their ``firm_other_names``. Same truthiness gate
                    # the rest of the block uses — empty list / None won't
                    # overwrite a present value.
                    if enriched_record.dba_names and enriched_record.dba_names != broker_dealer.dba_names:
                        broker_dealer.dba_names = enriched_record.dba_names
                        changes.append("dba_names")

                # ``branch_count`` and ``business_type`` come off the FINRA
                # *search* payload, not the Form BD PDF that
                # ``enrich_with_detail`` parses. Without this extra fetch the
                # two fields are stuck at whatever ``initial_load`` captured
                # — sometimes never, for firms FINRA's keyword/alpha sweep
                # missed. One free HTTP call (FINRA-only, no LLM) closes the
                # gap so the master-list and detail page reflect current
                # firm metadata.
                search_meta = await finra_service.fetch_firm_search_metadata(
                    broker_dealer.crd_number
                )
                if search_meta is not None:
                    new_branch_count = search_meta.get("branch_count")
                    if (
                        new_branch_count is not None
                        and new_branch_count != broker_dealer.branch_count
                    ):
                        broker_dealer.branch_count = new_branch_count
                        changes.append("branch_count")
                    new_business_type = search_meta.get("business_type")
                    if (
                        new_business_type
                        and new_business_type != broker_dealer.business_type
                    ):
                        broker_dealer.business_type = new_business_type
                        changes.append("business_type")

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


async def _run_refresh_clearing(parent_run_id: int, bd_id: int, trigger_source: str) -> tuple[str, str]:
    """Wrap ``ClearingPipelineService.extract_clearing_for_broker_dealer``
    so its self-managed PipelineRun row becomes a child of the
    orchestrator's parent. Mirror of ``_run_refresh_financials``.
    """
    from app.services.pipeline import ClearingPipelineService

    async with SessionLocal() as db:
        child_id = await _create_child_run(
            db,
            pipeline_name=SUB_REFRESH_CLEARING,
            parent_run_id=parent_run_id,
            bd_id=bd_id,
            trigger_source=trigger_source,
        )

    service = ClearingPipelineService()
    try:
        await service.extract_clearing_for_broker_dealer(
            bd_id,
            pipeline_run_id=child_id,
            trigger_source=trigger_source,
        )
    except Exception as exc:
        logger.exception("refresh-all/refresh-clearing failed for bd %s", bd_id)
        return "failed", f"{type(exc).__name__}: {str(exc)[:200]}"

    async with SessionLocal() as db:
        child = await db.get(PipelineRun, child_id)
        if child is None:
            return "failed", "Child run row disappeared after extraction."
        try:
            payload = json.loads(child.notes or "{}")
            summary = payload.get("summary") or "Clearing extraction complete."
        except (TypeError, ValueError):
            summary = "Clearing extraction complete."
        return child.status, summary[:500]


async def _run_refresh_filings(parent_run_id: int, bd_id: int, trigger_source: str) -> tuple[str, str]:
    """Re-query EDGAR submissions for the BD's CIK and update
    ``BrokerDealer.last_filing_date`` if a more recent filing exists.

    Gate is "BD has a CIK and last_filing_date is None" (see
    ``decide_pipelines``), so we don't run for firms without an EDGAR
    presence. Stale-but-present dates are still refreshed by the daily
    ``filing_monitor`` cron — this sub-pipeline is a per-firm catch-up
    for rows initial_load missed.
    """
    async with SessionLocal() as db:
        child_id = await _create_child_run(
            db,
            pipeline_name=SUB_REFRESH_FILINGS,
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

            if not broker_dealer.cik:
                summary = "No CIK on file — cannot query EDGAR."
                await _finalize_child(
                    child_id, status="completed_with_errors", success=0, failure=1, summary=summary
                )
                return "completed_with_errors", summary

            edgar = EdgarService()
            latest = await edgar.fetch_last_filing_for_cik(broker_dealer.cik)

            if latest is None:
                summary = "EDGAR returned no parseable filings."
                await _finalize_child(
                    child_id, status="completed_with_errors", success=0, failure=1, summary=summary
                )
                return "completed_with_errors", summary

            existing = broker_dealer.last_filing_date
            if existing is not None and latest <= existing:
                summary = f"No newer filings (current: {existing.isoformat()})."
                await _finalize_child(
                    child_id, status="completed", success=1, failure=0, summary=summary
                )
                return "completed", summary

            broker_dealer.last_filing_date = latest
            if not broker_dealer.filings_index_url:
                padded = broker_dealer.cik.strip().lstrip("0").zfill(10)
                broker_dealer.filings_index_url = (
                    f"{settings.sec_submissions_base_url}/CIK{padded}.json"
                )
            await db.commit()

        summary = f"Updated last_filing_date to {latest.isoformat()}."
        await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
        return "completed", summary

    except Exception as exc:
        logger.exception("refresh-all/refresh-filings failed for bd %s", bd_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


async def _run_focus_contact(parent_run_id: int, bd_id: int, trigger_source: str) -> tuple[str, str]:
    """Wrap ``FocusCeoExtractionService.extract`` so its work lands as a
    child of the orchestrator's parent run.

    Pulls the X-17A-5 "PERSON TO CONTACT" block (name/title/phone/email) +
    net capital off the latest filing and persists an
    ``ExecutiveContact(source="focus_report")``. This is the only automated
    source of BD filing-contact emails — it replaces the retired detail-page
    "Extract FOCUS Data" button.
    """
    # Imported lazily (mirrors _run_refresh_clearing) to keep the orchestrator's
    # import graph flat and avoid pulling the Gemini/PDF stack at module load.
    from app.services.focus_ceo_extraction import FocusCeoExtractionService

    async with SessionLocal() as db:
        child_id = await _create_child_run(
            db,
            pipeline_name=SUB_FOCUS_CONTACT,
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

        service = FocusCeoExtractionService()
        async with SessionLocal() as db:
            broker_dealer = await db.get(BrokerDealer, bd_id)
            if broker_dealer is None:
                raise RuntimeError(f"Broker-dealer {bd_id} not found mid-flight.")
            result = await service.extract(db, broker_dealer)
            await db.commit()

        if result.extraction_status == "success":
            landed = [
                label
                for value, label in (
                    (result.ceo_name, "contact"),
                    (result.ceo_email, "email"),
                    (result.ceo_phone, "phone"),
                )
                if value
            ]
            summary = (
                f"Extracted FOCUS {', '.join(landed)}."
                if landed
                else "FOCUS extraction succeeded (no contact fields)."
            )
            await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
            return "completed", summary

        if result.extraction_status in ("no_pdf", "low_confidence"):
            # Clean miss, not a hard failure — mirror resolve-website's contract
            # so the parent toast reads "we tried, found nothing".
            summary = (
                "No X-17A-5 PDF on EDGAR."
                if result.extraction_status == "no_pdf"
                else "FOCUS extraction below confidence threshold."
            )
            await _finalize_child(
                child_id, status="completed_with_errors", success=0, failure=1, summary=summary
            )
            return "completed_with_errors", summary

        # extraction_status == "error" (or any unexpected value).
        summary = (result.extraction_notes or "FOCUS extraction error.")[:200]
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary

    except Exception as exc:
        logger.exception("refresh-all/focus-contact failed for bd %s", bd_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


_RUNNERS = {
    SUB_REFRESH_FINANCIALS: _run_refresh_financials,
    SUB_RESOLVE_WEBSITE: _run_resolve_website,
    SUB_HEALTH_CHECK: _run_health_check,
    SUB_ENRICH: _run_enrich,
    SUB_REFRESH_FILINGS: _run_refresh_filings,
    SUB_REFRESH_CLEARING: _run_refresh_clearing,
    SUB_FOCUS_CONTACT: _run_focus_contact,
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
    "RefreshScope",
    "SUB_ENRICH",
    "SUB_FOCUS_CONTACT",
    "SUB_HEALTH_CHECK",
    "SUB_REFRESH_CLEARING",
    "SUB_REFRESH_FILINGS",
    "SUB_REFRESH_FINANCIALS",
    "SUB_RESOLVE_WEBSITE",
    "decide_pipelines",
    "has_executive_contacts",
    "required_provider_keys",
    "run_refresh_all",
]
