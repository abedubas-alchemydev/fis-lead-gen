"""Pydantic schemas for the bank-charter vertical (``GET /api/v1/banks*``).

Mirrors ``schemas/broker_dealer.py``: a flat list item validated straight
off the ORM row (``from_attributes``), a meta envelope, and a detail shape
that extends the list item with the OCC application-event timeline and the
official source links the FE renders on the detail page.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator


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
    """One person extracted from a charter-application public-portion PDF.

    The banks sibling of the BD detail's ``ExecutiveContactItem``. Written
    only by the conservative extractor (``--extract-contacts`` watcher
    phase); ``role_context`` says WHY the filing names the person
    ('contact_person' | 'organizer' | 'proposed_officer' | 'counsel'), and
    ``source_url`` + ``page_number`` + ``context_snippet`` are the receipt
    so the FE can link every row to its exact spot in the occ.gov PDF.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    title: str | None
    role_context: str
    email: str | None
    phone: str | None
    source: str
    source_url: str
    page_number: int | None
    context_snippet: str | None
    created_at: datetime


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
