from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Bank(Base):
    """A banking institution or pending charter application.

    One row per institution, reconciled across the two official public
    sources the watcher ingests:

    - **FDIC BankFind** (``api.fdic.gov/banks/institutions``) — the system of
      record for *opened* insured institutions. Keyed by ``fdic_cert``.
    - **OCC Corporate Applications Search** (``apps.occ.gov/CAS/api/search``,
      filingTypes=2 "New Bank Charter") — the system of record for national
      bank / federal trust *applications* (pending → approved → opened /
      withdrawn). Keyed by ``occ_control_number`` (e.g. ``2026-Charter-344521``).

    A row may start OCC-only (application received, no FDIC record yet) and
    later gain its ``fdic_cert`` when the bank opens and shows up in BankFind;
    the watcher reconciles via the OCC national-banks directory (charter
    number / CERT / RSSD) with a conservative name+state fallback. State
    charters never pass through the OCC and are FDIC-only rows.
    """

    __tablename__ = "banks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ── Identifiers (each nullable — an application has no FDIC cert yet;
    # a state-chartered bank never has OCC identifiers) ──
    fdic_cert: Mapped[str | None] = mapped_column(String(16), unique=True, index=True, nullable=True)
    fed_rssd: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    occ_charter_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # OCC CAS control number, e.g. '2026-Charter-344521'. Unique-nullable: the
    # stable key the watcher upserts OCC application rows on.
    occ_control_number: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    # ISO 17442 Legal Entity Identifier (20 chars) — stamped by the watcher's
    # reconcile phase from the OCC Institutions API; NULL for rows reconciled
    # via the keyless XLSX fallback (which doesn't carry it) and for
    # state-chartered banks that never pass through the OCC.
    lei: Mapped[str | None] = mapped_column(String(20), nullable=True)

    name: Mapped[str] = mapped_column(String(255), index=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    zip: Mapped[str | None] = mapped_column(String(16), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 'OCC' | 'STATE' — FDIC CHRTAGNT verbatim, or 'OCC' for CAS-sourced
    # applications. NULL only when a source ever omits it (schema-tolerant).
    charter_authority: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    # FDIC BKCLASS verbatim (N, NM, SM, SB, SA, OI ...). NULL for
    # applications that have no FDIC record yet.
    bkclass: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Primary federal regulator — FDIC REGAGNT verbatim (FDIC/OCC/FED ...).
    regulator: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # OCC Institutions API CharterType verbatim, e.g. 'National' /
    # 'TrustCo-National'. Descriptive only — deliberately NOT used to infer
    # digital_assets (plenty of TrustCo-National institutions, e.g. Citicorp
    # Trust Delaware, are not digital-asset businesses).
    charter_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Lifecycle: pending | approved | opened | withdrawn | rescinded.
    # Derived from the latest OCC action (Receipt→pending, Approved→approved,
    # Consummated-Effective→opened, Withdrawn→withdrawn, ...Rescinded→
    # rescinded) and forced to 'opened' by an ACTIVE FDIC record.
    charter_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)

    established_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    insured_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # OCC 'Receipt' action date — when the charter application was received.
    application_received_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Most recent OCC action date (any action) — the pending-tracker recency signal.
    last_action_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # FDIC financials as reported (BankFind ASSET/DEP are in $ thousands).
    asset: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    deposits: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    offices: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # FDIC ACTIVE flag. NULL = unknown (no FDIC record yet — OCC-only
    # applications), mirroring the tri-state sentinel convention used across
    # the BD vertical (NULL means "never evaluated", not "false").
    active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Matched on the OCC "Digital Assets Licensing Applications" page
    # (novel / de-novo digital-asset national bank charters + conversions).
    # Sticky: the watcher only ever sets this true (a bank dropping off the
    # page later — the OCC prunes decided applications — must not lose the
    # tag). Persisted flag per the client's request.
    digital_assets: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False, index=True
    )
    # Public-portion application PDF links from that page, deduped by URL:
    # [{"title": str, "url": str, "received_date": "YYYY-MM-DD"|None}, ...].
    # URLs only — the PDFs themselves are not fetched/rendered (the existing
    # ``services/_pdf_render_worker.py`` subprocess pattern stands by if a
    # future feature needs their contents).
    digital_asset_pdfs: Mapped[list | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )

    # 'fdic' | 'occ' | 'fdic+occ' — which official source(s) contributed.
    source: Mapped[str] = mapped_column(String(16), default="fdic", nullable=False)
    # Per-source freshness stamps, stamped by the watcher on every touch of a
    # row (even a no-change pass) so ops can tell "never checked" from
    # "checked, nothing new" — same sentinel convention as
    # broker_dealers.registration_checked_at.
    fdic_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occ_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    application_events: Mapped[list["BankApplicationEvent"]] = relationship(
        back_populates="bank",
        cascade="all, delete-orphan",
        order_by="BankApplicationEvent.action_date.desc(), BankApplicationEvent.id.desc()",
    )
    contacts: Mapped[list["BankContact"]] = relationship(
        back_populates="bank",
        cascade="all, delete-orphan",
        order_by="BankContact.id.asc()",
    )


