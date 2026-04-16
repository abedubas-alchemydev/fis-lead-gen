from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.competitor_provider import CompetitorProvider

DEFAULT_COMPETITORS = [
    {"name": "Pershing LLC", "aliases": ["Pershing", "BNY Pershing"], "priority": 10},
    {"name": "Apex Clearing Corporation", "aliases": ["Apex", "Apex Clearing"], "priority": 20},
    {"name": "Hilltop Securities Inc.", "aliases": ["Hilltop", "Hilltop Securities"], "priority": 30},
    {"name": "RBC Correspondent Services", "aliases": ["RBC", "RBC Correspondent"], "priority": 40},
    {"name": "Axos Clearing LLC", "aliases": ["Axos", "Axos Clearing"], "priority": 50},
    {"name": "Vision Financial Markets LLC", "aliases": ["Vision", "Vision Financial Markets"], "priority": 60},
]


def normalize_provider_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


class CompetitorProviderService:
    async def seed_defaults(self, db: AsyncSession) -> int:
        stmt = insert(CompetitorProvider).values(DEFAULT_COMPETITORS)
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
        return len(DEFAULT_COMPETITORS)

    async def list_active(self, db: AsyncSession) -> list[CompetitorProvider]:
        stmt = (
            select(CompetitorProvider)
            .where(CompetitorProvider.is_active.is_(True))
            .order_by(CompetitorProvider.priority.asc(), CompetitorProvider.name.asc())
        )
        return (await db.execute(stmt)).scalars().all()
