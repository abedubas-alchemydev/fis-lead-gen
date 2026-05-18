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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.investors import (
    InvestorEnrichResponse,
    InvestorItem,
    InvestorListMeta,
    InvestorListResponse,
)
from app.core.feature_permissions import INVESTORS
from app.services.auth import ensure_feature, get_current_user
from app.services.form4_apollo import match_form4_person
from app.services.form4_transactions import Form4TransactionRepository
from app.services.service_models import ConsolidatedPersonRow

router = APIRouter(prefix="/investors")
repository = Form4TransactionRepository()


def _require_investors(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    ensure_feature(user, INVESTORS)
    return user


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
        enriched_at=row.enriched_at,
        source_filing_url=row.source_filing_url,
        filed_at=row.filed_at,
    )


@router.get("", response_model=InvestorListResponse)
async def list_investors(
    tab: Literal["buyers", "sellers", "all"] = Query(default="all"),
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
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    _: AuthenticatedUser = Depends(_require_investors),
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

    rows, total = await repository.list_consolidated_persons(
        db,
        ad_code=ad_code,
        ticker=ticker,
        days=effective_days,
        min_value=effective_min_value,
        page=page,
        limit=limit,
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
    _, enriched_at = await repository.attach_enrichment_by_person(
        db,
        reporting_owner_cik=row.reporting_owner_cik,
        phone=match.phone,
        email=match.email,
    )
    return InvestorEnrichResponse(
        txn_id=txn_id,
        enriched_phone=match.phone,
        enriched_email=match.email,
        enriched_at=enriched_at,
        matched=match.matched,
    )
