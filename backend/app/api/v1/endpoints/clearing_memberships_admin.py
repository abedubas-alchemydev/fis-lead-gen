"""Admin-only review queue for clearing-agency memberships.

The directory importer routes ambiguous name matches (same normalized key
hits >1 firm) to ``status='needs_review'`` so labels never auto-apply
when we can't tell which firm the OCC/DTCC entry meant. This module is
the surface a human uses to adjudicate those rows:

* ``GET  /clearing-memberships/review`` — every needs_review row joined
  to its firm name + side, ordered so candidates for the same directory
  entry group together (member_name, agency, then by side/firm_id).
* ``POST /clearing-memberships/{id}/approve`` — flip to ``active``, and
  set ``match_method='manual'`` so a future re-import preserves the
  human decision (the importer's reconcile path explicitly skips
  ``manual`` rows).
* ``POST /clearing-memberships/{id}/reject`` — flip to ``rejected``.

Admin-gated via the same ``_ensure_admin`` pattern users_admin.py uses.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import case, func, literal, select, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.broker_dealer import BrokerDealer
from app.models.clearing_agency_membership import ClearingAgencyMembership
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.schemas.clearing_membership import (
    ClearingMembershipDecisionResponse,
    ClearingMembershipReviewListResponse,
    ClearingMembershipReviewRow,
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/clearing-memberships", tags=["clearing-memberships-admin"])


def _ensure_admin(current_user: AuthenticatedUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )


@router.get("/review", response_model=ClearingMembershipReviewListResponse)
async def list_review_queue(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ClearingMembershipReviewListResponse:
    """List every ``needs_review`` row with its firm name + side.

    Rows for the same directory entry (same ``member_name_raw + agency``)
    are returned adjacently so the FE can group them as one decision.
    """
    _ensure_admin(current_user)

    bd_q = (
        select(
            ClearingAgencyMembership.id.label("id"),
            ClearingAgencyMembership.agency.label("agency"),
            ClearingAgencyMembership.member_number.label("member_number"),
            ClearingAgencyMembership.member_name_raw.label("member_name_raw"),
            ClearingAgencyMembership.source_file.label("source_file"),
            ClearingAgencyMembership.source_version.label("source_version"),
            ClearingAgencyMembership.match_method.label("match_method"),
            ClearingAgencyMembership.match_confidence.label("match_confidence"),
            literal("broker_dealer").label("firm_side"),
            ClearingAgencyMembership.broker_dealer_id.label("firm_id"),
            BrokerDealer.name.label("firm_name"),
            ClearingAgencyMembership.created_at.label("created_at"),
        )
        .join(BrokerDealer, BrokerDealer.id == ClearingAgencyMembership.broker_dealer_id)
        .where(
            ClearingAgencyMembership.status == "needs_review",
            ClearingAgencyMembership.broker_dealer_id.is_not(None),
        )
    )
    ia_q = (
        select(
            ClearingAgencyMembership.id.label("id"),
            ClearingAgencyMembership.agency.label("agency"),
            ClearingAgencyMembership.member_number.label("member_number"),
            ClearingAgencyMembership.member_name_raw.label("member_name_raw"),
            ClearingAgencyMembership.source_file.label("source_file"),
            ClearingAgencyMembership.source_version.label("source_version"),
            ClearingAgencyMembership.match_method.label("match_method"),
            ClearingAgencyMembership.match_confidence.label("match_confidence"),
            literal("investment_advisor").label("firm_side"),
            ClearingAgencyMembership.advisor_id.label("firm_id"),
            InvestmentAdvisor.name.label("firm_name"),
            ClearingAgencyMembership.created_at.label("created_at"),
        )
        .join(InvestmentAdvisor, InvestmentAdvisor.id == ClearingAgencyMembership.advisor_id)
        .where(
            ClearingAgencyMembership.status == "needs_review",
            ClearingAgencyMembership.advisor_id.is_not(None),
        )
    )
    combined = union_all(bd_q, ia_q).subquery("combined")
    stmt = (
        select(combined)
        .order_by(
            combined.c.member_name_raw.asc(),
            combined.c.agency.asc(),
            combined.c.firm_side.asc(),
            combined.c.firm_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).mappings().all()

    total_stmt = select(func.count()).select_from(ClearingAgencyMembership).where(
        ClearingAgencyMembership.status == "needs_review"
    )
    total = (await db.execute(total_stmt)).scalar_one()

    return ClearingMembershipReviewListResponse(
        items=[ClearingMembershipReviewRow.model_validate(dict(r)) for r in rows],
        total=int(total),
    )


async def _load_or_404(db: AsyncSession, membership_id: int) -> ClearingAgencyMembership:
    row = await db.get(ClearingAgencyMembership, membership_id)
    if row is None:
        raise HTTPException(status_code=404, detail="membership_not_found")
    return row


@router.post(
    "/{membership_id}/approve", response_model=ClearingMembershipDecisionResponse
)
async def approve_membership(
    membership_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ClearingMembershipDecisionResponse:
    """Mark a ``needs_review`` row as the canonical match for its (firm,
    agency). Sets ``status='active'`` and ``match_method='manual'`` so a
    later importer re-run with ``--reconcile`` won't deactivate it.

    Also rejects any *other* ``needs_review`` siblings for the same
    (firm_side, agency, member_name_raw) so a single click cleans the
    whole ambiguity cluster — the directory entry obviously meant *this*
    firm, not the others.
    """
    _ensure_admin(current_user)
    row = await _load_or_404(db, membership_id)
    if row.status not in ("needs_review", "rejected"):
        raise HTTPException(status_code=409, detail="not_in_review_state")

    row.status = "active"
    row.match_method = "manual"

    # Reject sibling candidates (same directory entry, same firm side, *not*
    # the same firm). After this approve they're settled — they can be
    # reopened via a future re-import if the underlying state changes.
    sibling_side_col = (
        ClearingAgencyMembership.broker_dealer_id
        if row.broker_dealer_id is not None
        else ClearingAgencyMembership.advisor_id
    )
    await db.execute(
        update(ClearingAgencyMembership)
        .where(
            ClearingAgencyMembership.id != row.id,
            ClearingAgencyMembership.agency == row.agency,
            ClearingAgencyMembership.member_name_raw == row.member_name_raw,
            ClearingAgencyMembership.status == "needs_review",
            sibling_side_col.is_not(None),
        )
        .values(status="rejected")
    )
    await db.commit()
    return ClearingMembershipDecisionResponse(
        id=row.id, status=row.status, match_method=row.match_method
    )


@router.post(
    "/{membership_id}/reject", response_model=ClearingMembershipDecisionResponse
)
async def reject_membership(
    membership_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ClearingMembershipDecisionResponse:
    """Mark a ``needs_review`` row as not-this-firm (``status='rejected'``).

    Does not touch sibling candidates — the operator may approve one of
    them in a follow-up click.
    """
    _ensure_admin(current_user)
    row = await _load_or_404(db, membership_id)
    if row.status != "needs_review":
        raise HTTPException(status_code=409, detail="not_in_review_state")
    row.status = "rejected"
    await db.commit()
    return ClearingMembershipDecisionResponse(
        id=row.id, status=row.status, match_method=row.match_method
    )
