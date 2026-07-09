"""Endpoints for custom favorites lists (#17, all phases shipped).

Manages user-owned ``favorite_list`` rows and their ``favorite_list_item``
membership: GET/POST/PUT/DELETE on the lists, plus POST/DELETE on items.
The companion ``GET /broker-dealers/{id}/favorite-lists`` (in
``broker_dealers.py``) is the FE list-picker's data source. The legacy
``user_favorite`` table was dropped in 20260429_0021 (and again,
idempotently, in 20260429_0022); writes from the legacy POST path were
rewired onto ``favorite_list_item`` in PR #172.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    status,
)
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.bank import Bank
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.form4_transaction import Form4Transaction
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investment_advisor import InvestmentAdvisor
from app.models.pipeline_run import PipelineRun
from app.models.reporting_owner import ReportingOwner
from app.schemas.auth import AuthenticatedUser
from app.schemas.favorite_list import (
    FavoriteListAdvisorItemBatchCreate,
    FavoriteListAdvisorItemCreate,
    FavoriteListAdvisorItemResponse,
    FavoriteListBankItemBatchCreate,
    FavoriteListBankItemCreate,
    FavoriteListBankItemResponse,
    FavoriteListCreate,
    FavoriteListEnrichResponse,
    FavoriteListInvestorItemBatchCreate,
    FavoriteListInvestorItemCreate,
    FavoriteListInvestorItemResponse,
    FavoriteListItemBatchCreate,
    FavoriteListItemBatchResponse,
    FavoriteListItemCreate,
    FavoriteListItemResponse,
    FavoriteListReportingOwnerItemCreate,
    FavoriteListReportingOwnerItemResponse,
    FavoriteListResponse,
    FavoriteListUpdate,
    PaginatedFavoriteListItems,
)
from app.services.auth import get_current_user
from app.services.favorite_list_enrich_orchestrator import (
    FAVORITE_LIST_ENRICH_PIPELINE_NAME,
    EnrichItem,
    run_favorite_list_enrich_background,
)
from app.services.pipeline_reaper import is_run_stale

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/favorite-lists")

_DUPLICATE_NAME_DETAIL = "A list with that name already exists"


@router.get("", response_model=list[FavoriteListResponse])
async def list_favorite_lists(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[FavoriteListResponse]:
    """Return the calling user's lists.

    Default list first, then the rest by ``created_at`` ascending so newly
    created lists land at the bottom of the FE sidebar.
    """
    item_count = (
        select(
            FavoriteListItem.list_id.label("list_id"),
            func.count(FavoriteListItem.id).label("count"),
        )
        .group_by(FavoriteListItem.list_id)
        .subquery()
    )
    stmt = (
        select(FavoriteList, func.coalesce(item_count.c.count, 0).label("item_count"))
        .outerjoin(item_count, FavoriteList.id == item_count.c.list_id)
        .where(FavoriteList.user_id == current_user.id)
        .order_by(FavoriteList.is_default.desc(), FavoriteList.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        FavoriteListResponse(
            id=fl.id,
            name=fl.name,
            is_default=fl.is_default,
            item_count=int(count),
            created_at=fl.created_at,
        )
        for fl, count in rows
    ]


@router.get("/{list_id}/items", response_model=PaginatedFavoriteListItems)
async def list_favorite_list_items(
    list_id: int = Path(..., ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PaginatedFavoriteListItems:
    """Return a page of items in a list owned by the calling user.

    Returns broker-dealer, advisor, institutional-investor, Form 4
    reporting-owner, and bank items in one stream, ordered by
    ``created_at desc`` then ``id desc``. Each row carries an
    ``entity_type`` discriminator plus the populated id/name pair for its
    kind (the other kinds' columns are ``None``); bank rows additionally
    carry city/state (see the schema docstring).

    404 if the list doesn't exist or belongs to another user — same shape so
    a leaked list_id doesn't reveal whether it's "missing" vs. "yours".
    """
    owner_check = await db.execute(
        select(FavoriteList.id).where(
            FavoriteList.id == list_id,
            FavoriteList.user_id == current_user.id,
        )
    )
    if owner_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="favorite_list_not_found")

    total_stmt = select(func.count(FavoriteListItem.id)).where(
        FavoriteListItem.list_id == list_id
    )
    total = int((await db.execute(total_stmt)).scalar_one())

    offset = (page - 1) * page_size
    data_stmt = (
        select(
            FavoriteListItem.id,
            FavoriteListItem.broker_dealer_id,
            BrokerDealer.name.label("broker_dealer_name"),
            FavoriteListItem.advisor_id,
            InvestmentAdvisor.name.label("advisor_name"),
            FavoriteListItem.institutional_investor_id,
            InstitutionalInvestor.name.label("institutional_investor_name"),
            FavoriteListItem.reporting_owner_id,
            ReportingOwner.name.label("reporting_owner_name"),
            FavoriteListItem.bank_id,
            Bank.name.label("bank_name"),
            Bank.city.label("bank_city"),
            Bank.state.label("bank_state"),
            FavoriteListItem.created_at,
        )
        .outerjoin(
            BrokerDealer, BrokerDealer.id == FavoriteListItem.broker_dealer_id
        )
        .outerjoin(
            InvestmentAdvisor,
            InvestmentAdvisor.id == FavoriteListItem.advisor_id,
        )
        .outerjoin(
            InstitutionalInvestor,
            InstitutionalInvestor.id == FavoriteListItem.institutional_investor_id,
        )
        .outerjoin(
            ReportingOwner,
            ReportingOwner.id == FavoriteListItem.reporting_owner_id,
        )
        .outerjoin(
            Bank,
            Bank.id == FavoriteListItem.bank_id,
        )
        .where(FavoriteListItem.list_id == list_id)
        .order_by(FavoriteListItem.created_at.desc(), FavoriteListItem.id.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = (await db.execute(data_stmt)).all()

    items: list[FavoriteListItemResponse] = []
    for (
        _item_id,
        bd_id,
        bd_name,
        advisor_id,
        advisor_name,
        investor_id,
        investor_name,
        reporting_owner_id,
        reporting_owner_name,
        bank_id,
        bank_name,
        bank_city,
        bank_state,
        added_at,
    ) in rows:
        if bd_id is not None:
            items.append(
                FavoriteListItemResponse(
                    entity_type="broker_dealer",
                    broker_dealer_id=bd_id,
                    broker_dealer_name=bd_name,
                    added_at=added_at,
                )
            )
        elif advisor_id is not None:
            items.append(
                FavoriteListItemResponse(
                    entity_type="advisor",
                    advisor_id=advisor_id,
                    advisor_name=advisor_name,
                    added_at=added_at,
                )
            )
        elif investor_id is not None:
            items.append(
                FavoriteListItemResponse(
                    entity_type="institutional_investor",
                    institutional_investor_id=investor_id,
                    institutional_investor_name=investor_name,
                    added_at=added_at,
                )
            )
        elif bank_id is not None:
            items.append(
                FavoriteListItemResponse(
                    entity_type="bank",
                    bank_id=bank_id,
                    bank_name=bank_name,
                    bank_city=bank_city,
                    bank_state=bank_state,
                    added_at=added_at,
                )
            )
        else:
            items.append(
                FavoriteListItemResponse(
                    entity_type="reporting_owner",
                    reporting_owner_id=reporting_owner_id,
                    reporting_owner_name=reporting_owner_name,
                    added_at=added_at,
                )
            )
    return PaginatedFavoriteListItems(
        items=items, total=total, page=page, page_size=page_size
    )


async def _get_owned_list(
    db: AsyncSession, list_id: int, user_id: str
) -> FavoriteList:
    """Return the list iff it belongs to ``user_id``; 404 otherwise.

    Same opaque ``favorite_list_not_found`` detail as phase 1 so a leaked
    list_id can't be used to enumerate other users' lists.
    """
    result = await db.execute(
        select(FavoriteList).where(
            FavoriteList.id == list_id,
            FavoriteList.user_id == user_id,
        )
    )
    favorite_list = result.scalar_one_or_none()
    if favorite_list is None:
        raise HTTPException(status_code=404, detail="favorite_list_not_found")
    return favorite_list


async def _load_all_items(db: AsyncSession, list_id: int) -> list[EnrichItem]:
    """Enumerate every item in a list across all five entity types.

    Unlike ``GET /{list_id}/items`` this is unpaginated: the bulk-enrich
    worker needs the whole membership up front. Uses the same outer-join
    hydration so each row carries its firm's display name, and preserves the
    ``created_at desc, id desc`` order the FE renders so the loader's per-row
    order matches the list the user sees.
    """
    stmt = (
        select(
            FavoriteListItem.id,
            FavoriteListItem.broker_dealer_id,
            BrokerDealer.name.label("broker_dealer_name"),
            FavoriteListItem.advisor_id,
            InvestmentAdvisor.name.label("advisor_name"),
            FavoriteListItem.institutional_investor_id,
            InstitutionalInvestor.name.label("institutional_investor_name"),
            FavoriteListItem.reporting_owner_id,
            ReportingOwner.name.label("reporting_owner_name"),
            FavoriteListItem.bank_id,
            Bank.name.label("bank_name"),
        )
        .outerjoin(BrokerDealer, BrokerDealer.id == FavoriteListItem.broker_dealer_id)
        .outerjoin(InvestmentAdvisor, InvestmentAdvisor.id == FavoriteListItem.advisor_id)
        .outerjoin(
            InstitutionalInvestor,
            InstitutionalInvestor.id == FavoriteListItem.institutional_investor_id,
        )
        .outerjoin(ReportingOwner, ReportingOwner.id == FavoriteListItem.reporting_owner_id)
        .outerjoin(Bank, Bank.id == FavoriteListItem.bank_id)
        .where(FavoriteListItem.list_id == list_id)
        .order_by(FavoriteListItem.created_at.desc(), FavoriteListItem.id.desc())
    )
    rows = (await db.execute(stmt)).all()

    items: list[EnrichItem] = []
    for (
        item_id,
        bd_id,
        bd_name,
        advisor_id,
        advisor_name,
        investor_id,
        investor_name,
        reporting_owner_id,
        reporting_owner_name,
        bank_id,
        bank_name,
    ) in rows:
        if bd_id is not None:
            entity_type, entity_id, name = "broker_dealer", bd_id, bd_name
        elif advisor_id is not None:
            entity_type, entity_id, name = "advisor", advisor_id, advisor_name
        elif investor_id is not None:
            entity_type, entity_id, name = (
                "institutional_investor",
                investor_id,
                investor_name,
            )
        elif bank_id is not None:
            entity_type, entity_id, name = "bank", bank_id, bank_name
        else:
            entity_type, entity_id, name = (
                "reporting_owner",
                reporting_owner_id,
                reporting_owner_name,
            )
        items.append(
            EnrichItem(
                item_id=item_id,
                entity_type=entity_type,
                entity_id=entity_id,
                name=name or f"{entity_type} #{entity_id}",
            )
        )
    return items


# Namespace int4 for the per-(user, list) bulk-enrich dispatch advisory lock,
# so its key space can't collide with any other advisory lock the app might add.
_ENRICH_DISPATCH_LOCK_NS = 0x464C454E  # "FLEN" (favorite-list-enrich)


async def _acquire_enrich_dispatch_lock(
    db: AsyncSession, user_id: str, list_id: int
) -> None:
    """Serialize concurrent bulk-enrich dispatches for one (user, list).

    A transaction-scoped Postgres advisory lock closes the check-then-insert
    TOCTOU: without it, two simultaneous clicks both pass the in-flight SELECT
    (each seeing no run yet) and each INSERT a parent run, spawning two workers
    that double-bill every provider. With it, the second dispatch blocks until
    the first commits, then its in-flight SELECT sees the just-created run
    (READ COMMITTED) and attaches instead of duplicating. Auto-released at
    transaction end (the commit/rollback below). No-op on non-Postgres, where
    the concurrency path isn't exercised.
    """
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))").bindparams(
            ns=_ENRICH_DISPATCH_LOCK_NS,
            key=f"{user_id}:{list_id}",
        )
    )


@router.post("/{list_id}/enrich", response_model=FavoriteListEnrichResponse)
async def enrich_favorite_list(
    background_tasks: BackgroundTasks,
    response: Response,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListEnrichResponse:
    """Bulk-enrich every firm in a list owned by the calling user.

    Owner-scoped (404 if the list is missing or belongs to another user, same
    opaque shape as the rest of this router). Enumerates all items across the
    five entity types and dispatches ONE resilient background job that
    enriches each firm sequentially — gap-filling per entity type and always
    ending with email DISCOVERY (never SMTP-verify). Returns 202 with a
    ``run_id`` the FE polls at ``GET /api/v1/pipeline/run/{run_id}``; the run's
    ``notes`` JSON carries the live "which firm are we on" pointer so the
    loader survives a tab close.

    Per-item feature gates (admin bypass): MASTER_LIST for broker-dealers,
    INVESTMENT_ADVISORS for advisors, BANKS for banks, EMAIL_EXTRACTOR for the
    email step — a blocked gate skips just that item/step with a noted reason
    rather than 403-ing the whole run. ``institutional_investor`` and
    ``reporting_owner`` items are skipped with a reason (no enrichment flow
    yet). Attaches to an in-flight run for this (user, list) rather than
    queueing a duplicate.
    """
    await _get_owned_list(db, list_id, current_user.id)

    items = await _load_all_items(db, list_id)
    if not items:
        return FavoriteListEnrichResponse(
            run_id=None,
            status="skipped",
            list_id=list_id,
            total=0,
            reason="List is empty.",
        )

    # Close the dispatch TOCTOU: hold a per-(user, list) advisory lock across
    # the in-flight check + parent-run insert so two concurrent clicks can't
    # both start a run (double-spend). Released when this request commits below.
    await _acquire_enrich_dispatch_lock(db, current_user.id, list_id)

    trigger_source = f"favorite_list_enrich:{current_user.email}"
    # Match the id as a COMPLETE JSON token — delimited by ',' or '}' — so one
    # list id can never prefix-match another (list 12 vs list 123's run).
    list_marker = f'"list_id": {list_id}'
    list_run_marker_match = or_(
        PipelineRun.notes.ilike(f"%{list_marker},%"),
        PipelineRun.notes.ilike(f"%{list_marker}" + "}%"),
    )
    in_flight_stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == FAVORITE_LIST_ENRICH_PIPELINE_NAME)
        .where(PipelineRun.trigger_source == trigger_source)
        .where(PipelineRun.status.in_(("queued", "running")))
        .where(list_run_marker_match)
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    in_flight = (await db.execute(in_flight_stmt)).scalar_one_or_none()
    # Heartbeat-based liveness (NOT started_at): a bulk run legitimately runs
    # past STALE_REFRESH_RUN_AGE, so it must keep reading as in-flight as long
    # as its worker is still advancing (fresh notes.last_progress_at). Only a
    # run whose heartbeat has gone quiet for the threshold is treated as an
    # orphan and superseded by a fresh run.
    if in_flight is not None and not is_run_stale(in_flight):
        response.status_code = status.HTTP_202_ACCEPTED
        return FavoriteListEnrichResponse(
            run_id=in_flight.id,
            status="in_flight",
            list_id=list_id,
            total=len(items),
            reason="A bulk-enrich run is already in flight for this list.",
        )

    parent_run = PipelineRun(
        pipeline_name=FAVORITE_LIST_ENRICH_PIPELINE_NAME,
        trigger_source=trigger_source,
        status="queued",
        total_items=len(items),
        processed_items=0,
        success_count=0,
        failure_count=0,
        notes=json.dumps(
            {
                "list_id": list_id,
                "stage": "queued",
                "total": len(items),
                "current_index": None,
                "current_name": None,
                "current_entity_type": None,
                # Seed the heartbeat at queue time so the in-flight guard and
                # reaper have a liveness signal even before the worker's first
                # progress write lands.
                "last_progress_at": datetime.now(timezone.utc).isoformat(),
                "items": [],
            }
        ),
    )
    db.add(parent_run)
    await db.commit()
    await db.refresh(parent_run)

    background_tasks.add_task(
        run_favorite_list_enrich_background,
        parent_run.id,
        list_id,
        items,
        user=current_user,
    )

    response.status_code = status.HTTP_202_ACCEPTED
    return FavoriteListEnrichResponse(
        run_id=parent_run.id,
        status=parent_run.status,
        list_id=list_id,
        total=len(items),
        reason=None,
    )


@router.post("", response_model=FavoriteListResponse, status_code=201)
async def create_favorite_list(
    payload: FavoriteListCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListResponse:
    """Create a new (non-default) list owned by the calling user."""
    favorite_list = FavoriteList(
        user_id=current_user.id,
        name=payload.name,
        is_default=False,
    )
    db.add(favorite_list)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_DUPLICATE_NAME_DETAIL)
    await db.refresh(favorite_list)
    return FavoriteListResponse(
        id=favorite_list.id,
        name=favorite_list.name,
        is_default=favorite_list.is_default,
        item_count=0,
        created_at=favorite_list.created_at,
    )


@router.put("/{list_id}", response_model=FavoriteListResponse)
async def update_favorite_list(
    payload: FavoriteListUpdate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListResponse:
    """Rename a non-default list owned by the calling user."""
    favorite_list = await _get_owned_list(db, list_id, current_user.id)
    if favorite_list.is_default:
        raise HTTPException(
            status_code=400, detail="The default list cannot be renamed"
        )

    favorite_list.name = payload.name
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_DUPLICATE_NAME_DETAIL)
    await db.refresh(favorite_list)

    count_stmt = select(func.count(FavoriteListItem.id)).where(
        FavoriteListItem.list_id == favorite_list.id
    )
    item_count = int((await db.execute(count_stmt)).scalar_one())
    return FavoriteListResponse(
        id=favorite_list.id,
        name=favorite_list.name,
        is_default=favorite_list.is_default,
        item_count=item_count,
        created_at=favorite_list.created_at,
    )


@router.delete("/{list_id}", status_code=204)
async def delete_favorite_list(
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Delete a non-default list (cascades items via FK)."""
    favorite_list = await _get_owned_list(db, list_id, current_user.id)
    if favorite_list.is_default:
        raise HTTPException(
            status_code=400, detail="The default list cannot be deleted"
        )
    await db.delete(favorite_list)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{list_id}/items", response_model=FavoriteListItemResponse)
async def add_item_to_favorite_list(
    payload: FavoriteListItemCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListItemResponse:
    """Add a broker-dealer to a list owned by the calling user.

    Idempotent — re-POSTing the same ``broker_dealer_id`` returns the
    existing row instead of raising on the unique constraint, so the FE
    can fire the same call twice (double-click, retry) without a 500.
    """
    await _get_owned_list(db, list_id, current_user.id)

    bd_check = await db.execute(
        select(BrokerDealer.id, BrokerDealer.name).where(
            BrokerDealer.id == payload.broker_dealer_id
        )
    )
    bd_row = bd_check.first()
    if bd_row is None:
        raise HTTPException(status_code=404, detail="Firm not found")
    bd_id, bd_name = bd_row

    upsert = (
        pg_insert(FavoriteListItem)
        .values(list_id=list_id, broker_dealer_id=bd_id)
        .on_conflict_do_nothing(
            index_elements=["list_id", "broker_dealer_id"]
        )
        .returning(FavoriteListItem.id, FavoriteListItem.created_at)
    )
    inserted = (await db.execute(upsert)).first()
    if inserted is None:
        existing = await db.execute(
            select(FavoriteListItem.created_at).where(
                FavoriteListItem.list_id == list_id,
                FavoriteListItem.broker_dealer_id == bd_id,
            )
        )
        added_at = existing.scalar_one()
    else:
        added_at = inserted[1]
    await db.commit()

    return FavoriteListItemResponse(
        entity_type="broker_dealer",
        broker_dealer_id=bd_id,
        broker_dealer_name=bd_name,
        added_at=added_at,
    )


@router.post(
    "/{list_id}/items/batch",
    response_model=FavoriteListItemBatchResponse,
)
async def batch_add_items_to_favorite_list(
    payload: FavoriteListItemBatchCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListItemBatchResponse:
    """Add many broker-dealers to a list in one transaction.

    Idempotent — already-present ids land in ``skipped_existing``; non-existent
    firm ids land in ``skipped_unknown`` rather than aborting the batch. Capped
    at 200 ids/call by the request schema. ``advisor_id`` is set explicitly so
    the polymorphic XOR check (``ck_favorite_list_item_exactly_one_target``)
    is satisfied.
    """
    await _get_owned_list(db, list_id, current_user.id)

    requested_ids = payload.broker_dealer_ids
    known_rows = await db.execute(
        select(BrokerDealer.id).where(BrokerDealer.id.in_(requested_ids))
    )
    known_ids = {row[0] for row in known_rows}
    skipped_unknown = [bd_id for bd_id in requested_ids if bd_id not in known_ids]

    added_count = 0
    if known_ids:
        upsert = (
            pg_insert(FavoriteListItem)
            .values(
                [
                    {
                        "list_id": list_id,
                        "broker_dealer_id": bd_id,
                        "advisor_id": None,
                    }
                    for bd_id in known_ids
                ]
            )
            .on_conflict_do_nothing(index_elements=["list_id", "broker_dealer_id"])
            .returning(FavoriteListItem.id)
        )
        result = await db.execute(upsert)
        added_count = len(result.fetchall())

    await db.commit()
    return FavoriteListItemBatchResponse(
        added=added_count,
        skipped_existing=len(known_ids) - added_count,
        skipped_unknown=skipped_unknown,
    )


@router.delete("/{list_id}/items/{broker_dealer_id}", status_code=204)
async def remove_item_from_favorite_list(
    list_id: int = Path(..., ge=1),
    broker_dealer_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Remove a firm from the list. 404 if it wasn't in the list."""
    await _get_owned_list(db, list_id, current_user.id)

    result = await db.execute(
        delete(FavoriteListItem).where(
            FavoriteListItem.list_id == list_id,
            FavoriteListItem.broker_dealer_id == broker_dealer_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="favorite_list_item_not_found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Investment-advisor parallels ──────────────────────────────────────────
# Same shape as the broker-dealer endpoints above, but they write/delete
# rows where ``advisor_id`` is set and ``broker_dealer_id IS NULL``. The
# XOR check constraint (``ck_favorite_list_item_exactly_one_target``,
# migration 0031) is the DB-side backstop; the explicit ``broker_dealer_id
# = None`` on inserts satisfies it.


@router.post(
    "/{list_id}/advisor-items",
    response_model=FavoriteListAdvisorItemResponse,
)
async def add_advisor_to_favorite_list(
    payload: FavoriteListAdvisorItemCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListAdvisorItemResponse:
    """Add an investment advisor to a list owned by the calling user.

    Idempotent — re-POSTing the same ``advisor_id`` returns the existing
    row's ``added_at`` instead of raising on the unique constraint.
    """
    await _get_owned_list(db, list_id, current_user.id)

    advisor_check = await db.execute(
        select(InvestmentAdvisor.id, InvestmentAdvisor.name).where(
            InvestmentAdvisor.id == payload.advisor_id
        )
    )
    advisor_row = advisor_check.first()
    if advisor_row is None:
        raise HTTPException(status_code=404, detail="Advisor not found")
    advisor_id, advisor_name = advisor_row

    # uq_favorite_list_item_list_advisor is a PARTIAL unique index
    # (WHERE advisor_id IS NOT NULL) from migration 0031. ON CONFLICT
    # needs the matching index_where predicate to bind to the right index.
    upsert = (
        pg_insert(FavoriteListItem)
        .values(list_id=list_id, advisor_id=advisor_id, broker_dealer_id=None)
        .on_conflict_do_nothing(
            index_elements=["list_id", "advisor_id"],
            index_where=text("advisor_id IS NOT NULL"),
        )
        .returning(FavoriteListItem.id, FavoriteListItem.created_at)
    )
    inserted = (await db.execute(upsert)).first()
    if inserted is None:
        existing = await db.execute(
            select(FavoriteListItem.created_at).where(
                FavoriteListItem.list_id == list_id,
                FavoriteListItem.advisor_id == advisor_id,
            )
        )
        added_at = existing.scalar_one()
    else:
        added_at = inserted[1]
    await db.commit()

    return FavoriteListAdvisorItemResponse(
        advisor_id=advisor_id,
        advisor_name=advisor_name,
        added_at=added_at,
    )


@router.post(
    "/{list_id}/advisor-items/batch",
    response_model=FavoriteListItemBatchResponse,
)
async def batch_add_advisors_to_favorite_list(
    payload: FavoriteListAdvisorItemBatchCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListItemBatchResponse:
    """Add many investment advisors to a list in one transaction.

    Mirrors the BD batch endpoint: idempotent, with ``skipped_existing`` /
    ``skipped_unknown`` accounting. ``broker_dealer_id`` is set explicitly
    to ``None`` so the XOR check constraint is satisfied.
    """
    await _get_owned_list(db, list_id, current_user.id)

    requested_ids = payload.advisor_ids
    known_rows = await db.execute(
        select(InvestmentAdvisor.id).where(InvestmentAdvisor.id.in_(requested_ids))
    )
    known_ids = {row[0] for row in known_rows}
    skipped_unknown = [aid for aid in requested_ids if aid not in known_ids]

    added_count = 0
    if known_ids:
        # See comment on the single-advisor upsert above re: partial index.
        upsert = (
            pg_insert(FavoriteListItem)
            .values(
                [
                    {
                        "list_id": list_id,
                        "broker_dealer_id": None,
                        "advisor_id": aid,
                    }
                    for aid in known_ids
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["list_id", "advisor_id"],
                index_where=text("advisor_id IS NOT NULL"),
            )
            .returning(FavoriteListItem.id)
        )
        result = await db.execute(upsert)
        added_count = len(result.fetchall())

    await db.commit()
    return FavoriteListItemBatchResponse(
        added=added_count,
        skipped_existing=len(known_ids) - added_count,
        skipped_unknown=skipped_unknown,
    )


@router.delete(
    "/{list_id}/advisor-items/{advisor_id}",
    status_code=204,
)
async def remove_advisor_from_favorite_list(
    list_id: int = Path(..., ge=1),
    advisor_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Remove an advisor from the list. 404 if it wasn't in the list."""
    await _get_owned_list(db, list_id, current_user.id)

    result = await db.execute(
        delete(FavoriteListItem).where(
            FavoriteListItem.list_id == list_id,
            FavoriteListItem.advisor_id == advisor_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="favorite_list_item_not_found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Institutional-investor parallels ──────────────────────────────────────
# Same shape as the BD + advisor endpoints above, but they write/delete
# rows where ``institutional_investor_id`` is set and the other two FK
# columns are NULL. The 3-way XOR check constraint
# (``ck_favorite_list_item_exactly_one_target``, rewritten in migration
# 0045) is the DB-side backstop; the explicit ``broker_dealer_id = None``
# and ``advisor_id = None`` on inserts satisfies it.


@router.post(
    "/{list_id}/investor-items",
    response_model=FavoriteListInvestorItemResponse,
)
async def add_investor_to_favorite_list(
    payload: FavoriteListInvestorItemCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListInvestorItemResponse:
    """Add an institutional investor to a list owned by the calling user.

    Idempotent -- re-POSTing the same id returns the existing row's
    ``added_at`` instead of raising on the partial unique index.
    """
    await _get_owned_list(db, list_id, current_user.id)

    investor_check = await db.execute(
        select(InstitutionalInvestor.id, InstitutionalInvestor.name).where(
            InstitutionalInvestor.id == payload.institutional_investor_id
        )
    )
    investor_row = investor_check.first()
    if investor_row is None:
        raise HTTPException(status_code=404, detail="Investor not found")
    investor_id, investor_name = investor_row

    # uq_favorite_list_item_list_investor is a PARTIAL unique index
    # (WHERE institutional_investor_id IS NOT NULL) from migration 0045.
    # ON CONFLICT needs the matching index_where predicate to bind to
    # the right index.
    upsert = (
        pg_insert(FavoriteListItem)
        .values(
            list_id=list_id,
            institutional_investor_id=investor_id,
            broker_dealer_id=None,
            advisor_id=None,
        )
        .on_conflict_do_nothing(
            index_elements=["list_id", "institutional_investor_id"],
            index_where=text("institutional_investor_id IS NOT NULL"),
        )
        .returning(FavoriteListItem.id, FavoriteListItem.created_at)
    )
    inserted = (await db.execute(upsert)).first()
    if inserted is None:
        existing = await db.execute(
            select(FavoriteListItem.created_at).where(
                FavoriteListItem.list_id == list_id,
                FavoriteListItem.institutional_investor_id == investor_id,
            )
        )
        added_at = existing.scalar_one()
    else:
        added_at = inserted[1]
    await db.commit()

    return FavoriteListInvestorItemResponse(
        institutional_investor_id=investor_id,
        institutional_investor_name=investor_name,
        added_at=added_at,
    )


@router.post(
    "/{list_id}/investor-items/batch",
    response_model=FavoriteListItemBatchResponse,
)
async def batch_add_investors_to_favorite_list(
    payload: FavoriteListInvestorItemBatchCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListItemBatchResponse:
    """Add many institutional investors to a list in one transaction.

    Mirrors the BD + advisor batch endpoints: idempotent, with
    ``skipped_existing`` / ``skipped_unknown`` accounting.
    """
    await _get_owned_list(db, list_id, current_user.id)

    requested_ids = payload.institutional_investor_ids
    known_rows = await db.execute(
        select(InstitutionalInvestor.id).where(
            InstitutionalInvestor.id.in_(requested_ids)
        )
    )
    known_ids = {row[0] for row in known_rows}
    skipped_unknown = [iid for iid in requested_ids if iid not in known_ids]

    added_count = 0
    if known_ids:
        upsert = (
            pg_insert(FavoriteListItem)
            .values(
                [
                    {
                        "list_id": list_id,
                        "broker_dealer_id": None,
                        "advisor_id": None,
                        "institutional_investor_id": iid,
                    }
                    for iid in known_ids
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["list_id", "institutional_investor_id"],
                index_where=text("institutional_investor_id IS NOT NULL"),
            )
            .returning(FavoriteListItem.id)
        )
        result = await db.execute(upsert)
        added_count = len(result.fetchall())

    await db.commit()
    return FavoriteListItemBatchResponse(
        added=added_count,
        skipped_existing=len(known_ids) - added_count,
        skipped_unknown=skipped_unknown,
    )


@router.delete(
    "/{list_id}/investor-items/{investor_id}",
    status_code=204,
)
async def remove_investor_from_favorite_list(
    list_id: int = Path(..., ge=1),
    investor_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Remove an institutional investor from the list."""
    await _get_owned_list(db, list_id, current_user.id)

    result = await db.execute(
        delete(FavoriteListItem).where(
            FavoriteListItem.list_id == list_id,
            FavoriteListItem.institutional_investor_id == investor_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="favorite_list_item_not_found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Form 4 reporting-owner (insider) parallels ────────────────────────────
# Unlike the other three types, reporting owners are addressed by CIK
# (string) rather than a surrogate id: the /investors feed only carries
# the owner's CIK, and the ``reporting_owners`` row is lazy-created on
# first favorite (new Form 4 filers appear continuously, so a CIK may not
# yet have a row even though the migration backfilled history). The add
# path resolves the canonical name from Form 4 history, upserts the owner,
# then pins it -- with ``broker_dealer_id``/``advisor_id``/
# ``institutional_investor_id`` left NULL to satisfy the 4-way XOR check
# (``ck_favorite_list_item_exactly_one_target``, migration 0055).


@router.post(
    "/{list_id}/reporting-owner-items",
    response_model=FavoriteListReportingOwnerItemResponse,
)
async def add_reporting_owner_to_favorite_list(
    payload: FavoriteListReportingOwnerItemCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListReportingOwnerItemResponse:
    """Add a Form 4 reporting owner (insider) to a list, by CIK.

    Lazy-creates the ``reporting_owners`` row when the CIK is seen for the
    first time, resolving its canonical name from the most-recent Form 4
    filing. Idempotent -- re-POSTing the same CIK returns the existing
    pinned row's ``added_at`` instead of raising on the partial unique
    index. 404 if the CIK has no Form 4 history (so it isn't a real
    insider the FE could be showing).
    """
    await _get_owned_list(db, list_id, current_user.id)

    cik = payload.cik
    # Canonical name = most-recent Form 4 name for this CIK. The FE only
    # ever sends a CIK it's currently displaying, so a missing row means
    # the CIK isn't a real Form 4 filer.
    name = (
        await db.execute(
            select(Form4Transaction.reporting_owner_name)
            .where(Form4Transaction.reporting_owner_cik == cik)
            .order_by(Form4Transaction.filed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if name is None:
        raise HTTPException(status_code=404, detail="Reporting owner not found")

    # Upsert the owner by CIK. DO UPDATE SET cik = excluded.cik is a no-op
    # that lets RETURNING hand back the id whether the row was just
    # inserted or already existed (and keeps two concurrent first-favorites
    # of the same new CIK from racing). The existing name is preserved --
    # we don't clobber it with each favorite.
    owner_insert = pg_insert(ReportingOwner).values(cik=cik, name=name)
    owner_insert = owner_insert.on_conflict_do_update(
        index_elements=["cik"],
        set_={"cik": owner_insert.excluded.cik},
    ).returning(ReportingOwner.id, ReportingOwner.name)
    reporting_owner_id, reporting_owner_name = (
        await db.execute(owner_insert)
    ).one()

    # uq_favorite_list_item_list_reporting_owner is a PARTIAL unique index
    # (WHERE reporting_owner_id IS NOT NULL) from migration 0055. ON
    # CONFLICT needs the matching index_where predicate to bind to it.
    upsert = (
        pg_insert(FavoriteListItem)
        .values(
            list_id=list_id,
            reporting_owner_id=reporting_owner_id,
            broker_dealer_id=None,
            advisor_id=None,
            institutional_investor_id=None,
        )
        .on_conflict_do_nothing(
            index_elements=["list_id", "reporting_owner_id"],
            index_where=text("reporting_owner_id IS NOT NULL"),
        )
        .returning(FavoriteListItem.id, FavoriteListItem.created_at)
    )
    inserted = (await db.execute(upsert)).first()
    if inserted is None:
        existing = await db.execute(
            select(FavoriteListItem.created_at).where(
                FavoriteListItem.list_id == list_id,
                FavoriteListItem.reporting_owner_id == reporting_owner_id,
            )
        )
        added_at = existing.scalar_one()
    else:
        added_at = inserted[1]
    await db.commit()

    return FavoriteListReportingOwnerItemResponse(
        reporting_owner_id=reporting_owner_id,
        reporting_owner_name=reporting_owner_name,
        added_at=added_at,
    )


@router.delete(
    "/{list_id}/reporting-owner-items/{reporting_owner_id}",
    status_code=204,
)
async def remove_reporting_owner_from_favorite_list(
    list_id: int = Path(..., ge=1),
    reporting_owner_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Remove a reporting owner from the list. 404 if it wasn't in the list."""
    await _get_owned_list(db, list_id, current_user.id)

    result = await db.execute(
        delete(FavoriteListItem).where(
            FavoriteListItem.list_id == list_id,
            FavoriteListItem.reporting_owner_id == reporting_owner_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="favorite_list_item_not_found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Bank parallels ─────────────────────────────────────────────────────────
# Same shape as the BD + advisor + investor endpoints above, but they
# write/delete rows where ``bank_id`` is set and the other four FK columns
# are NULL. The 5-way XOR check constraint
# (``ck_favorite_list_item_exactly_one_target``, rewritten in migration
# 20260708_0001) is the DB-side backstop; the explicit ``None`` on the
# sibling FKs on inserts satisfies it.


@router.post(
    "/{list_id}/bank-items",
    response_model=FavoriteListBankItemResponse,
)
async def add_bank_to_favorite_list(
    payload: FavoriteListBankItemCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListBankItemResponse:
    """Add a bank to a list owned by the calling user.

    Idempotent — re-POSTing the same ``bank_id`` returns the existing
    row's ``added_at`` instead of raising on the partial unique index.
    """
    await _get_owned_list(db, list_id, current_user.id)

    bank_check = await db.execute(
        select(Bank.id, Bank.name).where(Bank.id == payload.bank_id)
    )
    bank_row = bank_check.first()
    if bank_row is None:
        raise HTTPException(status_code=404, detail="Bank not found")
    bank_id, bank_name = bank_row

    # uq_favorite_list_item_list_bank is a PARTIAL unique index
    # (WHERE bank_id IS NOT NULL) from migration 20260708_0001. ON CONFLICT
    # needs the matching index_where predicate to bind to the right index.
    upsert = (
        pg_insert(FavoriteListItem)
        .values(
            list_id=list_id,
            bank_id=bank_id,
            broker_dealer_id=None,
            advisor_id=None,
            institutional_investor_id=None,
            reporting_owner_id=None,
        )
        .on_conflict_do_nothing(
            index_elements=["list_id", "bank_id"],
            index_where=text("bank_id IS NOT NULL"),
        )
        .returning(FavoriteListItem.id, FavoriteListItem.created_at)
    )
    inserted = (await db.execute(upsert)).first()
    if inserted is None:
        existing = await db.execute(
            select(FavoriteListItem.created_at).where(
                FavoriteListItem.list_id == list_id,
                FavoriteListItem.bank_id == bank_id,
            )
        )
        added_at = existing.scalar_one()
    else:
        added_at = inserted[1]
    await db.commit()

    return FavoriteListBankItemResponse(
        bank_id=bank_id,
        bank_name=bank_name,
        added_at=added_at,
    )


@router.post(
    "/{list_id}/bank-items/batch",
    response_model=FavoriteListItemBatchResponse,
)
async def batch_add_banks_to_favorite_list(
    payload: FavoriteListBankItemBatchCreate,
    list_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteListItemBatchResponse:
    """Add many banks to a list in one transaction.

    Mirrors the BD + advisor + investor batch endpoints: idempotent, with
    ``skipped_existing`` / ``skipped_unknown`` accounting.
    """
    await _get_owned_list(db, list_id, current_user.id)

    requested_ids = payload.bank_ids
    known_rows = await db.execute(
        select(Bank.id).where(Bank.id.in_(requested_ids))
    )
    known_ids = {row[0] for row in known_rows}
    skipped_unknown = [bank_id for bank_id in requested_ids if bank_id not in known_ids]

    added_count = 0
    if known_ids:
        # See comment on the single-bank upsert above re: partial index.
        upsert = (
            pg_insert(FavoriteListItem)
            .values(
                [
                    {
                        "list_id": list_id,
                        "broker_dealer_id": None,
                        "advisor_id": None,
                        "institutional_investor_id": None,
                        "reporting_owner_id": None,
                        "bank_id": bank_id,
                    }
                    for bank_id in known_ids
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["list_id", "bank_id"],
                index_where=text("bank_id IS NOT NULL"),
            )
            .returning(FavoriteListItem.id)
        )
        result = await db.execute(upsert)
        added_count = len(result.fetchall())

    await db.commit()
    return FavoriteListItemBatchResponse(
        added=added_count,
        skipped_existing=len(known_ids) - added_count,
        skipped_unknown=skipped_unknown,
    )


@router.delete(
    "/{list_id}/bank-items/{bank_id}",
    status_code=204,
)
async def remove_bank_from_favorite_list(
    list_id: int = Path(..., ge=1),
    bank_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Remove a bank from the list. 404 if it wasn't in the list."""
    await _get_owned_list(db, list_id, current_user.id)

    result = await db.execute(
        delete(FavoriteListItem).where(
            FavoriteListItem.list_id == list_id,
            FavoriteListItem.bank_id == bank_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="favorite_list_item_not_found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
