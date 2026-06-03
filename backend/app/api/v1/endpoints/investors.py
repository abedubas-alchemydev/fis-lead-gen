"""Investors tab endpoints.

``GET /api/v1/investors`` — paginated, partitioned reporting-person list
sourced from ``form4_transactions``. Rows are consolidated per
``(issuer, person, ad_code)``: shares and transaction value are summed
across every Form 4 the person filed in the visibility window, and the
leader row (most recent filing) supplies the name, title, address, and
"View Form 4" pointer. Default visibility is the last 90 days (the
product brief's "three months") with the $50K floor applied per
underlying transaction at query time.

``POST /api/v1/investors/{id}/enrich`` — on-demand Apollo match for the
person identified by the leader transaction id. The match is persisted
to every ``form4_transactions`` row sharing the same
``reporting_owner_cik`` so the enrichment shows up wherever that person
appears (other issuers, the "All" tab, etc.). The response returns just
the enrichment fields; the FE merges them into the row it already has.
"""

from __future__ import annotations

from decimal import Decimal
from math import ceil
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import exists, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.reporting_owner import ReportingOwner
from app.schemas.auth import AuthenticatedUser
from app.schemas.favorite_list import FavoriteListWithMembership
from app.schemas.investors import (
    InvestorEnrichResponse,
    InvestorItem,
    InvestorListMeta,
    InvestorListResponse,
)
from app.core.feature_permissions import INVESTORS
from app.services.auth import ensure_feature, get_current_user
from app.services.form4_apollo import looks_like_entity, match_form4_person
from app.services.form4_transactions import Form4TransactionRepository
from app.services.service_models import ConsolidatedPersonRow

router = APIRouter(prefix="/investors")
repository = Form4TransactionRepository()


