"""
Storage layer targeting Neon Postgres via asyncpg.

Neon speaks the Postgres wire protocol, so the only configuration difference
from stock Postgres is the connection string and an SSL requirement.

Neon example:
  postgresql+asyncpg://user:password@ep-xxx.region.aws.neon.tech/dbname?ssl=require

Tables:
  firms_input   — your existing table of 3K firms with CRDs (READ-ONLY from this pipeline)
  firm_profile  — Domain 1 output (FINRA BrokerCheck)
  focus_report  — Domain 2 output (SEC X-17A-5); one row per filing period
  firm_record   — the merged per-firm output including YoY derivations
  parse_errors  — dead-letter queue
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import AsyncIterator, Iterable, Optional

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    Column,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..config import settings
from ..schema.models import (
    FirmProfile,
    FirmRecord,
    FocusReport,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQLAlchemy base + models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class FirmInput(Base):
    """Source table — your existing 3K firms. Read-only from the pipeline."""
    __tablename__ = "firms_input"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    firm_name: Mapped[str] = mapped_column(String(512))
    crd_number: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    sec_cik: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    status: Mapped[Optional[str]] = mapped_column(String(32))  # your own lifecycle column


class FirmProfileRow(Base):
    __tablename__ = "firm_profile"

    crd_number: Mapped[str] = mapped_column(String(32), primary_key=True)
    firm_name: Mapped[Optional[str]] = mapped_column(String(512))
    sec_number: Mapped[Optional[str]] = mapped_column(String(32))
    is_registered: Mapped[Optional[bool]] = mapped_column()

    formation_date: Mapped[Optional[date]] = mapped_column(Date)
    registration_date: Mapped[Optional[date]] = mapped_column(Date)
    termination_date: Mapped[Optional[date]] = mapped_column(Date)

    types_total: Mapped[Optional[int]] = mapped_column(Integer)
    types_services: Mapped[Optional[list]] = mapped_column(JSON)
    types_other: Mapped[Optional[str]] = mapped_column(Text)

    clearing_type: Mapped[Optional[str]] = mapped_column(String(32))
    clearing_statement: Mapped[Optional[str]] = mapped_column(Text)
    clearing_raw_text: Mapped[Optional[str]] = mapped_column(Text)
    introducing_arrangements: Mapped[Optional[list]] = mapped_column(JSON)

    officers: Mapped[Optional[list]] = mapped_column(JSON)
    parse_warnings: Mapped[Optional[list]] = mapped_column(JSON)

    raw_pdf_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    parsed_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=datetime.utcnow)


class FocusReportRow(Base):
    __tablename__ = "focus_report"
    __table_args__ = (UniqueConstraint("crd_number", "period_ending"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    crd_number: Mapped[str] = mapped_column(String(32), index=True)
    sec_file_number: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    firm_name: Mapped[Optional[str]] = mapped_column(String(512))

    period_beginning: Mapped[Optional[date]] = mapped_column(Date)
    period_ending: Mapped[Optional[date]] = mapped_column(Date, index=True)

    contact_name: Mapped[Optional[str]] = mapped_column(String(256))
    contact_title: Mapped[Optional[str]] = mapped_column(String(256))
    contact_email: Mapped[Optional[str]] = mapped_column(String(256))
    contact_phone: Mapped[Optional[str]] = mapped_column(String(32))

    total_assets: Mapped[Optional[Decimal]] = mapped_column(Numeric(22, 2))
    total_liabilities: Mapped[Optional[Decimal]] = mapped_column(Numeric(22, 2))
    members_equity: Mapped[Optional[Decimal]] = mapped_column(Numeric(22, 2))
    stockholders_equity: Mapped[Optional[Decimal]] = mapped_column(Numeric(22, 2))
    net_capital: Mapped[Optional[Decimal]] = mapped_column(Numeric(22, 2))

    auditor_name: Mapped[Optional[str]] = mapped_column(String(256))
    auditor_pcaob_id: Mapped[Optional[str]] = mapped_column(String(16))

    raw_pdf_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    parsed_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=datetime.utcnow)
    parse_warnings: Mapped[Optional[list]] = mapped_column(JSON)


class FirmRecordRow(Base):
    """Merged view with derivations — one row per firm, updated each run."""
    __tablename__ = "firm_record"

    crd_number: Mapped[str] = mapped_column(
        String(32), ForeignKey("firm_profile.crd_number"), primary_key=True
    )
    queried_name: Mapped[Optional[str]] = mapped_column(String(512))

    net_capital_current: Mapped[Optional[Decimal]] = mapped_column(Numeric(22, 2))
    net_capital_prior: Mapped[Optional[Decimal]] = mapped_column(Numeric(22, 2))
    net_capital_growth_pct: Mapped[Optional[float]] = mapped_column()

    total_assets_current: Mapped[Optional[Decimal]] = mapped_column(Numeric(22, 2))
    total_assets_prior: Mapped[Optional[Decimal]] = mapped_column(Numeric(22, 2))
    total_assets_growth_pct: Mapped[Optional[float]] = mapped_column()

    status: Mapped[str] = mapped_column(String(16), default="partial")
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ParseError(Base):
    __tablename__ = "parse_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    crd_number: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(32))  # 'finra' | 'sec_edgar'
    stage: Mapped[str] = mapped_column(String(32))   # 'acquire' | 'parse' | 'persist'
    error_type: Mapped[str] = mapped_column(String(128))
    error_message: Mapped[str] = mapped_column(Text)
    context: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=datetime.utcnow)


class ReviewQueue(Base):
    """Firms flagged by the cross-validator for human spot-check."""
    __tablename__ = "review_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    crd_number: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(32))   # 'finra' | 'focus'
    disagreements: Mapped[list] = mapped_column(JSON)  # list of {field, det, llm}
    reviewer: Mapped[Optional[str]] = mapped_column(String(64))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)
    final_values: Mapped[Optional[dict]] = mapped_column(JSON)  # operator's chosen values
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Engine / session
# ---------------------------------------------------------------------------

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def init_schema() -> None:
    """Create tables if they don't exist. Use once during bootstrap."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ---------------------------------------------------------------------------
