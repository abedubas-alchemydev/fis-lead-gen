from __future__ import annotations

from datetime import date
from math import ceil
import re

from sqlalchemy import ARRAY, String, cast, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer
from app.models.clearing_arrangement import ClearingArrangement
from app.models.industry_arrangement import IndustryArrangement
from app.models.introducing_arrangement import IntroducingArrangement
from app.models.competitor_provider import CompetitorProvider
from app.models.executive_contact import ExecutiveContact
from app.models.filing_alert import FilingAlert
from app.models.financial_metric import FinancialMetric
from app.models.pipeline_run import PipelineRun
from app.models.scoring_setting import ScoringSetting
from app.schemas.broker_dealer import BrokerDealerListMeta, BrokerDealerListResponse
from app.services.scoring import calculate_lead_score, classify_lead_priority
from app.services.service_models import MergedBrokerDealerRecord, ProviderDistributionRecord

# Minimum adoption threshold for the master-list types-of-business filter.
# Anything that appears on only one firm is almost always a free-text "other"
# value rather than a real FINRA category, and including those one-offs blows
# the dropdown out to ~3,300 entries. Two firms in agreement is enough signal
# that the type is shared rather than firm-specific noise.
TYPES_OF_BUSINESS_MIN_COUNT = 2


ALLOWED_SORT_FIELDS = {
    "name": BrokerDealer.name,
    "cik": BrokerDealer.cik,
    "crd_number": BrokerDealer.crd_number,
    "state": BrokerDealer.state,
    "status": BrokerDealer.status,
    "last_filing_date": BrokerDealer.last_filing_date,
    "registration_date": BrokerDealer.registration_date,
    "branch_count": BrokerDealer.branch_count,
    "latest_net_capital": BrokerDealer.latest_net_capital,
    "yoy_growth": BrokerDealer.yoy_growth,
    "health_status": BrokerDealer.health_status,
    "lead_score": BrokerDealer.lead_score,
    "lead_priority": BrokerDealer.lead_priority,
    "current_clearing_partner": BrokerDealer.current_clearing_partner,
    "current_clearing_type": BrokerDealer.current_clearing_type,
    "clearing_classification": BrokerDealer.clearing_classification,
    "is_niche_restricted": BrokerDealer.is_niche_restricted,
}


