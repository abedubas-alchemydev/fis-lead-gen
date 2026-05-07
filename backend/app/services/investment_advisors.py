from __future__ import annotations

from datetime import date
from math import ceil

from sqlalchemy import ARRAY, String, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.advisor_contact import AdvisorContact
from app.models.advisor_filing import AdvisorFiling
from app.models.investment_advisor import InvestmentAdvisor
from app.models.pipeline_run import PipelineRun
from app.schemas.investment_advisor import (
    InvestmentAdvisorListMeta,
    InvestmentAdvisorListResponse,
)


# Same minimum-adoption threshold as the BD ``types_of_business`` filter
# (services/broker_dealers.py:TYPES_OF_BUSINESS_MIN_COUNT). Items appearing
# on a single firm are almost always free-text "other"-style values rather
# than canonical Form ADV checkboxes, and including them blows out the
# dropdown to thousands of one-offs.
ADVISORY_ACTIVITY_MIN_COUNT = 2
CLIENT_TYPE_MIN_COUNT = 2


ALLOWED_SORT_FIELDS = {
    "name": InvestmentAdvisor.name,
    "cik": InvestmentAdvisor.cik,
    "crd_number": InvestmentAdvisor.crd_number,
    "state": InvestmentAdvisor.state,
    "status": InvestmentAdvisor.status,
    "regulatory_aum": InvestmentAdvisor.regulatory_aum,
    "discretionary_aum": InvestmentAdvisor.discretionary_aum,
    "total_clients": InvestmentAdvisor.total_clients,
    "registration_date": InvestmentAdvisor.registration_date,
    "last_filing_date": InvestmentAdvisor.last_filing_date,
    "latest_13f_filing_date": InvestmentAdvisor.latest_13f_filing_date,
}


# Identifier used by ``get_latest_pipeline_run`` to filter pipeline-run rows
# down to the advisor pipelines only — keeps the BD pipeline cadence from
# leaking into the advisor-list "Pipeline refreshed Xm ago" stamp.
ADVISOR_PIPELINE_NAMES = (
    "initial_load_advisors",
    "advisor_form_adv_pipeline",
    "advisor_13f_monitor",
    "advisor_filing_monitor",
)


