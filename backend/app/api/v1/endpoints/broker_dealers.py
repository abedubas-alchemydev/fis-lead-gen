from __future__ import annotations

import base64
import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.schemas.broker_dealer import (
    BrokerDealerDetail,
    BrokerDealerListResponse,
    BrokerDealerProfileResponse,
    DeficiencyStatusSummary,
    ExecutiveContactItem,
    FocusCeoExtractionResponse,
    FilingHistoryItem,
    FilingHistoryPage,
    FinancialMetricItem,
    FinancialMetricsResponse,
    RefreshAllRequest,
    RefreshAllResponse,
    RefreshFinancialsResponse,
    RegistrationComplianceSummary,
    ResolveWebsiteResponse,
)
from app.schemas.clearing_membership import ClearingMembershipItem
from app.schemas.favorite_list import FavoriteListWithMembership
from app.schemas.favorites import FavoriteResponse
from app.services.contacts import ExecutiveContactService
from app.services.edgar import EdgarService, build_edgar_filing_url
from app.schemas.pipeline import ClearingArrangementItem, ClearingArrangementsResponse
from app.services.alerts import AlertRepository
from app.core.feature_permissions import MASTER_LIST
from app.services.auth import ensure_feature, get_current_user
from app.services.broker_dealers import BrokerDealerRepository
from app.services.unknown_reasons import (
    clearing_trigger_fields,
    derive_clearing_unknown_reason,
    derive_executive_contact_unknown_reason,
    derive_financial_unknown_reason,
    financial_trigger_fields,
    to_unknown_reason,
    with_trigger_fields,
)
from app.services.user_lists import (
    add_favorite,
    is_favorited,
    record_visit,
    remove_favorite,
)
from app.services.contact_discovery.apollo_match import ApolloMatchProvider
from app.services.contact_discovery.orchestrator import discover_contact
from app.services.contacts import (
    ApolloLookupError,
    ContactEnrichmentUnavailableError,
    ContactNotFoundError,
    NoEmailForLookupError,
)
from app.services.finra import FinraService
from app.services.finra_pdf_service import (
    FinraPdfFetchError,
    FinraPdfNotFound,
    fetch_brokercheck_pdf,
)
from app.services.apollo import ApolloClient
from app.services.focus_ceo_extraction import FocusCeoExtractionService
from app.services.focus_reports import FocusReportService
from app.services.refresh_all_orchestrator import (
    REFRESH_ALL_PIPELINE_NAME,
    decide_pipelines,
    has_executive_contacts,
    required_provider_keys,
    run_refresh_all,
)
from app.services.serpapi import SerpAPIClient
from app.services.serper import SerperClient
from app.services.service_models import FinraBrokerDealerRecord
from app.services.firm_alias_enricher import ensure_resolver_aliases
from app.services.pipeline_reaper import STALE_REFRESH_RUN_AGE
from app.services.website_resolver import resolve_website

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/broker-dealers")
repository = BrokerDealerRepository()


