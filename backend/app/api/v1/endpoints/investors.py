"""Investors tab endpoints.

``GET /api/v1/investors`` — paginated, partitioned reporting-person list
sourced from ``form4_transactions``. Default visibility is the last 90
days (the product brief's "three months") with the $50K floor applied
at query time.

``POST /api/v1/investors/{id}/enrich`` — on-demand Apollo match for a
single reporting person. Surfaces phone/email back into the row so
the FE can render them in place. ``enriched_at`` is set on every
successful Apollo round-trip even when the match comes back empty.
"""

from __future__ import annotations

from decimal import Decimal
from math import ceil
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.models.form4_transaction import Form4Transaction
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

router = APIRouter(prefix="/investors")
repository = Form4TransactionRepository()


def _require_investors(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    ensure_feature(user, INVESTORS)
    return user


def _item_from_row(row: Form4Transaction) -> InvestorItem:
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
        shares=float(row.shares) if row.shares is not None else None,
        price_per_share=(
            float(row.price_per_share) if row.price_per_share is not None else None
        ),
        transaction_value=(
            float(row.transaction_value) if row.transaction_value is not None else None
        ),
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

    rows, total = await repository.list_transactions(
        db,
        ad_code=ad_code,
        ticker=ticker,
        days=effective_days,
        min_value=effective_min_value,
        page=page,
        limit=limit,
    )
    return InvestorListResponse(
        items=[_item_from_row(row) for row in rows],
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
    updated = await repository.attach_enrichment(
        db, txn_id, phone=match.phone, email=match.email
    )
    if updated is None:
        # Race: row deleted between GET and UPDATE. Surface as 404.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Investor row not found."
        )
    return InvestorEnrichResponse(item=_item_from_row(updated), matched=match.matched)
