"""Pydantic shapes for admin-only per-user views.

Currently only the ``GET /api/v1/users/{user_id}/saved-firms`` endpoint
lives here; future admin-user surfaces (sent outreach, visits, etc.)
should slot in alongside.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AdminUserBrief(BaseModel):
    """Identity slice returned alongside paginated admin payloads so the FE
    breadcrumb / title can use exactly the same row the BE keyed off — no
    extra round-trip to ``/user`` and no risk of a stale name."""

    id: str
    email: str
    name: str


class AdminSavedFirmListSummary(BaseModel):
    """One row in the ``lists`` summary returned by the saved-firms endpoint.

    Powers the filter-pill row at the top of the admin view (one pill per
    list, each labeled with its current ``item_count``).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_default: bool
    item_count: int


class AdminSavedFirmRow(BaseModel):
    """One row in the flat paginated firms table.

    ``item_type`` discriminates which detail page the FE should link to:
    ``broker_dealer`` → ``/master-list/{target_id}``,
    ``advisor`` → ``/advisor-list/{target_id}``.
    """

    item_type: Literal["broker_dealer", "advisor"]
    target_id: int
    target_name: str
    list_id: int
    list_name: str
    list_is_default: bool
    saved_at: datetime


class AdminUserSavedFirmsResponse(BaseModel):
    """``GET /api/v1/users/{user_id}/saved-firms`` payload."""

    items: list[AdminSavedFirmRow]
    total: int
    limit: int
    offset: int
    lists: list[AdminSavedFirmListSummary]
    user: AdminUserBrief


class AdminUserActivityRow(BaseModel):
    """One row in the unified user-activity feed.

    ``event_type`` discriminates the source and shapes the FE row glyph +
    tooltip. ``target_type`` / ``target_id`` / ``target_name`` are set
    when the activity is bound to a firm (view, save, outreach) and are
    ``None`` for login / logout. For broad-surface instrumentation rows
    (nav/search/input) and Doxie chat rows (doxie) ``target_name`` carries
    the normalized route path the event fired on. ``details`` carries
    event-specific extras (login → IP + user_agent; save → list name; view
    → visit count; outreach → send status + error code; nav/search/input →
    FE-scrubbed metadata; doxie → page path + title + message preview) —
    the FE renders a tooltip from it.
    """

    event_type: Literal[
        "login",
        "logout",
        "view",
        "save",
        "outreach",
        "nav_view",
        "nav_click",
        "link_open",
        "search_query",
        "input_used",
        "doxie",
    ]
    timestamp: datetime
    target_type: Literal["broker_dealer", "advisor", "institutional_investor"] | None = None
    target_id: int | None = None
    target_name: str | None = None
    details: dict | None = None


class AdminUserActivitiesResponse(BaseModel):
    """``GET /api/v1/users/{user_id}/activities`` payload."""

    items: list[AdminUserActivityRow]
    total: int
    limit: int
    offset: int
    user: AdminUserBrief
