"""Schemas for the Outreach Contacts page (`/outreach/contacts`).

This is a separate namespace from ``schemas/vault.py``'s outreach
schemas because the new page returns a richer person shape (phone,
LinkedIn, enriched_at) plus list-level summary counts that the existing
send-flow autocomplete doesn't need. Kept in its own module so widening
fields on this page can't accidentally leak into the send flow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


EntityKind = Literal["broker_dealer", "advisor", "institutional_investor"]


class OutreachContactsFirmRow(BaseModel):
    """One firm row in the persons-by-firm list. One row covers any of
    the three entity kinds via ``entity_kind`` + ``entity_id``."""

    entity_kind: EntityKind
    entity_id: int
    name: str
    contact_count: int
    with_email_count: int
    with_phone_count: int
    last_enriched_at: datetime | None
    last_gap_fill_attempt_at: datetime | None
    # True when a gap-fill PipelineRun for this firm is currently
    # ``queued`` or ``running``. Used by the FE to disable the row's
    # "Enrich all" button so the user doesn't double-fire while a job
    # is in flight.
    gap_fill_in_progress: bool


class OutreachContactsFirmsResponse(BaseModel):
    items: list[OutreachContactsFirmRow]
    total: int


class OutreachContactPerson(BaseModel):
    """One contact row inside an expanded firm. Widens the shape used
    by ``/firms/contacts`` (which is email-only) so the persons-by-firm
    accordion can render phone-only contacts and surface enriched_at."""

    contact_id: int
    name: str
    title: str | None
    email: str | None
    phone: str | None
    linkedin_url: str | None
    enriched_at: datetime | None


class OutreachContactsFirmDetailResponse(BaseModel):
    entity_kind: EntityKind
    entity_id: int
    entity_name: str
    items: list[OutreachContactPerson]


class GapFillFirmResponse(BaseModel):
    """202 ACCEPTED response from the unified gap-fill dispatch endpoint."""

    run_id: int | None
    status: str
    entity_kind: EntityKind
    entity_id: int
    reason: str | None = None
