from __future__ import annotations

from datetime import date
from math import ceil

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.institutional_investor import InstitutionalInvestor
from app.models.investor_contact import InvestorContact
from app.models.investor_filing import InvestorFiling
from app.models.pipeline_run import PipelineRun
from app.schemas.institutional_investor import (
    InstitutionalInvestorListMeta,
    InstitutionalInvestorListResponse,
)


# Identifier used by ``get_latest_pipeline_run`` to filter pipeline-run
# rows down to the institutional-investor pipelines only -- keeps the
# BD / advisor pipeline cadence from leaking into the investor list's
# "Pipeline refreshed Xm ago" stamp.
INVESTOR_PIPELINE_NAMES = (
    "institutional_investors_13f_monitor",
    "institutional_investors_filing_monitor",
    "initial_load_institutional_investors",
)


ALLOWED_SORT_FIELDS = {
    "name": InstitutionalInvestor.name,
    "cik": InstitutionalInvestor.cik,
    "state": InstitutionalInvestor.state,
    "status": InstitutionalInvestor.status,
    "total_aum": InstitutionalInvestor.total_aum,
    "holdings_count": InstitutionalInvestor.holdings_count,
    "latest_13f_filing_date": InstitutionalInvestor.latest_13f_filing_date,
}


class InstitutionalInvestorRepository:
    """Sibling of ``InvestmentAdvisorRepository`` for the new investor list.

    Skeleton intentionally lean -- ships only the list / get / contacts /
    filings / adjacent surface needed for the FE to render. Holdings
    ingestion + Form 13F-HR narrative extraction land in follow-ups.
    """

    async def list_institutional_investors(
        self,
        db: AsyncSession,
        *,
        search: str | None,
        states: list[str],
        statuses: list[str],
        min_total_aum: float | None,
        max_total_aum: float | None,
        filed_13f_after: date | None,
        filed_13f_before: date | None,
        only_with_advisor_link: bool | None,
        sort_by: str,
        sort_dir: str,
        page: int,
        limit: int,
    ) -> InstitutionalInvestorListResponse:
        filters = []
        if search:
            like_value = f"%{search.strip()}%"
            filters.append(
                or_(
                    InstitutionalInvestor.name.ilike(like_value),
                    InstitutionalInvestor.legal_name.ilike(like_value),
                    cast(InstitutionalInvestor.cik, String).ilike(like_value),
                )
            )

        if states:
            filters.append(InstitutionalInvestor.state.in_(states))

        if statuses:
            filters.append(InstitutionalInvestor.status.in_(statuses))

        if min_total_aum is not None:
            filters.append(InstitutionalInvestor.total_aum >= min_total_aum)
        if max_total_aum is not None:
            filters.append(InstitutionalInvestor.total_aum <= max_total_aum)

        if filed_13f_after is not None:
            filters.append(InstitutionalInvestor.latest_13f_filing_date >= filed_13f_after)
        if filed_13f_before is not None:
            filters.append(InstitutionalInvestor.latest_13f_filing_date <= filed_13f_before)

        if only_with_advisor_link is True:
            filters.append(InstitutionalInvestor.advisor_id.is_not(None))
        elif only_with_advisor_link is False:
            filters.append(InstitutionalInvestor.advisor_id.is_(None))

        count_stmt = select(func.count(InstitutionalInvestor.id))
        if filters:
            count_stmt = count_stmt.where(*filters)

        total = int((await db.execute(count_stmt)).scalar_one())
        offset = (page - 1) * limit

        sort_column = ALLOWED_SORT_FIELDS.get(
            sort_by, InstitutionalInvestor.total_aum
        )
        ordering = (
            sort_column.desc().nullslast()
            if sort_dir == "desc"
            else sort_column.asc().nullslast()
        )

        data_stmt = select(InstitutionalInvestor)
        if filters:
            data_stmt = data_stmt.where(*filters)
        data_stmt = (
            data_stmt.order_by(ordering, InstitutionalInvestor.id.asc())
            .offset(offset)
            .limit(limit)
        )

        items = (await db.execute(data_stmt)).scalars().all()
        latest_run = await self.get_latest_pipeline_run(db)
        pipeline_refreshed_at = (
            (latest_run.completed_at or latest_run.started_at) if latest_run else None
        )
        return InstitutionalInvestorListResponse(
            items=items,
            meta=InstitutionalInvestorListMeta(
                page=page,
                limit=limit,
                total=total,
                total_pages=max(1, ceil(total / limit)) if limit else 1,
                pipeline_refreshed_at=pipeline_refreshed_at,
            ),
        )

    async def get_institutional_investor(
        self, db: AsyncSession, investor_id: int
    ) -> InstitutionalInvestor | None:
        return await db.get(InstitutionalInvestor, investor_id)

    async def list_investor_contacts(
        self, db: AsyncSession, investor_id: int
    ) -> list[InvestorContact]:
        stmt = (
            select(InvestorContact)
            .where(InvestorContact.investor_id == investor_id)
            .order_by(InvestorContact.id.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def list_investor_filings(
        self, db: AsyncSession, investor_id: int, *, limit: int = 25
    ) -> list[InvestorFiling]:
        stmt = (
            select(InvestorFiling)
            .where(InvestorFiling.investor_id == investor_id)
            .order_by(InvestorFiling.filed_at.desc(), InvestorFiling.id.desc())
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())

    async def list_states(self, db: AsyncSession) -> list[str]:
        stmt = (
            select(InstitutionalInvestor.state)
            .where(InstitutionalInvestor.state.is_not(None))
            .distinct()
            .order_by(InstitutionalInvestor.state.asc())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [row for row in rows if row]

    async def get_adjacent(
        self, db: AsyncSession, investor_id: int
    ) -> tuple[int | None, int | None]:
        """Prev/next id walking the default-sorted investor list.

        Default sort matches the list endpoint when no filter is applied:
        ``name ASC NULLS LAST, id ASC``. The FE detail page falls back to
        this endpoint when no return-envelope is present in the URL.
        """

        current = await db.get(InstitutionalInvestor, investor_id)
        if current is None:
            return None, None

        prev_stmt = (
            select(InstitutionalInvestor.id)
            .where(
                or_(
                    InstitutionalInvestor.name < current.name,
                    (InstitutionalInvestor.name == current.name)
                    & (InstitutionalInvestor.id < current.id),
                )
            )
            .order_by(
                InstitutionalInvestor.name.desc().nullslast(),
                InstitutionalInvestor.id.desc(),
            )
            .limit(1)
        )
        next_stmt = (
            select(InstitutionalInvestor.id)
            .where(
                or_(
                    InstitutionalInvestor.name > current.name,
                    (InstitutionalInvestor.name == current.name)
                    & (InstitutionalInvestor.id > current.id),
                )
            )
            .order_by(
                InstitutionalInvestor.name.asc().nullslast(),
                InstitutionalInvestor.id.asc(),
            )
            .limit(1)
        )
        prev_id = (await db.execute(prev_stmt)).scalar_one_or_none()
        next_id = (await db.execute(next_stmt)).scalar_one_or_none()
        return prev_id, next_id

    async def get_latest_pipeline_run(
        self, db: AsyncSession
    ) -> PipelineRun | None:
        stmt = (
            select(PipelineRun)
            .where(PipelineRun.pipeline_name.in_(INVESTOR_PIPELINE_NAMES))
            .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()