def _require_master_list(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    ensure_feature(user, MASTER_LIST)
    return user

alert_repository = AlertRepository()
contact_service = ExecutiveContactService()
finra_service = FinraService()
focus_ceo_service = FocusCeoExtractionService()
edgar_service = EdgarService()


def _parse_states(state: list[str] | None) -> list[str]:
    if not state:
        return []

    parsed: list[str] = []
    for value in state:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return parsed


def _build_internal_filing_history(
    *,
    recent_alerts,
    financials,
    clearing_arrangements,
) -> list[FilingHistoryItem]:
    """Compose the alert / FOCUS / X-17A-5 entries the FE renders in the
    FILING HISTORY card. Returns items unsorted — callers merge with EDGAR
    rows and sort once at the end.
    """
    items: list[FilingHistoryItem] = []
    for alert in recent_alerts:
        items.append(
            FilingHistoryItem(
                label=alert.form_type,
                filed_at=alert.filed_at,
                summary=alert.summary,
                source_filing_url=alert.source_filing_url,
                priority=alert.priority,
            )
        )
    for metric in financials:
        items.append(
            FilingHistoryItem(
                label="FOCUS Report",
                filed_at=datetime.combine(metric.report_date, time(hour=17), tzinfo=timezone.utc),
                summary="Financial report used for net capital and YoY growth calculations.",
                source_filing_url=metric.source_filing_url,
                priority="medium",
            )
        )
    for arrangement in clearing_arrangements:
        report_date = arrangement.report_date
        if report_date is None:
            continue
        items.append(
            FilingHistoryItem(
                label="X-17A-5 Annual Report",
                filed_at=datetime.combine(report_date, time(hour=16), tzinfo=timezone.utc),
                summary=(
                    f"Clearing arrangement extracted as {arrangement.clearing_partner or 'Unknown'} "
                    f"({arrangement.clearing_type or 'unknown'})."
                ),
                source_filing_url=arrangement.source_filing_url,
                priority="medium",
            )
        )
    return items


_ACCESSION_PATTERN = re.compile(r"(\d{10}-\d{2}-\d{6})")


def _extract_accession_from_url(url: str | None) -> str | None:
    """Pull a SEC accession number (e.g., 0001234567-25-000123) out of a
    filing URL when one is present. Returns ``None`` when the URL has no
    embedded accession (FOCUS report URLs that point at a primary doc
    inside an accession folder do; clearing-arrangement URLs sometimes
    don't).
    """
    if not url:
        return None
    match = _ACCESSION_PATTERN.search(url)
    if match:
        return match.group(1)
    # SEC archive URLs sometimes use the no-dash form: /000123456725000123/
    no_dash = re.search(r"/(\d{18})/", url)
    if no_dash:
        raw = no_dash.group(1)
        return f"{raw[:10]}-{raw[10:12]}-{raw[12:]}"
    return None


@router.get("", response_model=BrokerDealerListResponse)
async def list_broker_dealers(
    search: str | None = Query(default=None),
    state: list[str] | None = Query(default=None),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    health_filter: list[str] | None = Query(default=None, alias="health"),
    lead_priority_filter: list[str] | None = Query(default=None, alias="lead_priority"),
    clearing_partner_filter: list[str] | None = Query(default=None, alias="clearing_partner"),
    clearing_type_filter: list[str] | None = Query(default=None, alias="clearing_type"),
    types_of_business_filter: list[str] | None = Query(default=None, alias="types_of_business"),
    min_net_capital: float | None = Query(default=None, ge=0),
    max_net_capital: float | None = Query(default=None, ge=0),
    registered_after: date | None = Query(default=None),
    registered_before: date | None = Query(default=None),
    list_mode: str = Query(default="primary", alias="list", pattern="^(primary|alternative|all)$"),
    sort_by: str = Query(default="latest_net_capital"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> BrokerDealerListResponse:
    if (
        min_net_capital is not None
        and max_net_capital is not None
        and min_net_capital > max_net_capital
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_net_capital must be less than or equal to max_net_capital.",
        )
    if (
        registered_after is not None
        and registered_before is not None
        and registered_after > registered_before
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="registered_after must be on or before registered_before.",
        )

    return await repository.list_broker_dealers(
        db,
        search=search,
        states=_parse_states(state),
        statuses=_parse_states(status_filter),
        health_statuses=_parse_states(health_filter),
        lead_priorities=_parse_states(lead_priority_filter),
        clearing_partners=_parse_states(clearing_partner_filter),
        clearing_types=_parse_states(clearing_type_filter),
        # NOT _parse_states: it comma-splits each value, but type-of-business
        # labels legitimately contain commas (e.g. "...networking, kiosk or
        # similar arrangment with a: bank, savings bank or association, or...").
        # The FE sends each selection as its own repeated query param, so the
        # list already arrives intact — just drop blanks, never split.
        types_of_business=[t for t in (types_of_business_filter or []) if t.strip()],
        min_net_capital=min_net_capital,
        max_net_capital=max_net_capital,
        registered_after=registered_after,
        registered_before=registered_before,
        list_mode=list_mode,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        limit=limit,
    )


@router.get("/states", response_model=list[str])
async def list_broker_dealer_states(
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> list[str]:
    return await repository.list_states(db)


@router.get("/clearing-partners", response_model=list[str])
async def list_clearing_partners(
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> list[str]:
    return await repository.list_clearing_partners(db)


@router.get("/types-of-business", response_model=list[dict])
async def list_types_of_business(
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Distinct types-of-business across all firms with per-type counts.

    Fuels the multi-select filter on the master list. Shape: `[{type, count}, ...]`.
    """
    return await repository.list_types_of_business(db)


@router.get("/{broker_dealer_id}/focus-report.pdf")
async def download_focus_report_pdf(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Stream the firm's latest X-17A-5 (FOCUS) PDF.

    Downloads the latest filing from SEC EDGAR into a per-request tempdir
    and serves the bytes directly back to the browser via ``Response``. The
    persistent PDF cache that previously sat at ``PDF_CACHE_DIR`` was
    removed in Sprint 2 task #20 (it had grown to ~9 GB on the container).
    Each click costs one fresh SEC fetch (~5-10s) — acceptable UX for a
    rare on-demand action.
    """
    from app.services.pdf_downloader import PdfDownloaderService, pdf_tempdir  # deferred to avoid circular

    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    downloader = PdfDownloaderService()
    with pdf_tempdir(prefix="focus_report_endpoint_") as tmp_dir:
        try:
            record = await downloader.download_latest_x17a5_pdf(broker_dealer, tmp_dir)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not fetch FOCUS report from SEC: {exc}",
            ) from exc

        if record is None or not record.bytes_base64:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No FOCUS report available for this firm.",
            )

        # Decode while still inside the tempdir context. The Response carries
        # the bytes in memory, so the file on disk can be wiped on ``with``
        # exit without breaking the response stream the way FileResponse
        # would (FileResponse opens the file at write-time, not at endpoint
        # return-time).
        pdf_bytes = base64.b64decode(record.bytes_base64)

    filename = f"{broker_dealer.crd_number or broker_dealer.id}-focus-report.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{broker_dealer_id}/brokercheck.pdf")
async def download_brokercheck_pdf(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Stream the firm's FINRA BrokerCheck Detailed Report PDF.

    On-demand fetch from files.brokercheck.finra.org (~2-5s). The bytes
    flow straight from the upstream response into the browser via
    ``Response`` — no disk involved. The persistent FINRA cache that
    previously sat at ``PDF_CACHE_DIR/finra`` was removed in Sprint 2
    task #20.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")
    if not broker_dealer.crd_number:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This firm has no CRD number on file; BrokerCheck PDF is not fetchable.",
        )

    try:
        pdf_bytes = await fetch_brokercheck_pdf(broker_dealer.crd_number)
    except FinraPdfNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="FINRA has no Detailed Report PDF for this CRD.",
        ) from exc
    except FinraPdfFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch BrokerCheck PDF from FINRA: {exc}",
        ) from exc

    # `inline` lets the browser render the PDF in the new tab opened by the
    # frontend's `<a target="_blank">`. `attachment` would force a download
    # regardless of `target`, which is exactly what the client asked us to
    # stop doing.
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename={broker_dealer.crd_number}-brokercheck.pdf"
        },
    )