# DAL — high-level operations called by the orchestrator
# ---------------------------------------------------------------------------

async def iter_input_crds(
    batch_size: int = 100,
    where_status: Optional[str] = None,
) -> AsyncIterator[FirmInput]:
    """Stream CRDs from the source table in batches."""
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(FirmInput).where(FirmInput.crd_number.is_not(None))
        if where_status:
            stmt = stmt.where(FirmInput.status == where_status)
        stmt = stmt.execution_options(yield_per=batch_size)

        result = await session.stream(stmt)
        async for row in result.scalars():
            yield row


async def upsert_firm_profile(profile: FirmProfile) -> None:
    if not profile.crd_number:
        return

    values = _firm_profile_to_row(profile)
    stmt = pg_insert(FirmProfileRow).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[FirmProfileRow.crd_number],
        set_={k: v for k, v in values.items() if k != "crd_number"},
    )

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(stmt)
        await session.commit()


async def upsert_focus_report(crd_number: str, report: FocusReport) -> None:
    if not report.period_ending:
        return

    values = _focus_report_to_row(crd_number, report)
    stmt = pg_insert(FocusReportRow).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["crd_number", "period_ending"],
        set_={k: v for k, v in values.items() if k not in ("crd_number", "period_ending", "id")},
    )

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(stmt)
        await session.commit()


async def upsert_firm_record(record: FirmRecord) -> None:
    values = {
        "crd_number": record.finra.crd_number if record.finra else record.firm_id,
        "queried_name": record.queried_name,
        "net_capital_current": record.net_capital_yoy.current_value,
        "net_capital_prior": record.net_capital_yoy.prior_value,
        "net_capital_growth_pct": record.net_capital_yoy.growth_pct,
        "total_assets_current": record.total_assets_yoy.current_value,
        "total_assets_prior": record.total_assets_yoy.prior_value,
        "total_assets_growth_pct": record.total_assets_yoy.growth_pct,
        "status": record.status,
        "failure_reason": record.failure_reason,
        "updated_at": datetime.utcnow(),
    }
    if not values["crd_number"]:
        return

    stmt = pg_insert(FirmRecordRow).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[FirmRecordRow.crd_number],
        set_={k: v for k, v in values.items() if k != "crd_number"},
    )

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(stmt)
        await session.commit()


async def log_parse_error(
    crd_number: Optional[str],
    source: str,
    stage: str,
    exc: BaseException,
    context: Optional[dict] = None,
) -> None:
    row = ParseError(
        crd_number=crd_number,
        source=source,
        stage=stage,
        error_type=type(exc).__name__,
        error_message=str(exc)[:2000],
        context=context,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        await session.commit()


async def enqueue_review(
    crd_number: str,
    source: str,
    disagreements: list[dict],
) -> None:
    """Queue a firm for human review when cross-validator flags disagreements
    that persisted after Pro escalation."""
    row = ReviewQueue(
        crd_number=crd_number,
        source=source,
        disagreements=disagreements,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        await session.commit()


async def get_existing_pdf_hash(crd_number: str) -> Optional[str]:
    """Delta-detection: return the previously-stored raw_pdf_hash if any."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(FirmProfileRow.raw_pdf_hash).where(
                FirmProfileRow.crd_number == crd_number
            )
        )
        row = result.first()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# Pydantic → row adapters
# ---------------------------------------------------------------------------

def _firm_profile_to_row(p: FirmProfile) -> dict:
    return {
        "crd_number": p.crd_number,
        "firm_name": p.firm_name,
        "sec_number": p.sec_number,
        "is_registered": p.is_registered,
        "formation_date": p.history.formation_date,
        "registration_date": p.history.registration_date,
        "termination_date": p.history.termination_date,
        "types_total": p.types_of_business.total,
        "types_services": p.types_of_business.services,
        "types_other": p.types_of_business.other,
        "clearing_type": p.operations.clearing_type.value if p.operations.clearing_type else None,
        "clearing_statement": p.operations.clearing_statement,
        "clearing_raw_text": p.operations.clearing_raw_text,
        "introducing_arrangements": [
            i.model_dump(mode="json") for i in p.operations.introducing_arrangements
        ],
        "officers": [o.model_dump(mode="json") for o in p.officers],
        "parse_warnings": p.parse_warnings,
        "raw_pdf_hash": p.raw_pdf_hash,
        "parsed_at": p.parsed_at,
    }


def _focus_report_to_row(crd_number: str, r: FocusReport) -> dict:
    return {
        "crd_number": crd_number,
        "sec_file_number": r.sec_file_number,
        "firm_name": r.firm_name,
        "period_beginning": r.period_beginning,
        "period_ending": r.period_ending,
        "contact_name": r.contact.full_name,
        "contact_title": r.contact.title,
        "contact_email": r.contact.email,
        "contact_phone": r.contact.phone,
        "total_assets": r.financials.total_assets,
        "total_liabilities": r.financials.total_liabilities,
        "members_equity": r.financials.members_equity,
        "stockholders_equity": r.financials.stockholders_equity,
        "net_capital": r.financials.net_capital,
        "auditor_name": r.auditor_name,
        "auditor_pcaob_id": r.auditor_pcaob_id,
        "raw_pdf_hash": r.raw_pdf_hash,
        "parsed_at": r.parsed_at,
        "parse_warnings": r.parse_warnings,
    }