class InvestmentAdvisorRepository:
    """Sibling of ``BrokerDealerRepository`` for the Investment Advisor list.

    Skeleton intentionally lean — PR 1 ships only the list/get/filter
    surface needed to render an empty-or-seeded master list. PR 2 fills
    in ``upsert_many`` against the IAPD bulk dataset and PR 3 layers in
    Form ADV extraction.
    """

    async def list_investment_advisors(
        self,
        db: AsyncSession,
        *,
        search: str | None,
        states: list[str],
        statuses: list[str],
        advisory_activities: list[str],
        client_types: list[str],
        files_13f: bool | None,
        min_regulatory_aum: float | None,
        max_regulatory_aum: float | None,
        registered_after: date | None,
        registered_before: date | None,
        sort_by: str,
        sort_dir: str,
        page: int,
        limit: int,
    ) -> InvestmentAdvisorListResponse:
        filters = []
        if search:
            like_value = f"%{search.strip()}%"
            filters.append(
                or_(
                    InvestmentAdvisor.name.ilike(like_value),
                    InvestmentAdvisor.legal_name.ilike(like_value),
                    InvestmentAdvisor.cik.ilike(like_value),
                    cast(InvestmentAdvisor.crd_number, String).ilike(like_value),
                    cast(InvestmentAdvisor.sec_file_number, String).ilike(like_value),
                )
            )

        if states:
            filters.append(InvestmentAdvisor.state.in_(states))

        if statuses:
            filters.append(InvestmentAdvisor.status.in_(statuses))

        if advisory_activities:
            # JSONB array 'any-of' — same shape BD uses for types_of_business.
            filters.append(
                InvestmentAdvisor.advisory_activities.op("?|")(
                    cast(advisory_activities, ARRAY(String))
                )
            )

        if client_types:
            filters.append(
                InvestmentAdvisor.client_types.op("?|")(
                    cast(client_types, ARRAY(String))
                )
            )

        # The hard 13F scope. Endpoint defaults this to True so the list
        # only ever shows 13F filers unless an admin explicitly opts out.
        if files_13f is True:
            filters.append(InvestmentAdvisor.files_13f.is_(True))
        elif files_13f is False:
            filters.append(InvestmentAdvisor.files_13f.is_(False))

        # NULL columns never satisfy a range comparison, so firms missing
        # the underlying value are excluded automatically without a
        # separate ``is_not(None)`` guard — same approach BD uses.
        if min_regulatory_aum is not None:
            filters.append(InvestmentAdvisor.regulatory_aum >= min_regulatory_aum)
        if max_regulatory_aum is not None:
            filters.append(InvestmentAdvisor.regulatory_aum <= max_regulatory_aum)
        if registered_after is not None:
            filters.append(InvestmentAdvisor.registration_date >= registered_after)
        if registered_before is not None:
            filters.append(InvestmentAdvisor.registration_date <= registered_before)

        count_stmt = select(func.count(InvestmentAdvisor.id))
        if filters:
            count_stmt = count_stmt.where(*filters)

        total = int((await db.execute(count_stmt)).scalar_one())
        offset = (page - 1) * limit

        sort_column = ALLOWED_SORT_FIELDS.get(sort_by, InvestmentAdvisor.regulatory_aum)
        # ``nullslast`` regardless of direction — same default the BD list
        # uses, so firms with missing AUM don't dominate the top of an
        # ascending sort.
        ordering = (
            sort_column.desc().nullslast()
            if sort_dir == "desc"
            else sort_column.asc().nullslast()
        )

        data_stmt = select(InvestmentAdvisor)
        if filters:
            data_stmt = data_stmt.where(*filters)
        data_stmt = (
            data_stmt.order_by(ordering, InvestmentAdvisor.id.asc())
            .offset(offset)
            .limit(limit)
        )

        items = (await db.execute(data_stmt)).scalars().all()
        latest_run = await self.get_latest_pipeline_run(db)
        pipeline_refreshed_at = (
            (latest_run.completed_at or latest_run.started_at) if latest_run else None
        )
        return InvestmentAdvisorListResponse(
            items=items,
            meta=InvestmentAdvisorListMeta(
                page=page,
                limit=limit,
                total=total,
                total_pages=max(1, ceil(total / limit)) if limit else 1,
                pipeline_refreshed_at=pipeline_refreshed_at,
            ),
        )

    async def get_investment_advisor(
        self, db: AsyncSession, advisor_id: int
    ) -> InvestmentAdvisor | None:
        return await db.get(InvestmentAdvisor, advisor_id)

    async def list_advisor_contacts(
        self, db: AsyncSession, advisor_id: int
    ) -> list[AdvisorContact]:
        stmt = (
            select(AdvisorContact)
            .where(AdvisorContact.advisor_id == advisor_id)
            .order_by(AdvisorContact.id.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def list_advisor_filings(
        self, db: AsyncSession, advisor_id: int, *, limit: int = 25
    ) -> list[AdvisorFiling]:
        stmt = (
            select(AdvisorFiling)
            .where(AdvisorFiling.advisor_id == advisor_id)
            .order_by(AdvisorFiling.filed_at.desc(), AdvisorFiling.id.desc())
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())

    async def list_states(self, db: AsyncSession) -> list[str]:
        stmt = (
            select(InvestmentAdvisor.state)
            .where(InvestmentAdvisor.state.is_not(None))
            .distinct()
            .order_by(InvestmentAdvisor.state.asc())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [row for row in rows if row]

    async def list_advisory_activities(
        self, db: AsyncSession
    ) -> list[dict[str, object]]:
        """Distinct Form ADV Item 5.G advisory activities with per-type counts.

        Mirrors ``BrokerDealerRepository.list_types_of_business``. Same
        ``jsonb_typeof = 'array'`` guard against JSONB scalar 'null' rows
        crashing ``jsonb_array_elements_text``.
        """

        type_element = func.jsonb_array_elements_text(
            InvestmentAdvisor.advisory_activities
        ).label("type")
        subq = (
            select(type_element)
            .where(func.jsonb_typeof(InvestmentAdvisor.advisory_activities) == "array")
            .subquery()
        )
        trimmed = func.trim(subq.c.type)
        stmt = (
            select(trimmed.label("type"), func.count().label("count"))
            .where(trimmed.is_not(None))
            .where(func.length(trimmed) > 0)
            .group_by(trimmed)
            .having(func.count() >= ADVISORY_ACTIVITY_MIN_COUNT)
            .order_by(func.count().desc(), trimmed.asc())
        )
        rows = (await db.execute(stmt)).all()
        return [{"type": row.type, "count": int(row.count)} for row in rows]

    async def list_client_types(self, db: AsyncSession) -> list[dict[str, object]]:
        """Distinct Form ADV Item 5.D client types with per-type counts.

        Same shape as ``list_advisory_activities``; targets the
        ``client_types`` JSONB array.
        """

        type_element = func.jsonb_array_elements_text(
            InvestmentAdvisor.client_types
        ).label("type")
        subq = (
            select(type_element)
            .where(func.jsonb_typeof(InvestmentAdvisor.client_types) == "array")
            .subquery()
        )
        trimmed = func.trim(subq.c.type)
        stmt = (
            select(trimmed.label("type"), func.count().label("count"))
            .where(trimmed.is_not(None))
            .where(func.length(trimmed) > 0)
            .group_by(trimmed)
            .having(func.count() >= CLIENT_TYPE_MIN_COUNT)
            .order_by(func.count().desc(), trimmed.asc())
        )
        rows = (await db.execute(stmt)).all()
        return [{"type": row.type, "count": int(row.count)} for row in rows]

    async def get_latest_pipeline_run(
        self, db: AsyncSession
    ) -> PipelineRun | None:
        """Most recent advisor pipeline run for the "refreshed Xm ago" stamp.

        Filters by ``pipeline_name`` so BD-only runs don't leak into the
        advisor list's freshness display.
        """

        stmt = (
            select(PipelineRun)
            .where(PipelineRun.pipeline_name.in_(ADVISOR_PIPELINE_NAMES))
            .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def upsert_by_crd(
        self,
        db: AsyncSession,
        records: list[dict[str, object]],
    ) -> int:
        """Idempotent CRD-keyed upsert (insert-or-update by ``crd_number``).

        ``crd_number`` is indexed but not uniquely-constrained (see
        migration 0030 — kept loose so bulk imports tolerate occasional
        duplicate rows mid-flight). That rules out
        ``ON CONFLICT (crd_number) DO UPDATE``; instead we mirror BD's
        ``upsert_many`` no-CIK path: select existing CRDs in one query,
        then INSERT or UPDATE per record. Caller is responsible for
        ``await db.commit()``.
        """

        if not records:
            return 0

        crds = [r["crd_number"] for r in records if r.get("crd_number")]
        existing_stmt = select(InvestmentAdvisor.crd_number).where(
            InvestmentAdvisor.crd_number.in_(crds)
        )
        existing = set((await db.execute(existing_stmt)).scalars().all())

        for record in records:
            crd = record.get("crd_number")
            if crd and crd in existing:
                update_fields = {
                    k: v
                    for k, v in record.items()
                    if k not in {"crd_number", "id", "created_at"}
                }
                await db.execute(
                    InvestmentAdvisor.__table__.update()
                    .where(InvestmentAdvisor.crd_number == crd)
                    .values(**update_fields)
                )
            else:
                await db.execute(insert(InvestmentAdvisor).values(record))

        return len(records)
