"""Admin-only per-user views.

Exposes:

* ``GET /api/v1/users/{user_id}/saved-firms`` — a flat, paginated view of
  every firm a target user has bookmarked across all of their favorite
  lists (default + custom).
* ``GET /api/v1/users/{user_id}/activities`` — a unified timestamp-ordered
  feed of the user's login/logout events, firm views, saves, and
  outreach sends. UNION-ALLs ``audit_log`` + ``user_visit`` +
  ``favorite_list_item`` + ``outreach_sends`` so the FE renders one
  filterable stream per user.

The endpoints deliberately live outside ``/favorite-lists`` (which is
strictly per-caller and would otherwise grow a polluting
``?admin_for=<user_id>`` param) so admin-scoped reads and self-scoped
reads stay cleanly separated.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Text,
    cast,
    func,
    literal,
    null,
    select,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.audit_log import AuditLog
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investment_advisor import InvestmentAdvisor
from app.models.outreach_send import OutreachSend
from app.models.user_visit import UserVisit
from app.schemas.auth import AuthenticatedUser
from app.schemas.users_admin import (
    AdminSavedFirmListSummary,
    AdminSavedFirmRow,
    AdminUserActivitiesResponse,
    AdminUserActivityRow,
    AdminUserBrief,
    AdminUserSavedFirmsResponse,
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/users")


def _ensure_admin(current_user: AuthenticatedUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )


async def _load_user_or_404(db: AsyncSession, user_id: str) -> AuthUser:
    row = await db.execute(select(AuthUser).where(AuthUser.id == user_id))
    user = row.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return user


@router.get(
    "/{user_id}/saved-firms",
    response_model=AdminUserSavedFirmsResponse,
)
async def list_user_saved_firms(
    user_id: str = Path(..., min_length=1, max_length=255),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    list_id: int | None = Query(None, ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AdminUserSavedFirmsResponse:
    """Admin-only flat view of one user's saved firms (BDs + advisors).

    Joins ``favorite_list_item`` against both ``broker_dealers`` and
    ``investment_advisors`` (the item is polymorphic XOR via the
    ``ck_favorite_list_item_exactly_one_target`` DB-side check), UNION ALLs
    them into a single ordered stream, and paginates. The ``lists`` summary
    is computed separately so the FE can render the filter-pill row with
    accurate counts even when the current page is filtered.
    """
    _ensure_admin(current_user)
    user = await _load_user_or_404(db, user_id)

    bd_items = (
        select(
            literal("broker_dealer", String).label("item_type"),
            BrokerDealer.id.label("target_id"),
            BrokerDealer.name.label("target_name"),
            FavoriteList.id.label("list_id"),
            FavoriteList.name.label("list_name"),
            FavoriteList.is_default.label("list_is_default"),
            FavoriteListItem.created_at.label("saved_at"),
        )
        .select_from(FavoriteListItem)
        .join(FavoriteList, FavoriteList.id == FavoriteListItem.list_id)
        .join(BrokerDealer, BrokerDealer.id == FavoriteListItem.broker_dealer_id)
        .where(
            FavoriteList.user_id == user_id,
            FavoriteListItem.broker_dealer_id.is_not(None),
        )
    )

    advisor_items = (
        select(
            literal("advisor", String).label("item_type"),
            InvestmentAdvisor.id.label("target_id"),
            InvestmentAdvisor.name.label("target_name"),
            FavoriteList.id.label("list_id"),
            FavoriteList.name.label("list_name"),
            FavoriteList.is_default.label("list_is_default"),
            FavoriteListItem.created_at.label("saved_at"),
        )
        .select_from(FavoriteListItem)
        .join(FavoriteList, FavoriteList.id == FavoriteListItem.list_id)
        .join(
            InvestmentAdvisor,
            InvestmentAdvisor.id == FavoriteListItem.advisor_id,
        )
        .where(
            FavoriteList.user_id == user_id,
            FavoriteListItem.advisor_id.is_not(None),
        )
    )

    if list_id is not None:
        bd_items = bd_items.where(FavoriteList.id == list_id)
        advisor_items = advisor_items.where(FavoriteList.id == list_id)

    unioned = union_all(bd_items, advisor_items).subquery()

    total_stmt = select(func.count()).select_from(unioned)
    total = int((await db.execute(total_stmt)).scalar_one())

    page_stmt = (
        select(unioned)
        .order_by(unioned.c.saved_at.desc(), unioned.c.target_id.desc())
        .limit(limit)
        .offset(offset)
    )
    page_rows = (await db.execute(page_stmt)).all()

    items = [
        AdminSavedFirmRow(
            item_type=row.item_type,
            target_id=row.target_id,
            target_name=row.target_name,
            list_id=row.list_id,
            list_name=row.list_name,
            list_is_default=row.list_is_default,
            saved_at=row.saved_at,
        )
        for row in page_rows
    ]

    # Lists summary: one row per favorite_list the user owns, with the
    # combined BD + advisor item count. Unrelated to the ``list_id`` filter
    # above so the filter-pill row always shows every list.
    summary_stmt = (
        select(
            FavoriteList.id,
            FavoriteList.name,
            FavoriteList.is_default,
            func.count(FavoriteListItem.id).label("item_count"),
        )
        .select_from(FavoriteList)
        .outerjoin(FavoriteListItem, FavoriteListItem.list_id == FavoriteList.id)
        .where(FavoriteList.user_id == user_id)
        .group_by(FavoriteList.id, FavoriteList.name, FavoriteList.is_default)
        .order_by(FavoriteList.is_default.desc(), FavoriteList.created_at.asc())
    )
    summary_rows = (await db.execute(summary_stmt)).all()
    lists = [
        AdminSavedFirmListSummary(
            id=row.id,
            name=row.name,
            is_default=row.is_default,
            item_count=int(row.item_count),
        )
        for row in summary_rows
    ]

    return AdminUserSavedFirmsResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        lists=lists,
        user=AdminUserBrief(id=user.id, email=user.email, name=user.name),
    )


ActivityType = Literal["login", "view", "save", "outreach"]


def _row_details(
    *,
    event_type: str,
    audit_details: str | None,
    list_name: str | None,
    visit_count: int | None,
    send_status: str | None,
    send_error: str | None,
    send_subject: str | None,
) -> dict[str, Any] | None:
    """Build the per-row ``details`` dict the FE tooltip renders.

    Each branch in :func:`list_user_activities`'s UNION emits a small
    set of source-specific columns (the rest are NULL). This collapses
    them into one shape per ``event_type`` so the schema stays small
    while the FE keeps a single dict-shaped tooltip pipeline.
    """
    if event_type in ("login", "logout"):
        if not audit_details:
            return None
        try:
            parsed = json.loads(audit_details)
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            return None
    if event_type == "view":
        return {"visit_count": visit_count} if visit_count is not None else None
    if event_type == "save":
        return {"list_name": list_name} if list_name else None
    if event_type == "outreach":
        out: dict[str, Any] = {}
        if send_status:
            out["status"] = send_status
        if send_error:
            out["error"] = send_error
        if send_subject:
            out["subject"] = send_subject
        return out or None
    return None


@router.get(
    "/{user_id}/activities",
    response_model=AdminUserActivitiesResponse,
)
async def list_user_activities(
    user_id: str = Path(..., min_length=1, max_length=255),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    type: ActivityType | None = Query(None),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AdminUserActivitiesResponse:
    """Admin-only unified user-activity feed.

    UNION-ALLs four sources into one timestamp-ordered stream:

    * ``audit_log`` filtered to ``action IN ('login', 'logout')`` — the
      Better Auth ``session.create.after`` / ``session.delete.after``
      hooks (frontend/lib/auth.ts) insert one row per real login/logout.
    * ``user_visit`` — broker-dealer detail page views.
    * ``favorite_list_item`` — saves across all three firm types (BD,
      advisor, institutional investor), polymorphic XOR via
      ``ck_favorite_list_item_exactly_one_target``.
    * ``outreach_sends`` — outreach attempts across the same three firm
      types, polymorphic XOR via ``ck_outreach_sends_exactly_one_firm``.

    The optional ``type`` filter prunes whole branches so a "Only saves"
    view doesn't pay for the other selects.
    """
    _ensure_admin(current_user)
    user = await _load_user_or_404(db, user_id)

    # Canonical shape across branches. Each UNION arm emits these 11
    # columns in the same order; NULL-cast columns where a given source
    # has nothing to fill them with. Source-specific extras
    # (list_name / visit_count / send_* / audit_details) are stitched
    # into ``details`` per-row in Python after the query.
    null_target_type = cast(null(), String).label("target_type")
    null_target_id = cast(null(), Integer).label("target_id")
    null_target_name = cast(null(), String).label("target_name")
    null_list_name = cast(null(), String).label("list_name")
    null_visit_count = cast(null(), Integer).label("visit_count")
    null_send_status = cast(null(), String).label("send_status")
    null_send_error = cast(null(), Text).label("send_error")
    null_send_subject = cast(null(), String).label("send_subject")
    null_audit_details = cast(null(), Text).label("audit_details")

    branches: list = []

    if type is None or type == "login":
        # action is already one of "login"/"logout"; the FE collapses
        # both under the "login" filter pill.
        branches.append(
            select(
                AuditLog.action.label("event_type"),
                AuditLog.timestamp.label("ts"),
                null_target_type,
                null_target_id,
                null_target_name,
                null_list_name,
                null_visit_count,
                null_send_status,
                null_send_error,
                null_send_subject,
                AuditLog.details.label("audit_details"),
            ).where(
                AuditLog.user_id == user_id,
                AuditLog.action.in_(("login", "logout")),
            )
        )

    if type is None or type == "view":
        branches.append(
            select(
                literal("view", String).label("event_type"),
                UserVisit.last_visited_at.label("ts"),
                literal("broker_dealer", String).label("target_type"),
                UserVisit.bd_id.label("target_id"),
                BrokerDealer.name.label("target_name"),
                null_list_name,
                UserVisit.visit_count.label("visit_count"),
                null_send_status,
                null_send_error,
                null_send_subject,
                null_audit_details,
            )
            .select_from(UserVisit)
            .join(BrokerDealer, BrokerDealer.id == UserVisit.bd_id)
            .where(UserVisit.user_id == user_id)
        )

    if type is None or type == "save":
        # Polymorphic 3-way XOR: each list item has exactly one of
        # broker_dealer_id, advisor_id, institutional_investor_id set.
        # One SELECT per firm type — the WHERE on the matching FK
        # being non-null is implicit via the inner-join.
        branches.append(
            select(
                literal("save", String).label("event_type"),
                FavoriteListItem.created_at.label("ts"),
                literal("broker_dealer", String).label("target_type"),
                BrokerDealer.id.label("target_id"),
                BrokerDealer.name.label("target_name"),
                FavoriteList.name.label("list_name"),
                null_visit_count,
                null_send_status,
                null_send_error,
                null_send_subject,
                null_audit_details,
            )
            .select_from(FavoriteListItem)
            .join(FavoriteList, FavoriteList.id == FavoriteListItem.list_id)
            .join(
                BrokerDealer,
                BrokerDealer.id == FavoriteListItem.broker_dealer_id,
            )
            .where(FavoriteList.user_id == user_id)
        )
        branches.append(
            select(
                literal("save", String).label("event_type"),
                FavoriteListItem.created_at.label("ts"),
                literal("advisor", String).label("target_type"),
                InvestmentAdvisor.id.label("target_id"),
                InvestmentAdvisor.name.label("target_name"),
                FavoriteList.name.label("list_name"),
                null_visit_count,
                null_send_status,
                null_send_error,
                null_send_subject,
                null_audit_details,
            )
            .select_from(FavoriteListItem)
            .join(FavoriteList, FavoriteList.id == FavoriteListItem.list_id)
            .join(
                InvestmentAdvisor,
                InvestmentAdvisor.id == FavoriteListItem.advisor_id,
            )
            .where(FavoriteList.user_id == user_id)
        )
        branches.append(
            select(
                literal("save", String).label("event_type"),
                FavoriteListItem.created_at.label("ts"),
                literal("institutional_investor", String).label("target_type"),
                InstitutionalInvestor.id.label("target_id"),
                InstitutionalInvestor.name.label("target_name"),
                FavoriteList.name.label("list_name"),
                null_visit_count,
                null_send_status,
                null_send_error,
                null_send_subject,
                null_audit_details,
            )
            .select_from(FavoriteListItem)
            .join(FavoriteList, FavoriteList.id == FavoriteListItem.list_id)
            .join(
                InstitutionalInvestor,
                InstitutionalInvestor.id
                == FavoriteListItem.institutional_investor_id,
            )
            .where(FavoriteList.user_id == user_id)
        )

    if type is None or type == "outreach":
        branches.append(
            select(
                literal("outreach", String).label("event_type"),
                OutreachSend.sent_at.label("ts"),
                literal("broker_dealer", String).label("target_type"),
                BrokerDealer.id.label("target_id"),
                BrokerDealer.name.label("target_name"),
                null_list_name,
                null_visit_count,
                OutreachSend.status.label("send_status"),
                OutreachSend.error.label("send_error"),
                OutreachSend.subject.label("send_subject"),
                null_audit_details,
            )
            .select_from(OutreachSend)
            .join(BrokerDealer, BrokerDealer.id == OutreachSend.broker_dealer_id)
            .where(OutreachSend.user_id == user_id)
        )
        branches.append(
            select(
                literal("outreach", String).label("event_type"),
                OutreachSend.sent_at.label("ts"),
                literal("advisor", String).label("target_type"),
                InvestmentAdvisor.id.label("target_id"),
                InvestmentAdvisor.name.label("target_name"),
                null_list_name,
                null_visit_count,
                OutreachSend.status.label("send_status"),
                OutreachSend.error.label("send_error"),
                OutreachSend.subject.label("send_subject"),
                null_audit_details,
            )
            .select_from(OutreachSend)
            .join(
                InvestmentAdvisor,
                InvestmentAdvisor.id == OutreachSend.advisor_id,
            )
            .where(OutreachSend.user_id == user_id)
        )
        branches.append(
            select(
                literal("outreach", String).label("event_type"),
                OutreachSend.sent_at.label("ts"),
                literal("institutional_investor", String).label("target_type"),
                InstitutionalInvestor.id.label("target_id"),
                InstitutionalInvestor.name.label("target_name"),
                null_list_name,
                null_visit_count,
                OutreachSend.status.label("send_status"),
                OutreachSend.error.label("send_error"),
                OutreachSend.subject.label("send_subject"),
                null_audit_details,
            )
            .select_from(OutreachSend)
            .join(
                InstitutionalInvestor,
                InstitutionalInvestor.id == OutreachSend.institutional_investor_id,
            )
            .where(OutreachSend.user_id == user_id)
        )

    if not branches:
        # ``type`` was set but matched no branch — shouldn't be reachable
        # given the Literal validation, but keep the safety net.
        return AdminUserActivitiesResponse(
            items=[],
            total=0,
            limit=limit,
            offset=offset,
            user=AdminUserBrief(id=user.id, email=user.email, name=user.name),
        )

    unioned = (
        branches[0] if len(branches) == 1 else union_all(*branches)
    ).subquery()

    total_stmt = select(func.count()).select_from(unioned)
    total = int((await db.execute(total_stmt)).scalar_one())

    page_stmt = (
        select(unioned)
        .order_by(unioned.c.ts.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(page_stmt)).all()

    items: list[AdminUserActivityRow] = []
    for row in rows:
        action = row.event_type
        # Collapse "logout" rows into the same FE filter chip as "login"
        # but preserve the discriminator on the row itself so the glyph
        # + tooltip can differ.
        event_type: Literal["login", "logout", "view", "save", "outreach"]
        if action in ("login", "logout"):
            event_type = action  # type: ignore[assignment]
        elif action == "view":
            event_type = "view"
        elif action == "save":
            event_type = "save"
        else:
            event_type = "outreach"

        target_type = row.target_type
        # The Literal in the schema is narrower than the SQL String. Pass
        # through only the known set; anything else trips schema
        # validation, which is what we want.
        target_type_typed: (
            Literal["broker_dealer", "advisor", "institutional_investor"] | None
        )
        if target_type in ("broker_dealer", "advisor", "institutional_investor"):
            target_type_typed = target_type  # type: ignore[assignment]
        else:
            target_type_typed = None

        items.append(
            AdminUserActivityRow(
                event_type=event_type,
                timestamp=row.ts,
                target_type=target_type_typed,
                target_id=row.target_id,
                target_name=row.target_name,
                details=_row_details(
                    event_type=event_type,
                    audit_details=row.audit_details,
                    list_name=row.list_name,
                    visit_count=row.visit_count,
                    send_status=row.send_status,
                    send_error=row.send_error,
                    send_subject=row.send_subject,
                ),
            )
        )

    return AdminUserActivitiesResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        user=AdminUserBrief(id=user.id, email=user.email, name=user.name),
    )