class BankApplicationEvent(Base):
    """One OCC charter-application action (the CAS timeline).

    A CAS filing accretes actions over time (Receipt → Approved →
    Consummated-Effective, or Withdrawn), each with its own date. One row per
    (bank, action, action_date); the watcher upserts on that key so re-runs
    over an overlapping date window are idempotent. ``raw`` keeps the CAS
    item verbatim — CAS is official but undocumented, so unknown fields must
    survive parsing.
    """

    __tablename__ = "bank_application_events"
    __table_args__ = (
        UniqueConstraint("bank_id", "action", "action_date", name="uq_bank_application_events_bank_action_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bank_id: Mapped[int] = mapped_column(
        ForeignKey("banks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # OCC CAS 'act' verbatim: Receipt | Approved | Consummated-Effective |
    # Withdrawn | ... — deliberately not an enum; CAS is undocumented and new
    # vocabulary must land, not crash.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    action_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # CAS 'ft' verbatim ("New Bank Charter").
    filing_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Official CAS details page for this filing
    # (https://apps.occ.gov/CAS/home/details?FilingTypeID=&FilingID=&FilingSubtypeID=).
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full CAS item verbatim (ftId/fId/fstId/cn/bn/addr/cty/st/zip/cped/...).
    raw: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    bank: Mapped[Bank] = relationship(back_populates="application_events")


class BankContact(Base):
    """A person extracted from a bank's OCC charter-application PDF.

    The banks-vertical sibling of ``executive_contacts`` on the BD side.
    Rows are written exclusively by the conservative extractor
    (``services/bank_contact_extraction.py``, wired into the watcher's
    opt-in ``--extract-contacts`` phase): people are persisted only when
    the surrounding pattern in the public-portion PDF is unambiguous —
    the contact-person block, an organizers list, a "proposed <officer>"
    sentence, or counsel-of-record. Ambiguous hits are logged and skipped,
    never guessed.

    ``role_context`` records WHY the person appears in the filing
    ('contact_person' | 'organizer' | 'proposed_officer' | 'counsel');
    ``page_number`` + ``context_snippet`` keep the receipt (the raw text
    around the hit) so a user can verify any row against the source PDF
    (``source_url``, always an https occ.gov link) in one click.

    Dedupe key: ``(bank_id, name, coalesce(title,''), source)`` — a unique
    expression index (``title`` is nullable and NULL != NULL in Postgres,
    so the raw column can't participate in the key). The extractor upserts
    on the same key, so overlapping re-runs are no-ops.
    """

    __tablename__ = "bank_contacts"
    __table_args__ = (
        Index(
            "uq_bank_contacts_dedupe",
            "bank_id",
            "name",
            text("coalesce(title, '')"),
            "source",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bank_id: Mapped[int] = mapped_column(
        ForeignKey("banks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Verbatim from the filing when unambiguous (e.g. 'President and Chief
    # Executive Officer'); NULL when the filing names the person without one.
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 'contact_person' | 'organizer' | 'proposed_officer' | 'counsel' —
    # deliberately not an enum; new extraction patterns must land, not crash.
    role_context: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="application_pdf")
    # The occ.gov public-portion PDF the person was extracted from.
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    # 1-based page of the hit inside that PDF; NULL if unknown.
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Short raw text around the hit — the audit trail for the extraction.
    context_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ── Tier-2 Apollo enrichment bookkeeping (services/bank_contact_enrichment.py) ──
    # Stamped on every PAID lookup attempt that reached a decision (matched
    # OR no-match) so re-runs skip the row and never re-spend the credit.
    # NULL = never attempted. Provider errors (timeouts, 429/5xx exhaustion)
    # deliberately do NOT stamp — a transient Apollo outage must not
    # permanently mark the row as attempted.
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 'matched' | 'no_match' — outcome of the last Apollo lookup; NULL =
    # never attempted. Deliberately not an enum (the vocabulary must be able
    # to grow without a crash), mirroring role_context above.
    enrich_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    bank: Mapped[Bank] = relationship(back_populates="contacts")
