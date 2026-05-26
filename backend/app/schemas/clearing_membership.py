from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ClearingMembershipItem(BaseModel):
    """One clearing-agency / SRO membership with provenance, for the firm
    detail/profile response.

    Carries the directory's verbatim name + member number and how the match
    was made so a label is auditable. ``status`` is ``active`` or
    ``needs_review`` (``rejected`` rows are filtered out before
    serialization). The compact set of active agency codes also rides on the
    list rows as ``member_agencies`` — this fuller shape is profile-only.
    """

    model_config = ConfigDict(from_attributes=True)

    agency: str
    member_number: str | None = None
    member_name_raw: str
    source_file: str
    source_version: str | None = None
    match_method: str
    match_confidence: float | None = None
    status: str


class ClearingMembershipReviewRow(BaseModel):
    """One ``needs_review`` candidate joined to its firm name + which side it
    sits on. Returned by the admin review-queue endpoint. The FE groups rows
    by ``(member_name_raw, agency)`` so a human picks one candidate per
    ambiguous directory entry.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    agency: str
    member_number: str | None = None
    member_name_raw: str
    source_file: str
    source_version: str | None = None
    match_method: str
    match_confidence: float | None = None
    firm_side: str  # 'broker_dealer' | 'investment_advisor'
    firm_id: int
    firm_name: str
    created_at: datetime


class ClearingMembershipReviewListResponse(BaseModel):
    items: list[ClearingMembershipReviewRow]
    total: int


class ClearingMembershipDecisionResponse(BaseModel):
    """Response to approve/reject. Mirrors the row's new state."""

    id: int
    status: str
    match_method: str
