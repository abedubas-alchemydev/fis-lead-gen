from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Form4Transaction(Base):
    """One row per (reporting-person × Form 4 transaction) pair.

    A single Form 4 filing can list multiple ``reportingOwner`` blocks and
    multiple transactions across both ``nonDerivativeTable`` and
    ``derivativeTable``; the watcher emits one row per pair. The
    ``dedupe_key`` is keyed on ``(accession, nd|d, owner_cik, idx)`` so
    re-running the watcher over the same EFTS window is idempotent.
    """

    __tablename__ = "form4_transactions"
    __table_args__ = (
        CheckConstraint("ad_code IN ('A', 'D')", name="form4_transactions_ad_code_chk"),
        Index("ix_form4_ad_date", "ad_code", "transaction_date"),
        Index("ix_form4_ticker_date", "issuer_ticker", "transaction_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    accession_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    transaction_index: Mapped[int] = mapped_column(nullable=False)
    is_derivative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    # ── Issuer (the public company whose stock changed hands) ───────────
    issuer_cik: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    issuer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer_ticker: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)

    # ── Reporting person (the insider — our "potential investor") ────────
    reporting_owner_cik: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reporting_owner_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reporting_owner_is_director: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reporting_owner_is_officer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reporting_owner_is_ten_pct: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reporting_owner_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reporting_owner_street1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reporting_owner_street2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reporting_owner_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reporting_owner_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reporting_owner_zip: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ── Transaction ──────────────────────────────────────────────────────
    security_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Single-letter code from <transactionCoding/transactionCode>. Common
    # values: P (open-market buy), S (open-market sell), A (grant/award),
    # M (option exercise), F (tax withholding), G (gift), J (other).
    transaction_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # The binary bucket from <transactionAcquiredDisposedCode>. CHECK in
    # ('A', 'D') — this is what we partition the two lists on. Always set.
    ad_code: Mapped[str] = mapped_column(String(1), nullable=False)
    shares: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    price_per_share: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    # ``shares * price_per_share``. NULL when either input is missing
    # (which is how gift / award rows that should be excluded by the $50K
    # floor present themselves). Indexed so the value-threshold filter at
    # query time can use the index.
    transaction_value: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True, index=True
    )

    source_filing_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Optional Apollo enrichment (filled on the per-row Enrich click) ──
    enriched_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enriched_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