@router.get("/{broker_dealer_id}", response_model=BrokerDealerDetail)
async def get_broker_dealer(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> BrokerDealerDetail:
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    detail = BrokerDealerDetail.model_validate(broker_dealer)
    memberships = await repository.list_clearing_memberships(db, broker_dealer_id)
    detail.member_agencies = sorted({m.agency for m in memberships if m.status == "active"})
    arrangements = await repository.list_clearing_arrangements(db, broker_dealer_id)
    financials = await repository.get_financial_metrics(db, broker_dealer_id)
    detail.current_clearing_unknown_reason = to_unknown_reason(
        with_trigger_fields(
            derive_clearing_unknown_reason(arrangements[0] if arrangements else None),
            clearing_trigger_fields(broker_dealer),
        )
    )
    detail.financial_unknown_reason = to_unknown_reason(
        with_trigger_fields(
            derive_financial_unknown_reason(
                financials[0] if financials else None,
                broker_dealer=broker_dealer,
                clearing_arrangement=arrangements[0] if arrangements else None,
            ),
            financial_trigger_fields(broker_dealer),
        )
    )
    return detail


@router.get("/{broker_dealer_id}/financials", response_model=FinancialMetricsResponse)
async def get_broker_dealer_financials(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> FinancialMetricsResponse:
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    rows = await repository.get_financial_metrics(db, broker_dealer_id)
    items: list[FinancialMetricItem] = []
    for row in rows:
        item = FinancialMetricItem.model_validate(row)
        item.unknown_reason = to_unknown_reason(derive_financial_unknown_reason(row))
        items.append(item)
    return FinancialMetricsResponse(items=items)


@router.get("/{broker_dealer_id}/clearing-arrangements", response_model=ClearingArrangementsResponse)
async def get_broker_dealer_clearing_arrangements(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> ClearingArrangementsResponse:
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    rows = await repository.list_clearing_arrangements(db, broker_dealer_id)
    items: list[ClearingArrangementItem] = []
    for row in rows:
        item = ClearingArrangementItem.model_validate(row)
        item.unknown_reason = to_unknown_reason(derive_clearing_unknown_reason(row))
        items.append(item)
    return ClearingArrangementsResponse(items=items)


@router.get("/{broker_dealer_id}/adjacent")
async def get_adjacent_broker_dealers(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, int | None]:
    """Return the previous and next broker-dealer IDs for navigation arrows.

    Walks the master-list page's *default* view: ``list_mode='primary'``
    (``is_deficient=false``) ordered by ``name ASC NULLS LAST, id ASC``.
    The frontend has a separate return-envelope path that walks whatever
    filter / sort the user actually had on screen; this endpoint is the
    fallback for direct-link / bookmark visits, so it should match what
    the user would see if they navigated to ``/master-list`` fresh.
    """
    ordered_ids = (
        await db.execute(
            select(BrokerDealer.id)
            .where(BrokerDealer.is_deficient.is_(False))
            .order_by(BrokerDealer.name.asc().nullslast(), BrokerDealer.id.asc())
        )
    ).scalars().all()

    try:
        idx = ordered_ids.index(broker_dealer_id)
    except ValueError:
        # Firm exists but is filtered out of the primary list (e.g.
        # is_deficient=true). Surface no neighbours rather than guess —
        # the buttons disable, same UX contract as a head/tail row.
        return {"prev_id": None, "next_id": None}

    prev_id = ordered_ids[idx - 1] if idx > 0 else None
    next_id = ordered_ids[idx + 1] if idx < len(ordered_ids) - 1 else None
    return {"prev_id": prev_id, "next_id": next_id}


class EnrichOfficerRequest(BaseModel):
    """One officer the frontend wants discovered via the multi-provider chain.

    ``type="person"`` requires ``first_name`` and ``last_name``; ``title`` is
    optional but preserved on the resulting row so the UI can render the
    FINRA-derived role alongside the provider-found email / phone.

    ``type="organization"`` requires ``org_name`` (defaults to the firm's
    own name if omitted). Used for sole-member / parent-holding officer rows
    that aren't human beings.
    """

    type: str = Field(pattern="^(person|organization)$")
    first_name: str | None = None
    last_name: str | None = None
    org_name: str | None = None
    title: str | None = None


class EnrichRequestBody(BaseModel):
    officers: list[EnrichOfficerRequest] = Field(default_factory=list)
    # When True (the user-facing "Generate More Details" button), force a full
    # re-run past the server-side throttles: Phase 1's 24h cooldown + 90-day
    # freshness (``enrich_contacts(force=True)``) and Phase 2's 90-day
    # discovery cache (``discover_contact(use_cache=False)``). Defaults to
    # False so automated / bulk callers keep their guards.
    refresh: bool = False


@router.post("/{broker_dealer_id}/enrich", response_model=list[ExecutiveContactItem])
async def enrich_broker_dealer_contacts(
    broker_dealer_id: int,
    body: EnrichRequestBody | None = Body(default=None),
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> list[ExecutiveContactItem]:
    """Enrich executive contacts for a firm.

    Phase 1: run the Apollo-based company search (cheap, often catches
    officers Apollo already has). Phase 2: for each officer the frontend sent
    that didn't get matched in Phase 1, run the multi-provider discovery
    chain (``settings.contact_discovery_chain`` -- PDL / Apollo / Hunter /
    Snov / LinkedIn) anchored on the firm's website domain.

    ``refresh=True`` (the user-facing button) forces both phases past their
    throttles: Phase 1's cooldown + freshness and Phase 2's 90-day cache.

    When an ``officers`` list is supplied a Phase-1
    ``ContactEnrichmentUnavailableError`` is non-fatal -- Phase 2 still runs,
    since the chain doesn't depend on the ``contact_enrichment_provider``
    gate. With no officers the endpoint stays pure company-search and still
    surfaces 503 when that provider is unavailable.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    officers = list(body.officers) if body else []
    force = bool(body.refresh) if body else False

    # Did Phase 1 (company search) actually issue Apollo /people/match for this
    # firm's officers in THIS request? Only the forced button path with Apollo
    # configured does, and ``force`` guarantees enrich_contacts bypassed its
    # cooldown/freshness short-circuits, so the calls really went out. When they
    # did, Phase 2 must skip the chain's ``apollo_match`` provider: it issues the
    # identical /people/match call, so re-running it for the officers Phase 1
    # missed just burns a second credit on the same no-match (and would re-admit
    # cross-firm matches Phase 1's org guard already rejected). hunter + snov —
    # the email finders Phase 1 never runs — still carry Phase 2.
    #
    # When Phase 1 is unavailable (CONTACT_ENRICHMENT_PROVIDER=disabled, the
    # default) the except branch leaves this False, so ``apollo_match`` stays in
    # the chain as the *only* Apollo path and nothing is lost.
    phase1_ran_apollo = False
    try:
        contacts = await contact_service.enrich_contacts(db, broker_dealer, force=force)
        phase1_ran_apollo = (
            force
            and settings.contact_enrichment_provider.lower() == "apollo"
            and bool(settings.apollo_api_key)
        )
    except ContactEnrichmentUnavailableError as exc:
        # Company-search is unavailable. If the caller seeded officers, let
        # Phase 2 (the discovery chain) carry the request anyway; otherwise
        # there's nothing left to try, so surface the 503.
        if not officers:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        contacts = await contact_service.list_contacts(db, broker_dealer.id)

    if officers:
        # Anchor the Phase-2 email finders (Hunter/Snov) on the firm's real
        # email domain when we can infer it from the addresses Phase 1 /
        # existing contacts already carry. The website host is frequently a
        # different domain (e.g. website ``aegiscapcorp.com`` vs email
        # ``aegiscap.com``) and would send those providers hunting the wrong
        # domain; fall back to it only when no corporate email is known yet.
        derived_domain = _derive_email_domain(_known_contact_emails(contacts))
        domain = derived_domain or _resolve_domain(broker_dealer)
        existing_names = {_normalise_name(contact.name) for contact in contacts}
        # See ``phase1_ran_apollo`` above: drop the redundant second Apollo call.
        phase2_exclude = (
            frozenset({ApolloMatchProvider.name}) if phase1_ran_apollo else frozenset()
        )
        discovered = 0
        for officer in officers:
            entity = _officer_to_entity(officer, broker_dealer, domain)
            if entity is None:
                continue
            if _normalise_name(entity["cache_name"]) in existing_names:
                continue
            row = await discover_contact(
                entity,
                bd_id=broker_dealer.id,
                session=db,
                use_cache=not force,
                exclude_providers=phase2_exclude,
            )
            if row is not None:
                # Commit immediately so the row's apollo_person_id is persisted
                # before Apollo's async phone-reveal callback races in (the
                # webhook 200s on a no-match and Apollo won't retry). Use the
                # entity cache_name (== row.name) so we don't touch the
                # commit-expired ORM object.
                await db.commit()
                discovered += 1
                existing_names.add(_normalise_name(entity["cache_name"]))
        if discovered:
            contacts = await contact_service.list_contacts(db, broker_dealer.id)

    return [ExecutiveContactItem.model_validate(item) for item in contacts]


@router.post(
    "/{broker_dealer_id}/contacts/{contact_id}/find-phone",
    response_model=ExecutiveContactItem,
)
async def find_phone_for_contact(
    broker_dealer_id: int,
    contact_id: int,
    _: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ExecutiveContactItem:
    """Per-person Apollo /people/match phone lookup.

    Costs one Apollo credit per call. The frontend gates this behind an
    explicit per-row button (the "Find phone" pill on the People panel) so
    spend stays tied to user intent. Returns the updated contact; ``phone``
    will be ``null`` if Apollo had no phone for this email — the UI uses
    that to render a "tried, nothing found" state.
    """
    contact = await db.get(ExecutiveContact, contact_id)
    if contact is None or contact.bd_id != broker_dealer_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found.")

    try:
        updated = await contact_service.find_phone_for_contact(db, contact_id)
    except ContactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoEmailForLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except ContactEnrichmentUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ApolloLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"apollo: {exc}"
        ) from exc

    return ExecutiveContactItem.model_validate(updated)


def _resolve_domain(broker_dealer: BrokerDealer) -> str | None:
    """Extract a bare ``example.com`` domain from the firm's website.

    Handles ``https://www.example.com/path?x=1`` -> ``example.com`` and
    leaves an already-bare ``example.com`` alone. Returns ``None`` when the
    firm has no website on file so downstream providers can skip cleanly.
    """
    website = (broker_dealer.website or "").strip()
    if not website:
        return None
    candidate = website
    if "://" in candidate:
        candidate = candidate.split("://", 1)[1]
    candidate = candidate.split("/", 1)[0].strip().lower()
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate or None


# Free / personal mailbox providers never represent a firm's email domain, so a
# contact's personal Gmail can't hijack the Phase-2 domain anchor.
_FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "ymail.com",
        "outlook.com", "hotmail.com", "live.com", "msn.com",
        "aol.com", "icloud.com", "me.com", "mac.com",
        "protonmail.com", "proton.me", "gmx.com", "mail.com",
    }
)


def _known_contact_emails(contacts: list[ExecutiveContact]) -> list[str]:
    """Every email already on a firm's contacts -- the scalar ``email`` column
    plus each ``emails`` JSONB entry's ``value``."""
    out: list[str] = []
    for contact in contacts:
        scalar = getattr(contact, "email", None)
        if scalar:
            out.append(str(scalar))
        array = getattr(contact, "emails", None)
        if isinstance(array, list):
            out.extend(
                str(entry["value"])
                for entry in array
                if isinstance(entry, dict) and entry.get("value")
            )
    return out


def _derive_email_domain(emails: list[str]) -> str | None:
    """Most common corporate email domain among ``emails`` (or ``None``).

    A firm's website host often differs from its email-sending domain (e.g.
    website ``aegiscapcorp.com`` vs email ``aegiscap.com``). When real emails
    are already known, their domain is a far better anchor for the Phase-2
    email finders (Hunter/Snov) than the website host. Free mailbox providers
    are ignored; ties break on the domain name so the result is deterministic.
    """
    counts: dict[str, int] = {}
    for email in emails:
        if not email or "@" not in email:
            continue
        domain = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
        if not domain or domain in _FREE_EMAIL_DOMAINS:
            continue
        counts[domain] = counts.get(domain, 0) + 1
    if not counts:
        return None
    return max(sorted(counts), key=lambda d: counts[d])


def _officer_to_entity(
    officer: EnrichOfficerRequest,
    broker_dealer: BrokerDealer,
    domain: str | None,
) -> dict[str, object] | None:
    """Translate an EnrichOfficerRequest into the orchestrator's entity shape.

    Returns ``None`` when the officer is missing the fields its type requires
    (a person without first+last, an org entry that resolves to an empty
    name) so the endpoint can skip it without a provider round-trip.
    """
    if officer.type == "person":
        # Title-case the name. The frontend lowercases first/last
        # (parseFinraName), but the orchestrator persists the contact's
        # display name straight from these fields, and the endpoint's dedup
        # skip-set compares ``cache_name`` against the Phase-1 rows (already
        # title-cased via ``_parse_finra_person_name``). Both must agree, and
        # the persisted name must render cleanly. Apollo matching is
        # case-insensitive, so the provider query is unaffected.
        first = (officer.first_name or "").strip().title()
        last = (officer.last_name or "").strip().title()
        if not first or not last:
            return None
        return {
            "type": "person",
            "first_name": first,
            "last_name": last,
            "org_name": broker_dealer.name,
            "title": officer.title,
            "domain": domain,
            "cache_name": f"{first} {last}",
        }

    # organisation
    org_name = (officer.org_name or broker_dealer.name or "").strip()
    if not org_name:
        return None
    return {
        "type": "organization",
        "first_name": None,
        "last_name": None,
        "org_name": org_name,
        "title": officer.title,
        "domain": domain,
        "cache_name": org_name,
    }


def _normalise_name(name: str | None) -> str:
    return (name or "").strip().lower()


@router.post("/{broker_dealer_id}/extract-focus-ceo", response_model=FocusCeoExtractionResponse)
async def extract_focus_ceo(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> FocusCeoExtractionResponse:
    """On-demand (manual / ops) extraction of the FOCUS filing contact + net
    capital from the latest X-17A-5 PDF.

    Downloads the most recent X-17A-5 filing for this broker-dealer, sends it to
    Gemini for structured extraction, and persists the contact person as an
    ExecutiveContact with source="focus_report".

    No longer backs a FE button — the detail-page "Extract FOCUS Data" button
    was retired once bulk coverage moved into the gap-fill backfill (the
    refresh-all orchestrator's ``broker_dealer_focus_contact`` sub-pipeline).
    Kept as the single-firm manual trigger for ops / re-extraction.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    result = await focus_ceo_service.extract(db, broker_dealer)
    await db.commit()

    return FocusCeoExtractionResponse(
        ceo_name=result.ceo_name,
        ceo_title=result.ceo_title,
        ceo_phone=result.ceo_phone,
        ceo_email=result.ceo_email,
        net_capital=result.net_capital,
        report_date=result.report_date,
        source_pdf_url=result.source_pdf_url,
        confidence_score=result.confidence_score,
        extraction_status=result.extraction_status,
        extraction_notes=result.extraction_notes,
    )


@router.post("/{broker_dealer_id}/favorite", response_model=FavoriteResponse)
async def favorite_broker_dealer(
    broker_dealer_id: int,
    current_user: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteResponse:
    """Favorite a broker-dealer for the calling user.

    Idempotent: a second call returns 200 with the original ``favorited_at``.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    row = await add_favorite(db, current_user.id, broker_dealer_id)
    return FavoriteResponse(favorited=True, favorited_at=row.created_at)


@router.delete("/{broker_dealer_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
async def unfavorite_broker_dealer(
    broker_dealer_id: int,
    current_user: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove a favorite. Idempotent: 204 even when the row wasn't there."""
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    await remove_favorite(db, current_user.id, broker_dealer_id)


@router.get(
    "/{firm_id}/favorite-lists",
    response_model=list[FavoriteListWithMembership],
)
async def get_firm_favorite_lists(
    firm_id: int,
    current_user: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> list[FavoriteListWithMembership]:
    """Return the calling user's lists with an ``is_member`` flag for ``firm_id``.

    Powers the FE list-picker so each list can render a checked state without
    a per-list round-trip. ``item_count`` is computed via the same outer-join
    sub-aggregate that ``GET /favorite-lists`` uses so the list-picker can
    show ``Watchlist A · 3 firms`` next to the checkbox. Default list first,
    then by ``created_at`` ascending — matches the sidebar ordering.
    """
    firm_check = await db.execute(
        select(BrokerDealer.id).where(BrokerDealer.id == firm_id)
    )
    if firm_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firm not found.")

    item_count_sq = (
        select(
            FavoriteListItem.list_id.label("list_id"),
            func.count(FavoriteListItem.id).label("count"),
        )
        .group_by(FavoriteListItem.list_id)
        .subquery()
    )
    is_member_expr = (
        exists()
        .where(
            FavoriteListItem.list_id == FavoriteList.id,
            FavoriteListItem.broker_dealer_id == firm_id,
        )
        .label("is_member")
    )
    stmt = (
        select(
            FavoriteList,
            func.coalesce(item_count_sq.c.count, 0).label("item_count"),
            is_member_expr,
        )
        .outerjoin(item_count_sq, FavoriteList.id == item_count_sq.c.list_id)
        .where(FavoriteList.user_id == current_user.id)
        .order_by(FavoriteList.is_default.desc(), FavoriteList.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        FavoriteListWithMembership(
            id=fl.id,
            name=fl.name,
            is_default=fl.is_default,
            item_count=int(count),
            created_at=fl.created_at,
            is_member=bool(is_member),
        )
        for fl, count, is_member in rows
    ]


@router.post("/{broker_dealer_id}/visit", status_code=status.HTTP_204_NO_CONTENT)
async def visit_broker_dealer(
    broker_dealer_id: int,
    current_user: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Record a detail-page visit.

    Fired fire-and-forget by the frontend on mount. Upserts the ``user_visit``
    row: first call sets ``visit_count=1`` and both timestamps to ``now()``;
    subsequent calls bump ``visit_count`` and ``last_visited_at`` while the
    original ``first_visited_at`` is preserved.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    await record_visit(db, current_user.id, broker_dealer_id)


@router.get("/{broker_dealer_id}/profile", response_model=BrokerDealerProfileResponse)
async def get_broker_dealer_profile(
    broker_dealer_id: int,
    current_user: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> BrokerDealerProfileResponse:
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    financials = await repository.get_financial_metrics(db, broker_dealer_id)
    clearing_arrangements = await repository.list_clearing_arrangements(db, broker_dealer_id)
    membership_rows = await repository.list_clearing_memberships(db, broker_dealer_id)
    introducing_arrangements = await repository.list_introducing_arrangements(db, broker_dealer_id)
    industry_arrangements = await repository.list_industry_arrangements(db, broker_dealer_id)
    executive_contacts = await repository.get_executive_contacts(db, broker_dealer_id)
    recent_alerts = (
        await alert_repository.list_alerts(
            db,
            form_types=[],
            priorities=[],
            is_read=None,
            broker_dealer_id=broker_dealer_id,
            page=1,
            limit=8,
        )
    ).items

    filing_history = _build_internal_filing_history(
        recent_alerts=recent_alerts,
        financials=financials,
        clearing_arrangements=clearing_arrangements,
    )
    filing_history.sort(key=lambda item: item.filed_at, reverse=True)

    favorited, favorited_at = await is_favorited(db, current_user.id, broker_dealer_id)

    detail = BrokerDealerDetail.model_validate(broker_dealer)
    detail.member_agencies = sorted({m.agency for m in membership_rows if m.status == "active"})
    detail.current_clearing_unknown_reason = to_unknown_reason(
        with_trigger_fields(
            derive_clearing_unknown_reason(
                clearing_arrangements[0] if clearing_arrangements else None
            ),
            clearing_trigger_fields(broker_dealer),
        )
    )
    detail.financial_unknown_reason = to_unknown_reason(
        with_trigger_fields(
            derive_financial_unknown_reason(
                financials[0] if financials else None,
                broker_dealer=broker_dealer,
                clearing_arrangement=clearing_arrangements[0] if clearing_arrangements else None,
            ),
            financial_trigger_fields(broker_dealer),
        )
    )

    financial_items: list[FinancialMetricItem] = []
    for metric in financials:
        item = FinancialMetricItem.model_validate(metric)
        item.unknown_reason = to_unknown_reason(derive_financial_unknown_reason(metric))
        financial_items.append(item)

    clearing_items: list[ClearingArrangementItem] = []
    for arrangement in clearing_arrangements:
        item = ClearingArrangementItem.model_validate(arrangement)
        item.unknown_reason = to_unknown_reason(derive_clearing_unknown_reason(arrangement))
        clearing_items.append(item)

    executive_items = [ExecutiveContactItem.model_validate(item) for item in executive_contacts]
    executive_contacts_unknown_reason = to_unknown_reason(
        derive_executive_contact_unknown_reason(list(executive_contacts))
    )

    membership_items = [ClearingMembershipItem.model_validate(m) for m in membership_rows]

    return BrokerDealerProfileResponse(
        broker_dealer=detail,
        financials=financial_items,
        clearing_arrangements=clearing_items,
        clearing_memberships=membership_items,
        introducing_arrangements=introducing_arrangements,
        industry_arrangements=industry_arrangements,
        recent_alerts=recent_alerts,
        filing_history=filing_history[:10],
        executive_contacts=executive_items,
        executive_contacts_unknown_reason=executive_contacts_unknown_reason,
        is_favorited=favorited,
        favorited_at=favorited_at,
        registration_compliance=RegistrationComplianceSummary(
            registration_status=broker_dealer.status,
            registration_date=broker_dealer.registration_date,
            sec_file_number=broker_dealer.sec_file_number,
            crd_number=broker_dealer.crd_number,
            branch_count=broker_dealer.branch_count,
            business_type=broker_dealer.business_type,
            filings_index_url=broker_dealer.filings_index_url,
        ),
        deficiency_status=DeficiencyStatusSummary(
            is_deficient=broker_dealer.is_deficient,
            latest_deficiency_filed_at=broker_dealer.latest_deficiency_filed_at,
            message=(
                "Form 17a-11 deficiency notice detected. This firm belongs in the Alternative List."
                if broker_dealer.is_deficient
                else "No active Form 17a-11 deficiency notice is currently tracked."
            ),
        ),
    )


@router.get("/{broker_dealer_id}/filing-history", response_model=FilingHistoryPage)
async def get_filing_history(
    broker_dealer_id: int,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> FilingHistoryPage:
    """Paginated filing history for the firm. Merges every SEC EDGAR filing
    (when a CIK is on file) with our enriched alert / FOCUS / X-17A-5 entries,
    dedupes by accession number, and sorts newest first.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    financials = await repository.get_financial_metrics(db, broker_dealer_id)
    clearing_arrangements = await repository.list_clearing_arrangements(db, broker_dealer_id)
    alert_response = await alert_repository.list_alerts(
        db,
        form_types=[],
        priorities=[],
        is_read=None,
        broker_dealer_id=broker_dealer_id,
        page=1,
        limit=100,
    )

    internal_items = _build_internal_filing_history(
        recent_alerts=alert_response.items,
        financials=financials,
        clearing_arrangements=clearing_arrangements,
    )

    edgar_pairs: list[tuple[str | None, FilingHistoryItem]] = []
    if broker_dealer.cik:
        raw_filings = await edgar_service.list_all_filings_for_cik(broker_dealer.cik)
        for filing in raw_filings:
            filing_date = filing.get("filing_date")
            if filing_date is None:
                continue
            accession = filing.get("accession_number")
            primary_doc = filing.get("primary_document")
            primary_desc = filing.get("primary_doc_description")
            url = (
                build_edgar_filing_url(broker_dealer.cik, str(accession) if accession else None,
                                       str(primary_doc) if primary_doc else None)
                or broker_dealer.filings_index_url
            )
            item = FilingHistoryItem(
                label=str(filing.get("form") or "Filing"),
                filed_at=datetime.combine(filing_date, time(hour=12), tzinfo=timezone.utc),
                summary=str(primary_desc) if primary_desc else "Filed with SEC.",
                source_filing_url=url,
                priority=None,
            )
            edgar_pairs.append((str(accession) if accession else None, item))

    internal_accessions: set[str] = set()
    for item in internal_items:
        accession = _extract_accession_from_url(item.source_filing_url)
        if accession:
            internal_accessions.add(accession)

    deduped_edgar = [
        item for accession, item in edgar_pairs
        if not accession or accession not in internal_accessions
    ]

    merged = internal_items + deduped_edgar
    merged.sort(key=lambda item: item.filed_at, reverse=True)

    total = len(merged)
    total_pages = max(1, (total + limit - 1) // limit) if total else 0
    start = (page - 1) * limit
    end = start + limit

    return FilingHistoryPage(
        items=merged[start:end],
        total=total,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


@router.post("/{broker_dealer_id}/health-check")
async def trigger_health_check(
    broker_dealer_id: int,
    _: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    """Triggered Enrichment / Health Check (Revision 2.2).

    When a user clicks a firm, the system performs a real-time health check
    to determine whether the contact information, FINRA detail data, or net
    capital must be refreshed from the latest filing.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker-dealer not found.")

    changes: list[str] = []

    # Re-fetch FINRA detail if the firm has a CRD number
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
            if enriched_record.registration_date and enriched_record.registration_date != broker_dealer.registration_date:
                broker_dealer.registration_date = enriched_record.registration_date
                changes.append("registration_date")
            if enriched_record.formation_date and enriched_record.formation_date != broker_dealer.formation_date:
                broker_dealer.formation_date = enriched_record.formation_date
                changes.append("formation_date")
            if enriched_record.types_of_business_other and enriched_record.types_of_business_other != broker_dealer.types_of_business_other:
                broker_dealer.types_of_business_other = enriched_record.types_of_business_other
                changes.append("types_of_business_other")

    # Re-apply the niche-restricted flag only. clearing_classification is NOT
    # derived here anymore: the old determine_clearing_classification() regex
    # was inverted and only returned "needs_review", clobbering good labels on
    # every refresh. The clearing label is owned by the FOCUS-extraction +
    # clearing_validator path and the batch classifier.
    from app.services.classification import classify_niche_restricted

    new_niche = classify_niche_restricted(broker_dealer.types_of_business)
    if broker_dealer.is_niche_restricted != new_niche:
        broker_dealer.is_niche_restricted = new_niche
        changes.append("is_niche_restricted")

    await db.commit()

    return {
        "broker_dealer_id": broker_dealer_id,
        "fields_refreshed": changes,
        "total_changes": len(changes),
    }


@router.post(
    "/{broker_dealer_id}/resolve-website",
    response_model=ResolveWebsiteResponse,
)
async def resolve_broker_dealer_website(
    broker_dealer_id: int,
    current_user: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> ResolveWebsiteResponse:
    """On-demand firm-website resolver — Apollo -> Hunter chain.

    Fires from the master-list firm-detail page when ``bd.website`` is
    null. Idempotent: returns the cached value without re-running the
    chain when ``website`` is already set. Race-safe: persists via
    ``UPDATE ... WHERE website IS NULL`` so two concurrent calls can't
    overwrite a winner. Provider-error path leaves the column unchanged
    so a transient outage doesn't poison the cache; only ``no_valid_candidate``
    persists a NULL website (and even then we keep the existing row).
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )

    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Broker-dealer not found.",
        )

    # Idempotent — already resolved for this firm.
    if broker_dealer.website:
        return ResolveWebsiteResponse(
            website=broker_dealer.website,
            website_source=broker_dealer.website_source,
            reason=None,
        )

    apollo_key = settings.apollo_api_key
    if not apollo_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Apollo API key is not configured.",
        )
    apollo = ApolloClient(apollo_key)
    serper = (
        SerperClient(settings.serper_api_key)
        if settings.serper_api_key
        else None
    )
    serpapi = (
        SerpAPIClient(settings.serpapi_api_key)
        if settings.serpapi_api_key
        else None
    )

    # Lazy alias enrichment: populate ``resolver_aliases`` via Gemini if
    # this is the firm's first resolution attempt. The enricher writes
    # ``[]`` (not NULL) on a successful-but-empty response so the call
    # doesn't re-fire on every page mount for firms with no useful
    # parent/brand alternates. On Gemini failure we get ``[]`` here and
    # the column stays NULL for retry on the next visit; the resolver
    # chain still runs without the augmented tokens (graceful degrade).
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
        # Race-safe persistence: only stamp the row if it's still NULL.
        # The second concurrent caller's UPDATE finds nothing to change
        # and falls through to the re-fetch below.
        stmt = (
            update(BrokerDealer)
            .where(BrokerDealer.id == broker_dealer_id)
            .where(BrokerDealer.website.is_(None))
            .values(website=website, website_source=source)
        )
        await db.execute(stmt)
        await db.commit()
        await db.refresh(broker_dealer)

        return ResolveWebsiteResponse(
            website=broker_dealer.website or website,
            website_source=broker_dealer.website_source or source,
            reason=None,
        )

    # No website resolved — return reason without persisting (provider-
    # error) or with the column left NULL (clean miss). The chain
    # already left the field NULL by not running an UPDATE; nothing
    # else to write.
    return ResolveWebsiteResponse(
        website=None,
        website_source=None,
        reason=reason,
    )


# ───────────────────────────────────────────────────────────────────────────
# Per-firm on-demand financial pipeline trigger
# ───────────────────────────────────────────────────────────────────────────

REFRESH_FINANCIALS_PIPELINE_NAME = "financial_pdf_pipeline_single"


async def _run_refresh_financials_background(
    run_id: int, bd_id: int, trigger_source: str
) -> None:
    """Background task: drive the queued PipelineRow through the per-firm
    extraction. ``FocusReportService.load_financial_metrics_for_broker_dealer``
    transitions the run row through ``running → completed`` (or ``→ failed``)
    via :meth:`_finalize_pipeline_run` / :meth:`_mark_pipeline_run_failed`,
    so this wrapper just defends against unexpected exceptions and logs the
    outcome."""

    service = FocusReportService()
    try:
        await service.load_financial_metrics_for_broker_dealer(
            bd_id,
            trigger_source=trigger_source,
            pipeline_run_id=run_id,
        )
    except Exception:
        logger.exception(
            "refresh-financials background task failed (run_id=%s bd_id=%s)",
            run_id,
            bd_id,
        )


async def _in_flight_refresh_financials_run(
    db: AsyncSession, bd_id: int
) -> PipelineRun | None:
    """Return any queued/running refresh-financials PipelineRun for this
    firm, or ``None`` if none is in flight.

    Returns the full row so the manual endpoint can include ``status``
    in its 409 response without a second ``db.get()``. The match is
    keyed on ``pipeline_name`` + status window + a JSON substring on
    ``notes`` (``"bd_id": <int>``). The substring is bd-specific so we
    don't false-positive on sibling ids (e.g. bd_id=12 matching
    ``"bd_id": 123``); the surrounding quotes + space anchor it.
    """
    bd_id_marker = f'"bd_id": {bd_id}'
    stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == REFRESH_FINANCIALS_PIPELINE_NAME)
        .where(PipelineRun.status.in_(("queued", "running")))
        .where(PipelineRun.notes.ilike(f"%{bd_id_marker}%"))
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _enqueue_refresh_financials(
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    bd_id: int,
    *,
    trigger_source: str,
) -> int:
    """Create a queued PipelineRun row + schedule the BackgroundTask.

    Caller is responsible for the in-flight check via
    :func:`_in_flight_refresh_financials_run_id` BEFORE calling this —
    the helper does not re-check, so concurrent callers could race a
    duplicate run if they skip the guard. Returns the new run id.
    """
    pipeline_run = PipelineRun(
        pipeline_name=REFRESH_FINANCIALS_PIPELINE_NAME,
        trigger_source=trigger_source,
        status="queued",
        total_items=1,
        processed_items=0,
        success_count=0,
        failure_count=0,
        notes=json.dumps({"bd_id": bd_id, "stage": "queued"}),
    )
    db.add(pipeline_run)
    await db.commit()
    await db.refresh(pipeline_run)

    background_tasks.add_task(
        _run_refresh_financials_background,
        pipeline_run.id,
        bd_id,
        trigger_source,
    )
    return pipeline_run.id


def _refresh_financials_provider_available() -> bool:
    """True if a configured LLM provider key is present.

    Mirrors the gate at the start of the manual endpoint. The auto path
    uses this to short-circuit the whole batch with a single warning
    instead of 503-ing on every firm.
    """
    if settings.llm_provider == "openai":
        return bool(settings.openai_api_key)
    return bool(settings.gemini_api_key)


async def schedule_auto_refresh_financials_batch(
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    bd_ids: list[int],
    *,
    trigger_source: str,
) -> int:
    """Schedule auto re-extraction for each bd_id.

    Best-effort by design: per-firm in-flight runs are silently skipped
    (not 409'd); the whole batch is skipped with a single warning if no
    LLM provider key is configured. Returns the count of runs actually
    queued for log telemetry on the calling endpoint.

    Called from the filing-monitor endpoints after
    ``FilingMonitorService.run`` returns its ``auto_extract_bd_ids``
    list. Each ``bd_id`` becomes one ``PipelineRun`` tagged with the
    auto trigger_source, then handed to the same BackgroundTask worker
    as the manual /refresh-financials path.
    """
    if not bd_ids:
        return 0
    if not _refresh_financials_provider_available():
        logger.warning(
            "auto refresh-financials skipped: no LLM provider key configured "
            "(would have queued %d firm(s))",
            len(bd_ids),
        )
        return 0

    scheduled = 0
    for bd_id in bd_ids:
        in_flight = await _in_flight_refresh_financials_run(db, bd_id)
        if in_flight is not None:
            logger.debug(
                "auto refresh-financials skipped bd_id=%s; run %s already in flight",
                bd_id,
                in_flight.id,
            )
            continue
        new_run_id = await _enqueue_refresh_financials(
            db,
            background_tasks,
            bd_id,
            trigger_source=trigger_source,
        )
        scheduled += 1
        logger.info(
            "auto refresh-financials queued run_id=%s bd_id=%s trigger_source=%s",
            new_run_id,
            bd_id,
            trigger_source,
        )
    return scheduled


@router.post(
    "/{broker_dealer_id}/refresh-financials",
    response_model=RefreshFinancialsResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_broker_dealer_financials(
    broker_dealer_id: int,
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> RefreshFinancialsResponse:
    """On-demand X-17A-5 → Gemini extraction for a single firm.

    Async: extraction is 30-90s (PDF download + multi-year LLM
    extraction), so the handler creates a ``status="queued"``
    PipelineRun row, schedules the work as a FastAPI BackgroundTask,
    and returns 202 immediately. The FE polls
    ``GET /api/v1/pipeline/run/{run_id}`` for status.

    Auth: any authenticated user — Gemini cost is accepted as a product
    decision, mirroring the user-triggered Apollo / Hunter spend on the
    contact-discovery and resolve-website surfaces.

    Concurrency-guarded: a second POST while a queued/running run
    already exists for the same firm returns 409 with the in-flight
    ``run_id`` so the FE can pick up polling instead of erroring.
    Re-runs on already-populated firms ARE allowed once the prior run
    finishes — the rollup re-derives ``latest_net_capital``,
    ``yoy_growth``, ``health_status`` from the freshest
    ``FinancialMetric`` rows, which is the right behavior when a new
    annual filing lands.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Broker-dealer not found.",
        )

    # Provider key gate. Refuse early so the run never gets queued with
    # no chance of completing.
    if not _refresh_financials_provider_available():
        provider_label = "OpenAI" if settings.llm_provider == "openai" else "Gemini"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider_label} API key is not configured.",
        )

    # 409 concurrency guard for the manual path.
    in_flight = await _in_flight_refresh_financials_run(db, broker_dealer_id)
    if in_flight is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "A refresh-financials run is already in flight for this firm.",
                "run_id": in_flight.id,
                "status": in_flight.status,
                "broker_dealer_id": broker_dealer_id,
            },
        )

    trigger_source = f"manual_single:{current_user.email}"
    new_run_id = await _enqueue_refresh_financials(
        db,
        background_tasks,
        broker_dealer_id,
        trigger_source=trigger_source,
    )

    return RefreshFinancialsResponse(
        run_id=new_run_id,
        status="queued",
        broker_dealer_id=broker_dealer_id,
    )


# ───────────────────────────────────────────────────────────────────────────
# Per-firm refresh-all orchestrator
# ───────────────────────────────────────────────────────────────────────────

_REFRESH_ALL_COOLDOWN_SECONDS = 30


def _is_recent_run(started_at: datetime | None) -> bool:
    """True if a run started recently enough to still be considered alive.

    Anything older than STALE_REFRESH_RUN_AGE is presumed orphaned (its
    in-process BackgroundTask was killed by an instance swap) and must not be
    treated as in-flight.
    """

    if started_at is None:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return started_at >= datetime.now(timezone.utc) - STALE_REFRESH_RUN_AGE


async def _run_refresh_all_background(
    parent_run_id: int,
    bd_id: int,
    trigger_source: str,
    pipelines_to_run: tuple[str, ...],
    pipelines_to_skip: tuple[str, ...],
) -> None:
    """Background task: drive the parent PipelineRow through queued →
    running → terminal by firing each child sub-pipeline in parallel.
    Wraps :func:`run_refresh_all` in a defensive try so the BackgroundTask
    never bubbles an exception (the orchestrator already marks the parent
    failed on internal errors)."""

    try:
        await run_refresh_all(
            parent_run_id,
            bd_id,
            trigger_source=trigger_source,
            pipelines_to_run=pipelines_to_run,
            pipelines_to_skip=pipelines_to_skip,
        )
    except Exception:
        logger.exception(
            "refresh-all background task failed (parent_run_id=%s bd_id=%s)",
            parent_run_id,
            bd_id,
        )


@router.post(
    "/{broker_dealer_id}/refresh-all",
    response_model=RefreshAllResponse,
)
async def refresh_broker_dealer_all(
    broker_dealer_id: int,
    background_tasks: BackgroundTasks,
    response: Response,
    body: RefreshAllRequest = Body(default_factory=RefreshAllRequest),
    current_user: AuthenticatedUser = Depends(_require_master_list),
    db: AsyncSession = Depends(get_db_session),
) -> RefreshAllResponse:
    """One button per row → fan out to whatever sub-pipelines this firm
    actually needs.

    Inspects the BD record at request start and runs only the
    sub-pipelines whose target fields are still missing. If every gate
    fails, returns 200 + ``status="skipped"`` immediately with no
    PipelineRun row and zero provider cost.

    ``body.scope`` (default ``"all"``, preserves back-compat for empty
    POST bodies the FE used to send) controls which sub-pipelines the
    orchestrator considers. ``scope="list_only"`` is what the
    master-list row icon sends — it force-skips ``website`` and
    ``contacts`` because neither drives a column on the grid.

    Auth: any authenticated user. Each click costs at most ~2 Gemini +
    ~1 Apollo + ~1 Hunter calls (less when gates close); a 30-second
    per-(user, BD) cooldown blocks rage-clicks.

    Async: when at least one gate is open, returns 202 with the parent
    ``run_id`` and schedules a FastAPI BackgroundTask that drives the
    selected children in parallel. The FE polls
    ``GET /api/v1/pipeline/run/{run_id}`` until the parent's status
    flips to ``completed`` / ``completed_with_errors`` / ``failed``.
    """
    broker_dealer = await repository.get_broker_dealer(db, broker_dealer_id)
    if broker_dealer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Broker-dealer not found.",
        )

    has_contacts = await has_executive_contacts(db, broker_dealer_id)
    decision = decide_pipelines(broker_dealer, has_contacts=has_contacts, scope=body.scope)

    # Skipped short-circuit: every gate closed. No row, no cost.
    if not decision.to_run:
        return RefreshAllResponse(
            run_id=None,
            status="skipped",
            broker_dealer_id=broker_dealer_id,
            reason="Already complete.",
        )

    # 503 if any pipeline we're about to fire requires a provider key
    # that isn't configured.
    missing_keys = required_provider_keys(decision.to_run)
    if missing_keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Provider not configured: {', '.join(missing_keys)}.",
        )

    bd_marker = f'"bd_id": {broker_dealer_id}'

    # A genuinely in-flight refresh-all already exists for this firm. Rather
    # than 409 (which the browser surfaces as a red console error even though
    # the FE handles it cleanly), attach to the existing run and return 202
    # with that run_id. The FE polls run_id identically whether the run was
    # just queued or already in flight, so the behavior is unchanged.
    #
    # A run only counts as in-flight if it started within STALE_REFRESH_RUN_AGE.
    # An older running/queued row is an orphan — its in-process BackgroundTask
    # was killed by an instance swap and it will never finalize — so we ignore
    # it and start a fresh run instead of trapping the page on a dead run. The
    # startup reaper marks such orphans failed; this guard handles the window
    # before the next restart.
    in_flight_stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == REFRESH_ALL_PIPELINE_NAME)
        .where(PipelineRun.status.in_(("queued", "running")))
        .where(PipelineRun.notes.ilike(f"%{bd_marker}%"))
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    in_flight = (await db.execute(in_flight_stmt)).scalar_one_or_none()
    if in_flight is not None and _is_recent_run(in_flight.started_at):
        response.status_code = status.HTTP_202_ACCEPTED
        return RefreshAllResponse(
            run_id=in_flight.id,
            status="in_flight",
            broker_dealer_id=broker_dealer_id,
            reason="A refresh-all run is already in flight for this firm.",
        )

    # 429 — per-(user, BD) cooldown so rage-clicks don't burn provider
    # credits twice. Window matches the typical short-pipeline runtime
    # (~5-15s) plus margin; long financial extractions are protected by
    # the in-flight 409 above.
    cooldown_cutoff = datetime.now(timezone.utc) - timedelta(seconds=_REFRESH_ALL_COOLDOWN_SECONDS)
    user_marker = f"manual_single:{current_user.email}"
    cooldown_stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == REFRESH_ALL_PIPELINE_NAME)
        .where(PipelineRun.trigger_source == user_marker)
        .where(PipelineRun.notes.ilike(f"%{bd_marker}%"))
        .where(PipelineRun.started_at >= cooldown_cutoff)
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    recent = (await db.execute(cooldown_stmt)).scalar_one_or_none()
    if recent is not None:
        # Compute Retry-After from the row's started_at + 30s window.
        # HTTPException's headers kwarg is what survives back to the
        # client — mutating ``response.headers`` here gets discarded
        # because FastAPI builds a fresh Response from the exception.
        elapsed = (datetime.now(timezone.utc) - recent.started_at).total_seconds()
        retry_after = max(1, int(_REFRESH_ALL_COOLDOWN_SECONDS - elapsed))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Refresh-all cooldown active. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    trigger_source = user_marker
    parent_run = PipelineRun(
        pipeline_name=REFRESH_ALL_PIPELINE_NAME,
        trigger_source=trigger_source,
        status="queued",
        total_items=len(decision.to_run),
        processed_items=0,
        success_count=0,
        failure_count=0,
        notes=json.dumps(
            {
                "bd_id": broker_dealer_id,
                "stage": "queued",
                "scope": body.scope,
                "ran": list(decision.to_run),
                "skipped": list(decision.to_skip),
            }
        ),
    )
    db.add(parent_run)
    await db.commit()
    await db.refresh(parent_run)

    background_tasks.add_task(
        _run_refresh_all_background,
        parent_run.id,
        broker_dealer_id,
        trigger_source,
        decision.to_run,
        decision.to_skip,
    )

    response.status_code = status.HTTP_202_ACCEPTED
    return RefreshAllResponse(
        run_id=parent_run.id,
        status=parent_run.status,
        broker_dealer_id=broker_dealer_id,
        reason=None,
    )