def _require_investors(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    ensure_feature(user, INVESTORS)
    return user


def _parse_csv_list(values: list[str] | None) -> list[str]:
    """Split repeated query params with embedded CSVs into a flat list.

    Mirrors ``investment_advisors._parse_csv_list`` so the FE can use either
    ``?state=NY&state=CA`` or ``?state=NY,CA`` interchangeably.
    """

    if not values:
        return []
    parsed: list[str] = []
    for value in values:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return parsed


def _item_from_consolidated(row: ConsolidatedPersonRow) -> InvestorItem:
    return InvestorItem(
        id=row.id,
        accession_number=row.accession_number,
        is_derivative=row.is_derivative,
        issuer_cik=row.issuer_cik,
        issuer_name=row.issuer_name,
        issuer_ticker=row.issuer_ticker,
        reporting_owner_cik=row.reporting_owner_cik,
        reporting_owner_name=row.reporting_owner_name,
        reporting_owner_title=row.reporting_owner_title,
        reporting_owner_is_director=row.reporting_owner_is_director,
        reporting_owner_is_officer=row.reporting_owner_is_officer,
        reporting_owner_is_ten_pct=row.reporting_owner_is_ten_pct,
        reporting_owner_street1=row.reporting_owner_street1,
        reporting_owner_street2=row.reporting_owner_street2,
        reporting_owner_city=row.reporting_owner_city,
        reporting_owner_state=row.reporting_owner_state,
        reporting_owner_zip=row.reporting_owner_zip,
        security_title=row.security_title,
        transaction_date=row.transaction_date,
        transaction_code=row.transaction_code,
        ad_code=row.ad_code,
        shares=row.shares,
        price_per_share=row.price_per_share,
        transaction_value=row.transaction_value,
        txn_count=row.txn_count,
        enriched_phone=row.enriched_phone,
        enriched_email=row.enriched_email,
        enriched_linkedin_url=row.enriched_linkedin_url,
        enriched_at=row.enriched_at,
        # An Apollo reveal is in flight when we have a person id but no
        # number yet; the FE gates the "phone arriving" hint on a freshness
        # window so a reveal that never delivers stops showing eventually.
        phone_pending=bool(row.apollo_person_id) and not row.enriched_phone,
        source_filing_url=row.source_filing_url,
        filed_at=row.filed_at,
        reporting_owner_id=row.reporting_owner_id,
        is_favorited=row.is_favorited,
        is_entity=looks_like_entity(row.reporting_owner_name),
    )


@router.get("", response_model=InvestorListResponse)
async def list_investors(
    tab: Literal["buyers", "sellers", "all"] = Query(default="all"),
    search: str | None = Query(default=None, alias="q"),
    state: list[str] | None = Query(default=None),
    ticker: str | None = Query(default=None),
    days: int = Query(
        default=None,  # type: ignore[assignment]
        ge=1,
        le=365,
        description="Visibility window in days. Defaults to settings.form4_default_visibility_days (90).",
    ),
    min_value: int = Query(
        default=None,  # type: ignore[assignment]
        ge=0,
        description="Minimum transaction value. Defaults to settings.form4_min_transaction_value (50000).",
    ),
    max_value: int | None = Query(
        default=None,
        ge=0,
        description="Maximum transaction value. Omit for no ceiling.",
    ),
    sort_by: str = Query(default="transaction_date"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: AuthenticatedUser = Depends(_require_investors),
    db: AsyncSession = Depends(get_db_session),
) -> InvestorListResponse:
    ad_code: Literal["A", "D"] | None
    if tab == "buyers":
        ad_code = "A"
    elif tab == "sellers":
        ad_code = "D"
    else:
        ad_code = None

    effective_days = days if days is not None else settings.form4_default_visibility_days
    effective_min_value = (
        Decimal(str(min_value))
        if min_value is not None
        else Decimal(str(settings.form4_min_transaction_value))
    )
    effective_max_value = (
        Decimal(str(max_value)) if max_value is not None else None
    )
    if effective_max_value is not None and effective_min_value > effective_max_value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_value must be less than or equal to max_value.",
        )

    rows, total = await repository.list_consolidated_persons(
        db,
        ad_code=ad_code,
        ticker=ticker,
        days=effective_days,
        min_value=effective_min_value,
        max_value=effective_max_value,
        search=search,
        states=_parse_csv_list(state),
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        limit=limit,
        user_id=current_user.id,
    )
    return InvestorListResponse(
        items=[_item_from_consolidated(row) for row in rows],
        meta=InvestorListMeta(
            page=page,
            limit=limit,
            total=total,
            total_pages=max(1, ceil(total / limit)) if limit else 1,
        ),
    )


@router.get("/states", response_model=list[str])
async def list_investor_states(
    _: AuthenticatedUser = Depends(_require_investors),
    db: AsyncSession = Depends(get_db_session),
) -> list[str]:
    """Distinct insider-state codes — fuels the State Combo on /investors."""
    return await repository.list_states(db)


@router.get(
    "/reporting-owners/{cik}/favorite-lists",
    response_model=list[FavoriteListWithMembership],
)
async def get_reporting_owner_favorite_lists(
    cik: str,
    current_user: AuthenticatedUser = Depends(_require_investors),
    db: AsyncSession = Depends(get_db_session),
) -> list[FavoriteListWithMembership]:
    """Return the caller's lists with an ``is_member`` flag for the insider.

    Insiders are addressed by CIK; the ``reporting_owners`` row is
    lazy-created on first favorite, so a CIK with no row yet simply
    reports ``is_member=False`` on every list (it can't be pinned until it
    exists). Lists are still returned so the FE list-picker renders.
    Mirror of ``GET /institutional-investors/{id}/favorite-lists``.
    """
    reporting_owner_id = (
        await db.execute(
            select(ReportingOwner.id).where(ReportingOwner.cik == cik)
        )
    ).scalar_one_or_none()

    item_count_sq = (
        select(
            FavoriteListItem.list_id.label("list_id"),
            func.count(FavoriteListItem.id).label("count"),
        )
        .group_by(FavoriteListItem.list_id)
        .subquery()
    )
    if reporting_owner_id is not None:
        is_member_expr = (
            exists()
            .where(
                FavoriteListItem.list_id == FavoriteList.id,
                FavoriteListItem.reporting_owner_id == reporting_owner_id,
            )
            .label("is_member")
        )
    else:
        is_member_expr = false().label("is_member")
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


@router.post("/{txn_id}/enrich", response_model=InvestorEnrichResponse)
async def enrich_investor(
    txn_id: int,
    _: AuthenticatedUser = Depends(_require_investors),
    db: AsyncSession = Depends(get_db_session),
) -> InvestorEnrichResponse:
    row = await repository.get(db, txn_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Investor row not found."
        )

    match = await match_form4_person(
        full_name=row.reporting_owner_name,
        issuer_name=row.issuer_name,
    )

    # Defense in depth: the FE disables the button for entity rows, but if
    # a stale tab (or a direct API caller) still POSTs, the service-layer
    # short-circuit returns ``skip_reason="entity_filer"``. We don't write
    # ``enriched_at`` for this path — it's deterministic from the name
    # (computed on every list response), so caching it would be misleading
    # and would flip the row into the "Re-enrich" state for no reason.
    if match.skip_reason == "entity_filer":
        return InvestorEnrichResponse(
            txn_id=txn_id,
            enriched_phone=None,
            enriched_email=None,
            enriched_at=None,
            matched=False,
            skip_reason="entity_filer",
        )

    _, enriched_at = await repository.attach_enrichment_by_person(
        db,
        reporting_owner_cik=row.reporting_owner_cik,
        phone=match.phone,
        email=match.email,
        linkedin_url=match.linkedin_url,
        apollo_person_id=match.apollo_person_id,
    )
    return InvestorEnrichResponse(
        txn_id=txn_id,
        enriched_phone=match.phone,
        enriched_email=match.email,
        enriched_linkedin_url=match.linkedin_url,
        enriched_at=enriched_at,
        matched=match.matched,
        # Apollo had a record and a reveal was requested, but no number in
        # the sync body — it'll arrive via the webhook. Drives the FE's
        # "phone arriving" hint right after the click.
        phone_pending=bool(match.apollo_person_id) and not match.phone,
    )
