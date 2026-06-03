"""Find firms registered as BOTH a broker-dealer and an investment adviser.

Doxie's per-entity tools query broker-dealers and investment advisers
separately; neither can answer "which firms are both an RIA and a BD". A
dual-registrant shares one FINRA CRD across its BD and IA registrations, so
we match the two tables on ``crd_number`` (NULL-safe — the IA side is
indexed and IA rows without a CRD are dropped at load time).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer
from app.models.investment_advisor import InvestmentAdvisor

DUAL_REGISTRATION_LIMIT_DEFAULT = 10
DUAL_REGISTRATION_LIMIT_MAX = 25


@dataclass(frozen=True)
class DualRegisteredFirm:
    """A firm present in both the broker-dealer and investment-adviser tables.

    Carries only columns held directly on the two ORM models plus the matched
    advisor id, so the chatbot tool can deep-link to either record.
    """

    name: str
    crd_number: str | None
    cik: str | None
    city: str | None
    state: str | None
    lead_priority: str | None
    broker_dealer_id: int
    advisor_id: int | None


async def list_dual_registered_firms(
    db: AsyncSession,
    *,
    state: str | None = None,
    search: str | None = None,
    limit: int = DUAL_REGISTRATION_LIMIT_DEFAULT,
) -> tuple[list[DualRegisteredFirm], int]:
    """Return broker-dealers that are also registered investment advisers.

    Matched on a shared non-NULL ``crd_number``. Optional ``state`` (BD state,
    case-insensitive) and ``search`` (BD name substring) filters. Returns
    ``(firms, total_matched)``; ``total_matched`` is the full count before the
    ``limit`` cap.
    """
    # CRDs present on the IA side — the membership test for "is also an RIA".
    # IA.crd_number is indexed and NULLs are dropped at load, so this is cheap.
    ia_crds = select(InvestmentAdvisor.crd_number).where(
        InvestmentAdvisor.crd_number.is_not(None)
    )

    filters = [
        BrokerDealer.crd_number.is_not(None),
        BrokerDealer.crd_number.in_(ia_crds),
    ]
    if state:
        filters.append(func.upper(BrokerDealer.state) == state.strip().upper())
    if search:
        filters.append(BrokerDealer.name.ilike(f"%{search.strip()}%"))

    total = int(
        (
            await db.execute(select(func.count(BrokerDealer.id)).where(*filters))
        ).scalar_one()
    )
    if total == 0:
        return [], 0

    # Correlated scalar subquery: the matching IA id for each BD — one row per
    # BD even if a CRD maps to multiple IA rows (no JOIN fan-out, no dedup).
    advisor_id_sq = (
        select(InvestmentAdvisor.id)
        .where(InvestmentAdvisor.crd_number == BrokerDealer.crd_number)
        .order_by(InvestmentAdvisor.id.asc())
        .limit(1)
        .correlate(BrokerDealer)
        .scalar_subquery()
    )

    rows = (
        await db.execute(
            select(BrokerDealer, advisor_id_sq.label("advisor_id"))
            .where(*filters)
            .order_by(BrokerDealer.name.asc(), BrokerDealer.id.asc())
            .limit(max(1, limit))
        )
    ).all()

    firms = [
        DualRegisteredFirm(
            name=bd.name,
            crd_number=bd.crd_number,
            cik=bd.cik,
            city=bd.city,
            state=bd.state,
            lead_priority=bd.lead_priority,
            broker_dealer_id=bd.id,
            advisor_id=advisor_id,
        )
        for (bd, advisor_id) in rows
    ]
    return firms, total
