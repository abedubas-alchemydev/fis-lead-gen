"""Pydantic schemas for the bank-charter vertical (``GET /api/v1/banks*``).

Mirrors ``schemas/broker_dealer.py``: a flat list item validated straight
off the ORM row (``from_attributes``), a meta envelope, and a detail shape
that extends the list item with the OCC application-event timeline and the
official source links the FE renders on the detail page.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.schemas.contact_hits import EmailHit, PhoneHit, synthesize_contact_arrays


class BankPdfLink(BaseModel):
    """One public-portion application PDF from the OCC digital-assets page.

    Stored verbatim in ``banks.digital_asset_pdfs`` by the watcher; the
    URL points at occ.gov — the PDF itself is never proxied or rendered.
    """

    title: str | None = None
    url: str
    received_date: date | None = None


class BankListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # ── Identifiers (each nullable: an application has no FDIC cert yet; a
    # state-chartered bank never has OCC identifiers) ──
    fdic_cert: str | None
    fed_rssd: str | None
    occ_charter_number: str | None
    occ_control_number: str | None
    name: str
    address: str | None
    city: str | None
    state: str | None
    zip: str | None
    website: str | None
    # 'OCC' | 'STATE' (FDIC CHRTAGNT verbatim; 'OCC' for CAS applications).
    charter_authority: str | None
    bkclass: str | None
    regulator: str | None
    # OCC CharterType verbatim (e.g. 'National', 'TrustCo-National') — OCC
    # Institutions API enrichment stamped by the reconcile phase; NULL for
    # rows reconciled via the keyless XLSX fallback and for state charters.
    # Descriptive only, NOT a digital-assets signal. Promoted onto the list
    # item (was detail-only) so the list can show + filter on charter_type.
    charter_type: str | None = None
    # pending | approved | opened | withdrawn | rescinded
    charter_status: str
    established_date: date | None
    insured_date: date | None
    application_received_date: date | None
    last_action_date: date | None
    # FDIC BankFind ASSET/DEP as reported ($ thousands).
    asset: float | None
    deposits: float | None
    offices: int | None
    # Tri-state: NULL = no FDIC record yet (OCC-only application).
    active: bool | None
    # OCC "Digital Assets Licensing Applications" page match + its
    # public-portion application PDF links (URLs only).
    digital_assets: bool = False
    digital_asset_pdfs: list[BankPdfLink] = []
    # 'fdic' | 'occ' | 'fdic+occ' — which official source(s) contributed.
    source: str
    fdic_checked_at: datetime | None
    occ_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("digital_asset_pdfs", mode="before")
    @classmethod
    def _coerce_null_to_empty(cls, v: object) -> object:
        return v if v is not None else []


class BankListMeta(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int


class BankListResponse(BaseModel):
    items: list[BankListItem]
    meta: BankListMeta


class BankApplicationEventItem(BaseModel):
    """One OCC charter-application action (Receipt / Approved / ...).

    ``source_url`` is the official CAS details page for the filing so the
    FE can deep-link every timeline entry to its government source.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    action_date: date | None
    filing_type: str | None
    source_url: str | None
    created_at: datetime


class BankSourceLink(BaseModel):
    """An official public source page for this bank (FDIC BankFind
    institution page, OCC CAS filing details, digital-assets PDF, ...)."""

    label: str
    url: str


class BankContactItem(BaseModel):
    """One person on a bank profile — either extracted from a charter
    application PDF or surfaced by AI contact discovery.

    The banks sibling of the BD detail's ``ExecutiveContactItem``. PDF rows
    (``source='application_pdf'`` / ``'application_pdf_llm'``) carry the
    filing provenance: ``role_context`` says WHY the filing names the person
    ('contact_person' | 'organizer' | 'proposed_officer' | 'counsel'), and
    ``source_url`` + ``page_number`` + ``context_snippet`` link the row to its
    exact spot in the occ.gov PDF. Discovery rows leave those NULL and instead
    carry channel data: ``linkedin_url`` + the ``emails`` / ``phones`` arrays
    (synthesized from the scalar ``email`` / ``phone`` for rows that only wrote
    the scalars), exactly like ``AdvisorContactItem``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    title: str | None
    role_context: str | None
    email: str | None
    phone: str | None
    linkedin_url: str | None = None
    source: str
    discovery_source: str | None = None
    discovery_confidence: float | None = None
    source_url: str | None
    page_number: int | None
    context_snippet: str | None
    created_at: datetime
    emails: list[EmailHit] = []
    phones: list[PhoneHit] = []

    @field_validator("emails", "phones", mode="before")
    @classmethod
    def _coerce_contact_arrays_null_to_empty(cls, v: object) -> object:
        return v if v is not None else []

    @model_validator(mode="after")
    def _synthesize_arrays(self) -> Self:
        # Project scalar email/phone into 1-element arrays when the JSONB
        # columns are NULL, ordering emails personal-first — the single
        # read-time chokepoint shared with the BD/IA/II contact schemas.
        self.emails, self.phones = synthesize_contact_arrays(self)
        return self


class BankDetail(BankListItem):
    # ── OCC Institutions API enrichment (reconcile phase; NULL for rows
    # reconciled via the keyless XLSX fallback and for state charters) ──
    # ISO 17442 Legal Entity Identifier. (``charter_type`` lives on
    # BankListItem now — the list filters on it — and is inherited here.)
    lei: str | None = None
    # Newest-first OCC action timeline (empty for FDIC-only state charters).
    application_events: list[BankApplicationEventItem] = []
    # Assembled by the endpoint from whatever identifiers the row carries.
    source_links: list[BankSourceLink] = []
    # People from the application PDFs (empty until --extract-contacts has
    # run for this bank; detail-only, the list payload is unchanged).
    contacts: list[BankContactItem] = []


class GapFillBankResponse(BaseModel):
    """Response shape for ``POST /banks/{id}/gap-fill-contacts``.

    The banks analog of ``RefreshAdvisorResponse`` — the FE polls
    ``GET /pipeline/run/{run_id}`` for the run's terminal state exactly as it
    does for the advisor "Generate More Details" button:

    - ``run_id=int, status="queued"`` — a gap-fill run was queued.
    - ``run_id=int, status="in_flight"`` — a run was already in flight for this
      bank; the caller attaches to it (202, not 409, so the browser doesn't log
      a cosmetic console error).
    - ``run_id=None, status="skipped"`` — nothing to do (reserved; the endpoint
      always queues today since discovery works even for banks with no
      contacts).
    """

    run_id: int | None = None
    status: str
    bank_id: int
    reason: str | None = None