class BrokerDealerRepository:
    async def replace_dataset(self, db: AsyncSession, records: list[MergedBrokerDealerRecord]) -> int:
        await db.execute(delete(ClearingArrangement))
        await db.execute(delete(ExecutiveContact))
        await db.execute(delete(FilingAlert))
        await db.execute(delete(FinancialMetric))
        await db.execute(delete(BrokerDealer))
        await db.execute(delete(PipelineRun))
        await db.flush()

        if not records:
            await db.commit()
            return 0

        total = 0
        batch_size = 500
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            values = [
                {
                    "cik": record.cik,
                    "crd_number": record.crd_number,
                    "sec_file_number": record.sec_file_number,
                    "name": record.name,
                    "city": record.city,
                    "state": record.state,
                    "status": record.status,
                    "branch_count": record.branch_count,
                    "business_type": record.business_type,
                    "registration_date": record.registration_date,
                    "matched_source": record.matched_source,
                    "last_filing_date": record.last_filing_date,
                    "filings_index_url": record.filings_index_url,
                    "website": record.website,
                    "types_of_business": record.types_of_business,
                    "direct_owners": record.direct_owners,
                    "executive_officers": record.executive_officers,
                    "firm_operations_text": record.firm_operations_text,
                }
                for record in batch
            ]
            await db.execute(insert(BrokerDealer).values(values))
            total += len(batch)

        await db.commit()
        return total

    async def upsert_many(self, db: AsyncSession, records: list[MergedBrokerDealerRecord]) -> int:
        if not records:
            return 0

        # Split records: those with a CIK can use the CIK unique index for
        # upsert; FINRA-only records (cik=None) must use sec_file_number
        # instead, since PostgreSQL treats NULL != NULL and would silently
        # insert duplicates on the CIK conflict path.
        cik_records = [r for r in records if r.cik is not None]
        no_cik_records = [r for r in records if r.cik is None]
        total = 0
        batch_size = 500
        upsert_fields = {
            "crd_number", "sec_file_number", "name", "city", "state",
            "status", "branch_count", "business_type", "registration_date",
            "matched_source", "last_filing_date", "filings_index_url",
            "website", "types_of_business", "direct_owners",
            "executive_officers", "firm_operations_text",
        }

        def _to_values(batch: list[MergedBrokerDealerRecord]) -> list[dict[str, object]]:
            return [
                {
                    "cik": record.cik,
                    "crd_number": record.crd_number,
                    "sec_file_number": record.sec_file_number,
                    "name": record.name,
                    "city": record.city,
                    "state": record.state,
                    "status": record.status,
                    "branch_count": record.branch_count,
                    "business_type": record.business_type,
                    "registration_date": record.registration_date,
                    "matched_source": record.matched_source,
                    "last_filing_date": record.last_filing_date,
                    "filings_index_url": record.filings_index_url,
                    "website": record.website,
                    "types_of_business": record.types_of_business,
                    "direct_owners": record.direct_owners,
                    "executive_officers": record.executive_officers,
                    "firm_operations_text": record.firm_operations_text,
                }
                for record in batch
            ]

        # ── CIK-based upsert (records that have a CIK) ──
        for start in range(0, len(cik_records), batch_size):
            batch = cik_records[start : start + batch_size]
            values = _to_values(batch)
            stmt = insert(BrokerDealer).values(values)
            upsert_stmt = stmt.on_conflict_do_update(
                index_elements=[BrokerDealer.cik],
                set_={field: getattr(stmt.excluded, field) for field in upsert_fields},
            )
            await db.execute(upsert_stmt)
            total += len(batch)

        # ── SEC-file-number-based upsert (FINRA-only records with cik=None) ──
        # These records have no CIK, so we match on sec_file_number instead.
        # We use a simple select-then-insert/update approach since sec_file_number
        # has a non-unique index (not a unique constraint suitable for ON CONFLICT).
        for start in range(0, len(no_cik_records), batch_size):
            batch = no_cik_records[start : start + batch_size]
            sec_numbers = [r.sec_file_number for r in batch if r.sec_file_number]
            existing_stmt = select(BrokerDealer.sec_file_number).where(
                BrokerDealer.sec_file_number.in_(sec_numbers)
            )
            existing_rows = set((await db.execute(existing_stmt)).scalars().all())

            for record in batch:
                if record.sec_file_number and record.sec_file_number in existing_rows:
                    # Update existing row by sec_file_number
                    await db.execute(
                        update(BrokerDealer)
                        .where(BrokerDealer.sec_file_number == record.sec_file_number)
                        .values(
                            crd_number=record.crd_number,
                            name=record.name,
                            city=record.city,
                            state=record.state,
                            status=record.status,
                            branch_count=record.branch_count,
                            business_type=record.business_type,
                            registration_date=record.registration_date,
                            matched_source=record.matched_source,
                            last_filing_date=record.last_filing_date,
                            filings_index_url=record.filings_index_url,
                            website=record.website,
                            types_of_business=record.types_of_business,
                            direct_owners=record.direct_owners,
                            executive_officers=record.executive_officers,
                            firm_operations_text=record.firm_operations_text,
                        )
                    )
                else:
                    # Insert new row
                    await db.execute(
                        insert(BrokerDealer).values(_to_values([record])[0])
                    )
                total += 1

        await db.commit()
        return total

    async def list_broker_dealers(
        self,
        db: AsyncSession,
        *,
        search: str | None,
        states: list[str],
        statuses: list[str],
        health_statuses: list[str],
        lead_priorities: list[str],
        clearing_partners: list[str],
        clearing_types: list[str],
        types_of_business: list[str],
        list_mode: str,
        sort_by: str,
        sort_dir: str,
        page: int,
        limit: int,
        min_net_capital: float | None = None,
        max_net_capital: float | None = None,
        registered_after: date | None = None,
        registered_before: date | None = None,
    ) -> BrokerDealerListResponse:
        filters = []
        if search:
            like_value = f"%{search.strip()}%"
            filters.append(
                or_(
                    BrokerDealer.name.ilike(like_value),
                    BrokerDealer.cik.ilike(like_value),
                    cast(BrokerDealer.crd_number, String).ilike(like_value),
                    cast(BrokerDealer.sec_file_number, String).ilike(like_value),
                )
            )

        if states:
            filters.append(BrokerDealer.state.in_(states))

        if statuses:
            filters.append(BrokerDealer.status.in_(statuses))

        if health_statuses:
            filters.append(BrokerDealer.health_status.in_(health_statuses))

        if lead_priorities:
            filters.append(BrokerDealer.lead_priority.in_(lead_priorities))

        if clearing_partners:
            filters.append(BrokerDealer.current_clearing_partner.in_(clearing_partners))

        if clearing_types:
            filters.append(BrokerDealer.current_clearing_type.in_(clearing_types))

        if types_of_business:
            # JSONB array 'any-of': row matches if ANY requested type appears
            # in the firm's types_of_business list. Cast the query parameter
            # to the Postgres text[] type so the ?| operator is happy.
            filters.append(
                BrokerDealer.types_of_business.op("?|")(cast(types_of_business, ARRAY(String)))
            )

        # Net-capital + registration-date range filters. NULL columns never
        # satisfy a range comparison (NULL >= 5 evaluates to unknown, which
        # WHERE treats as false), so firms missing the underlying value are
        # excluded automatically — no explicit is_not(None) guard needed.
        if min_net_capital is not None:
            filters.append(BrokerDealer.latest_net_capital >= min_net_capital)
        if max_net_capital is not None:
            filters.append(BrokerDealer.latest_net_capital <= max_net_capital)
        if registered_after is not None:
            filters.append(BrokerDealer.registration_date >= registered_after)
        if registered_before is not None:
            filters.append(BrokerDealer.registration_date <= registered_before)

        if list_mode == "primary":
            filters.append(BrokerDealer.is_deficient.is_(False))
        elif list_mode == "alternative":
            filters.append(or_(BrokerDealer.is_deficient.is_(True), BrokerDealer.health_status == "at_risk"))

        count_stmt = select(func.count(BrokerDealer.id))
        if filters:
            count_stmt = count_stmt.where(*filters)

        total = int((await db.execute(count_stmt)).scalar_one())
        offset = (page - 1) * limit

        sort_column = ALLOWED_SORT_FIELDS.get(sort_by, BrokerDealer.name)
        # Push nulls to the end regardless of sort direction so firms with
        # missing data (e.g. no net capital, no YoY growth) don't dominate
        # the top of results.
        ordering = sort_column.desc().nullslast() if sort_dir == "desc" else sort_column.asc().nullslast()

        data_stmt = select(BrokerDealer)
        if filters:
            data_stmt = data_stmt.where(*filters)
        data_stmt = data_stmt.order_by(ordering, BrokerDealer.id.asc()).offset(offset).limit(limit)

        items = (await db.execute(data_stmt)).scalars().all()
        # Surface the latest pipeline-run timestamp on every list response so
        # the master-list topbar can render "Pipeline refreshed Xm ago" for
        # all authenticated users (the dedicated /pipeline/clearing endpoint
        # is admin-only). Prefer `completed_at`; fall back to `started_at`
        # while a run is still in flight; None if no runs exist yet.
        latest_run = await self.get_latest_pipeline_run(db)
        pipeline_refreshed_at = (
            (latest_run.completed_at or latest_run.started_at) if latest_run else None
        )
        return BrokerDealerListResponse(
            items=items,
            meta=BrokerDealerListMeta(
                page=page,
                limit=limit,
                total=total,
                total_pages=max(1, ceil(total / limit)) if limit else 1,
                pipeline_refreshed_at=pipeline_refreshed_at,
            ),
        )

    async def get_broker_dealer(self, db: AsyncSession, broker_dealer_id: int) -> BrokerDealer | None:
        return await db.get(BrokerDealer, broker_dealer_id)

    async def get_financial_metrics(self, db: AsyncSession, broker_dealer_id: int) -> list[FinancialMetric]:
        stmt = (
            select(FinancialMetric)
            .where(FinancialMetric.bd_id == broker_dealer_id)
            .order_by(FinancialMetric.report_date.desc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def get_executive_contacts(self, db: AsyncSession, broker_dealer_id: int) -> list[ExecutiveContact]:
        stmt = (
            select(ExecutiveContact)
            .where(ExecutiveContact.bd_id == broker_dealer_id)
            .order_by(ExecutiveContact.enriched_at.desc(), ExecutiveContact.id.asc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def count_all(self, db: AsyncSession) -> int:
        stmt = select(func.count(BrokerDealer.id))
        return int((await db.execute(stmt)).scalar_one())

    async def count_hot_leads(self, db: AsyncSession) -> int:
        stmt = select(func.count(BrokerDealer.id)).where(BrokerDealer.lead_priority == "hot")
        return int((await db.execute(stmt)).scalar_one())

    async def list_states(self, db: AsyncSession) -> list[str]:
        stmt = select(BrokerDealer.state).where(BrokerDealer.state.is_not(None)).distinct().order_by(BrokerDealer.state.asc())
        rows = (await db.execute(stmt)).scalars().all()
        return [row for row in rows if row]

    async def list_clearing_partners(self, db: AsyncSession) -> list[str]:
        stmt = (
            select(BrokerDealer.current_clearing_partner)
            .where(BrokerDealer.current_clearing_partner.is_not(None))
            .distinct()
            .order_by(BrokerDealer.current_clearing_partner.asc())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [row for row in rows if row]

    async def list_types_of_business(self, db: AsyncSession) -> list[dict[str, object]]:
        """Distinct types-of-business across all firms with per-type counts.

        Flattens the JSONB array via `jsonb_array_elements_text`, trims and
        drops null/empty values, groups by the trimmed text, excludes one-off
        free-text "other" entries via ``HAVING COUNT(*) >= TYPES_OF_BUSINESS_MIN_COUNT``,
        and returns `{type, count}` sorted by count desc then alphabetically.
        Fuels the multi-select filter on the master list.
        """
        type_element = func.jsonb_array_elements_text(BrokerDealer.types_of_business).label("type")
        subq = (
            select(type_element)
            .where(BrokerDealer.types_of_business.is_not(None))
            .subquery()
        )
        trimmed = func.trim(subq.c.type)
        stmt = (
            select(trimmed.label("type"), func.count().label("count"))
            .where(trimmed.is_not(None))
            .where(func.length(trimmed) > 0)
            .group_by(trimmed)
            .having(func.count() >= TYPES_OF_BUSINESS_MIN_COUNT)
            .order_by(func.count().desc(), trimmed.asc())
        )
        rows = (await db.execute(stmt)).all()
        return [{"type": row.type, "count": int(row.count)} for row in rows]

    async def list_clearing_arrangements(self, db: AsyncSession, broker_dealer_id: int) -> list[ClearingArrangement]:
        stmt = (
            select(ClearingArrangement)
            .where(ClearingArrangement.bd_id == broker_dealer_id)
            .order_by(ClearingArrangement.filing_year.desc(), ClearingArrangement.id.desc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def list_introducing_arrangements(self, db: AsyncSession, broker_dealer_id: int) -> list[IntroducingArrangement]:
        stmt = (
            select(IntroducingArrangement)
            .where(IntroducingArrangement.bd_id == broker_dealer_id)
            .order_by(IntroducingArrangement.id.asc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def list_industry_arrangements(
        self, db: AsyncSession, broker_dealer_id: int
    ) -> list[IndustryArrangement]:
        stmt = (
            select(IndustryArrangement)
            .where(IndustryArrangement.bd_id == broker_dealer_id)
            .order_by(IndustryArrangement.kind.asc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def upsert_clearing_arrangements(self, db: AsyncSession, records: list[dict[str, object]]) -> int:
        if not records:
            return 0

        stmt = insert(ClearingArrangement).values(records)
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=[ClearingArrangement.bd_id, ClearingArrangement.filing_year],
            set_={
                "pipeline_run_id": stmt.excluded.pipeline_run_id,
                "report_date": stmt.excluded.report_date,
                "source_filing_url": stmt.excluded.source_filing_url,
                "source_pdf_url": stmt.excluded.source_pdf_url,
                "local_document_path": stmt.excluded.local_document_path,
                "clearing_partner": stmt.excluded.clearing_partner,
                "normalized_partner": stmt.excluded.normalized_partner,
                "clearing_type": stmt.excluded.clearing_type,
                "agreement_date": stmt.excluded.agreement_date,
                "extraction_confidence": stmt.excluded.extraction_confidence,
                "extraction_status": stmt.excluded.extraction_status,
                "extraction_notes": stmt.excluded.extraction_notes,
                "is_competitor": stmt.excluded.is_competitor,
                "is_verified": stmt.excluded.is_verified,
                "extracted_at": stmt.excluded.extracted_at,
                "updated_at": func.now(),
            },
        )
        await db.execute(upsert_stmt)
        await db.flush()
        return len(records)

    async def refresh_clearing_rollups(self, db: AsyncSession) -> None:
        broker_dealers = (await db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))).scalars().all()
        arrangements = (await db.execute(select(ClearingArrangement).order_by(ClearingArrangement.filing_year.desc()))).scalars().all()

        latest_by_bd: dict[int, ClearingArrangement] = {}
        for arrangement in arrangements:
            current = latest_by_bd.get(arrangement.bd_id)
            if current is None or arrangement.filing_year > current.filing_year:
                latest_by_bd[arrangement.bd_id] = arrangement

        for broker_dealer in broker_dealers:
            latest = latest_by_bd.get(broker_dealer.id)
            if latest is None:
                broker_dealer.current_clearing_partner = None
                broker_dealer.current_clearing_type = None
                broker_dealer.current_clearing_is_competitor = False
                broker_dealer.current_clearing_source_filing_url = None
                broker_dealer.current_clearing_extraction_confidence = None
                broker_dealer.last_audit_report_date = None
                continue

            broker_dealer.current_clearing_partner = latest.clearing_partner
            broker_dealer.current_clearing_type = latest.clearing_type
            broker_dealer.current_clearing_is_competitor = bool(latest.is_competitor)
            broker_dealer.current_clearing_source_filing_url = latest.source_filing_url
            broker_dealer.current_clearing_extraction_confidence = (
                float(latest.extraction_confidence) if latest.extraction_confidence is not None else None
            )
            broker_dealer.last_audit_report_date = latest.report_date

        await db.flush()

    async def refresh_competitor_flags(self, db: AsyncSession) -> None:
        competitors = await self.list_competitor_providers(db)
        arrangements = (await db.execute(select(ClearingArrangement).order_by(ClearingArrangement.id.asc()))).scalars().all()
        for arrangement in arrangements:
            arrangement.is_competitor = self.match_competitor(arrangement.clearing_partner, competitors)
        await db.flush()
        await self.refresh_clearing_rollups(db)

    async def list_recent_pipeline_runs(self, db: AsyncSession, limit: int = 8) -> list[PipelineRun]:
        stmt = select(PipelineRun).order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc()).limit(limit)
        return (await db.execute(stmt)).scalars().all()

    async def get_latest_pipeline_run(self, db: AsyncSession) -> PipelineRun | None:
        stmt = select(PipelineRun).order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc()).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def list_recent_clearing_failures(self, db: AsyncSession, limit: int = 10) -> list[ClearingArrangement]:
        stmt = (
            select(ClearingArrangement)
            .where(ClearingArrangement.extraction_status != "parsed")
            .order_by(ClearingArrangement.extracted_at.desc().nullslast(), ClearingArrangement.id.desc())
            .limit(limit)
        )
        return (await db.execute(stmt)).scalars().all()

    async def list_failed_clearing_broker_dealer_ids(self, db: AsyncSession) -> set[int]:
        stmt = select(ClearingArrangement.bd_id).where(ClearingArrangement.extraction_status != "parsed")
        rows = (await db.execute(stmt)).scalars().all()
        return set(rows)

    async def list_competitor_providers(self, db: AsyncSession) -> list[CompetitorProvider]:
        stmt = (
            select(CompetitorProvider)
            .where(CompetitorProvider.is_active.is_(True))
            .order_by(CompetitorProvider.priority.asc(), CompetitorProvider.name.asc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def get_scoring_settings(self, db: AsyncSession) -> ScoringSetting | None:
        stmt = select(ScoringSetting).where(ScoringSetting.settings_key == "default").limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def refresh_lead_scores(self, db: AsyncSession) -> None:
        broker_dealers = (await db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))).scalars().all()
        settings = await self.get_scoring_settings(db)
        if settings is None:
            settings = ScoringSetting(settings_key="default")
            db.add(settings)
            await db.flush()

        for broker_dealer in broker_dealers:
            score = calculate_lead_score(
                yoy_growth=float(broker_dealer.yoy_growth) if broker_dealer.yoy_growth is not None else None,
                clearing_type=broker_dealer.current_clearing_type,
                is_competitor=bool(broker_dealer.current_clearing_is_competitor),
                health_status=broker_dealer.health_status,
                registration_date=broker_dealer.registration_date,
                weights=settings,
            )
            broker_dealer.lead_score = score
            broker_dealer.lead_priority = classify_lead_priority(score)

        await db.flush()

    async def get_clearing_distribution(self, db: AsyncSession) -> list[ProviderDistributionRecord]:
        total = await self.count_all(db)
        if total == 0:
            return []

        stmt = (
            select(
                BrokerDealer.current_clearing_partner,
                BrokerDealer.current_clearing_is_competitor,
                func.count(BrokerDealer.id),
            )
            .where(BrokerDealer.current_clearing_partner.is_not(None))
            .group_by(BrokerDealer.current_clearing_partner, BrokerDealer.current_clearing_is_competitor)
            .order_by(func.count(BrokerDealer.id).desc(), BrokerDealer.current_clearing_partner.asc())
        )
        rows = (await db.execute(stmt)).all()
        return [
            ProviderDistributionRecord(
                provider=provider or "Unknown",
                count=int(count),
                percentage=round((int(count) / total) * 100, 2),
                is_competitor=bool(is_competitor),
            )
            for provider, is_competitor, count in rows
        ]

    def normalize_partner_name(self, value: str | None) -> str | None:
        if not value:
            return None
        normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
        return normalized or None

    def match_competitor(self, partner_name: str | None, competitors: list[CompetitorProvider]) -> bool:
        """Check if any known competitor name/alias appears within the partner string.

        Gemini sometimes returns multi-partner strings like
        ``"Goldman, Sachs & Co., Pershing LLC, and Mirae Asset Securities"``.
        We need to catch the embedded ``Pershing LLC`` without firing on
        sister entities that share a brand prefix (``RBC Capital Markets``
        vs ``RBC Correspondent Services``).

        Match each competitor name and alias as a whole word
        (``\\b<alias>\\b``, case-insensitive) against the original
        un-normalized partner string. Whitespace inside multi-word aliases
        is treated as ``\\s+`` so commas-and-spaces variants ("BNY Pershing"
        vs "BNY  Pershing") still match. Bare-prefix aliases that collide
        with sibling brands have been removed from ``DEFAULT_COMPETITORS``
        in tandem with this change.
        """
        if not partner_name:
            return False

        for competitor in competitors:
            for candidate in [competitor.name, *competitor.aliases]:
                if not candidate:
                    continue
                pattern = r"\b" + r"\s+".join(re.escape(token) for token in candidate.split()) + r"\b"
                if re.search(pattern, partner_name, re.IGNORECASE):
                    return True
        return False
