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
    # Count of Email-Extractor ``discovered_email`` rows linked to this
    # firm. A PARALLEL source from the typed contact tables -- the three
    # counts above stay typed-only; this one is kept separate so the FE
    # can render a distinct "extracted emails" pill without double-
    # counting. For institutional investors this counts through the
    # resolved ``advisor_id`` (0 when the investor has no advisor link,
    # since ``discovered_email`` carries no ``investor_id``).
    discovered_email_count: int
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


class OutreachDiscoveredEmailRow(BaseModel):
    """One Email-Extractor ``discovered_email`` row surfaced under a firm.

    A parallel source from the typed contact rows in
    :class:`OutreachContactPerson`: these come straight from the Email
    Extractor's scans (``discovered_email`` table) and carry no typed
    ``contact_id`` -- the FE wires outreach off the email address via the
    adhoc compose path. ``email`` is the discovered address that keyed the
    scan; the ``enriched_*`` fields are whatever provider enrichment
    attached afterwards (may be NULL on an unenriched row)."""

    id: int
    email: str
    enriched_name: str | None
    enriched_title: str | None
    enriched_phone: str | None
    enriched_linkedin_url: str | None
    enrichment_status: str
    source: str | None
    confidence: float | None
    created_at: datetime


class GapFillFirmResponse(BaseModel):
    """202 ACCEPTED response from the unified gap-fill dispatch endpoint."""

    run_id: int | None
    status: str
    entity_kind: EntityKind
    entity_id: int
    reason: str | None = None


# Distinct ``source`` discriminator for an email row in the email-search
# results: a typed contact row (ExecutiveContact / AdvisorContact /
# InvestorContact -- "Generate More Details" enrich output) vs. an
# Email-Extractor ``discovered_email`` row.
EmailSource = Literal["contact", "extracted"]


class EmailSearchResult(BaseModel):
    """One email/contact row returned by ``/outreach/contacts/email-search``.

    A FLAT, per-email row (not a firm row) that the FE renders when the
    user searches the contacts page by an email address. Unifies the two
    sources behind one shape: a typed contact (``source="contact"``,
    carries ``contact_id`` + any typed ``phone``) and an Email-Extractor
    ``discovered_email`` (``source="extracted"``, no ``contact_id``;
    ``phone`` is always None since the discovered shape is keyed off the
    address, not a typed channel record).

    ``entity_kind`` + ``entity_id`` + ``firm_name`` identify the owning
    firm so the FE can link the row back to its firm. ``owner_name`` /
    ``title`` come from the contact (``name`` / ``title``) or the
    discovered row (``enriched_name`` / ``enriched_title``)."""

    entity_kind: EntityKind
    entity_id: int
    firm_name: str
    owner_name: str | None
    title: str | None
    email: str
    phone: str | None
    source: EmailSource
    contact_id: int | None


class EmailSearchResponse(BaseModel):
    """Paginated wrapper for the email-search results.

    Mirrors the firms-list pagination contract: ``total`` is the full
    match count across both sources (after dedup), ``page`` / ``limit``
    echo the request, and ``has_more`` tells the FE whether another page
    exists without it recomputing from ``total``."""

    results: list[EmailSearchResult]
    total: int
    page: int
    limit: int
    has_more: bool
