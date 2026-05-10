from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.competitor_provider import CompetitorProvider
from app.models.scoring_setting import ScoringSetting


class SettingsService:
    async def get_scoring_settings(self, db: AsyncSession) -> ScoringSetting:
        stmt = select(ScoringSetting).where(ScoringSetting.settings_key == "default").limit(1)
        setting = (await db.execute(stmt)).scalar_one_or_none()
        if setting is not None:
            return setting

        setting = ScoringSetting(settings_key="default")
        db.add(setting)
        await db.commit()
        await db.refresh(setting)
        return setting

    async def update_scoring_settings(
        self,
        db: AsyncSession,
        *,
        net_capital_growth_weight: int,
        clearing_arrangement_weight: int,
        financial_health_weight: int,
        registration_recency_weight: int,
    ) -> ScoringSetting:
        setting = await self.get_scoring_settings(db)
        setting.net_capital_growth_weight = net_capital_growth_weight
        setting.clearing_arrangement_weight = clearing_arrangement_weight
        setting.financial_health_weight = financial_health_weight
        setting.registration_recency_weight = registration_recency_weight
        await db.commit()
        await db.refresh(setting)
        return setting

    async def create_competitor(
        self,
        db: AsyncSession,
        *,
        name: str,
        aliases: list[str],
        priority: int,
    ) -> CompetitorProvider:
        stmt = insert(CompetitorProvider).values(
            {
                "name": name,
                "aliases": aliases,
                "priority": priority,
                "is_active": True,
            }
        )
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=[CompetitorProvider.name],
            set_={
                "aliases": stmt.excluded.aliases,
                "priority": stmt.excluded.priority,
                "is_active": True,
            },
        )
        await db.execute(upsert_stmt)
        await db.commit()
        refreshed = (
            await db.execute(select(CompetitorProvider).where(CompetitorProvider.name == name).limit(1))
        ).scalar_one()
        return refreshed

    async def update_competitor(
        self,
        db: AsyncSession,
        competitor_id: int,
        *,
        aliases: list[str],
        priority: int,
        is_active: bool,
    ) -> CompetitorProvider | None:
        competitor = await db.get(CompetitorProvider, competitor_id)
        if competitor is None:
            return None
        competitor.aliases = aliases
        competitor.priority = priority
        competitor.is_active = is_active
        await db.commit()
        await db.refresh(competitor)
        return competitor
