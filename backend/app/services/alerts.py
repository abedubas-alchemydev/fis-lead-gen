from __future__ import annotations

from math import ceil

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer
from app.models.filing_alert import FilingAlert
from app.schemas.alerts import AlertListItem, AlertListMeta, AlertListResponse
from app.services.service_models import FilingAlertRecord


class AlertRepository:
    async def upsert_many(self, db: AsyncSession, records: list[FilingAlertRecord]) -> int:
        if not records:
            return 0

        stmt = insert(FilingAlert).values(
            [
                {
                    "bd_id": record.bd_id,
                    "dedupe_key": record.dedupe_key,
                    "form_type": record.form_type,
                    "priority": record.priority,
                    "filed_at": record.filed_at,
                    "summary": record.summary,
                    "source_filing_url": record.source_filing_url,
                    "is_read": False,
                }
                for record in records
            ]
        )
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=[FilingAlert.dedupe_key],
            set_={
                "form_type": stmt.excluded.form_type,
                "priority": stmt.excluded.priority,
                "filed_at": stmt.excluded.filed_at,
                "summary": stmt.excluded.summary,
                "source_filing_url": stmt.excluded.source_filing_url,
                "updated_at": func.now(),
            },
        )
        await db.execute(upsert_stmt)
        await db.flush()
        return len(records)

    async def list_alerts(
        self,
        db: AsyncSession,
        *,
        form_types: list[str],
        priorities: list[str],
        is_read: bool | None,
        broker_dealer_id: int | None,
        page: int,
        limit: int,
        category: str | None = None,
    ) -> AlertListResponse:
        filters = []
        if form_types:
            filters.append(FilingAlert.form_type.in_(form_types))
        if priorities:
            filters.append(FilingAlert.priority.in_(priorities))
        if is_read is not None:
            filters.append(FilingAlert.is_read.is_(is_read))
        if broker_dealer_id is not None:
            filters.append(FilingAlert.bd_id == broker_dealer_id)
        if category == "form_bd":
            filters.append(FilingAlert.form_type == "Form BD")
        elif category == "deficiency":
            filters.append(FilingAlert.form_type == "Form 17a-11")

        count_stmt = select(func.count(FilingAlert.id))
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = int((await db.execute(count_stmt)).scalar_one())

        stmt = (
            select(FilingAlert, BrokerDealer.name)
            .join(BrokerDealer, BrokerDealer.id == FilingAlert.bd_id)
            .order_by(FilingAlert.filed_at.desc(), FilingAlert.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        if filters:
            stmt = stmt.where(*filters)

        rows = (await db.execute(stmt)).all()
        items = [
            AlertListItem(
                id=alert.id,
                bd_id=alert.bd_id,
                firm_name=firm_name,
                form_type=alert.form_type,
                priority=alert.priority,
                filed_at=alert.filed_at,
                summary=alert.summary,
                source_filing_url=alert.source_filing_url,
                is_read=alert.is_read,
            )
            for alert, firm_name in rows
        ]

        return AlertListResponse(
            items=items,
            meta=AlertListMeta(
                page=page,
                limit=limit,
                total=total,
                total_pages=max(1, ceil(total / limit)) if limit else 1,
            ),
        )

    async def get_recent_alerts(self, db: AsyncSession, *, limit: int = 6) -> list[AlertListItem]:
        response = await self.list_alerts(
            db,
            form_types=[],
            priorities=[],
            is_read=None,
            broker_dealer_id=None,
            page=1,
            limit=limit,
        )
        return response.items

    async def get_alert(self, db: AsyncSession, alert_id: int) -> FilingAlert | None:
        return await db.get(FilingAlert, alert_id)

    async def mark_alert_read(self, db: AsyncSession, alert_id: int, *, is_read: bool = True) -> FilingAlert | None:
        alert = await self.get_alert(db, alert_id)
        if alert is None:
            return None
        alert.is_read = is_read
        await db.commit()
        await db.refresh(alert)
        return alert

    async def mark_all_read(
        self,
        db: AsyncSession,
        *,
        form_types: list[str],
        priorities: list[str],
        broker_dealer_id: int | None = None,
    ) -> int:
        filters = [FilingAlert.is_read.is_(False)]
        if form_types:
            filters.append(FilingAlert.form_type.in_(form_types))
        if priorities:
            filters.append(FilingAlert.priority.in_(priorities))
        if broker_dealer_id is not None:
            filters.append(FilingAlert.bd_id == broker_dealer_id)

        result = await db.execute(
            update(FilingAlert)
            .where(and_(*filters))
            .values(is_read=True, updated_at=func.now())
        )
        await db.commit()
        return int(result.rowcount or 0)

    async def count_deficiency_firms(self, db: AsyncSession) -> int:
        stmt = select(func.count(BrokerDealer.id)).where(BrokerDealer.is_deficient.is_(True))
        return int((await db.execute(stmt)).scalar_one())

    async def count_unread_alerts(self, db: AsyncSession) -> int:
        stmt = select(func.count(FilingAlert.id)).where(FilingAlert.is_read.is_(False))
        return int((await db.execute(stmt)).scalar_one())

    async def get_filing_history(self, db: AsyncSession, broker_dealer_id: int) -> list[FilingAlert]:
        stmt = (
            select(FilingAlert)
            .where(FilingAlert.bd_id == broker_dealer_id)
            .order_by(FilingAlert.filed_at.desc(), FilingAlert.id.desc())
        )
        return (await db.execute(stmt)).scalars().all()
