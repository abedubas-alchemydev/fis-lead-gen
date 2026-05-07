from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.competitor_provider import CompetitorProvider

# Bare-brand aliases (RBC, Apex, Axos, Hilltop, Vision) were removed because
# they collide with sister entities that share the brand prefix but are not
# the seeded competitor (e.g. "RBC Capital Markets" vs the actual competitor
# "RBC Correspondent Services"). Pershing is kept as a bare alias because
# the brand belongs to one firm in our universe. See
# reports/acg-competitor-audit-2026-04-28.md.
#
# `display_name` is the short label rendered in the consolidated
# clearing-partner filter dropdown. New entries below the original six
# follow the same bare-brand collision rule — full multi-word aliases
# only — and should be reviewed against the live `current_clearing_partner`
# distinct-values query before each merge.
DEFAULT_COMPETITORS = [
    {"name": "Pershing LLC", "display_name": "Pershing", "aliases": ["Pershing", "BNY Pershing"], "priority": 10},
    {"name": "Apex Clearing Corporation", "display_name": "Apex", "aliases": ["Apex Clearing"], "priority": 20},
    {"name": "Hilltop Securities Inc.", "display_name": "Hilltop", "aliases": ["Hilltop Securities"], "priority": 30},
    {"name": "RBC Correspondent Services", "display_name": "RBC", "aliases": ["RBC Correspondent"], "priority": 40},
    {"name": "Axos Clearing LLC", "display_name": "Axos", "aliases": ["Axos Clearing"], "priority": 50},
    {"name": "Vision Financial Markets LLC", "display_name": "Vision", "aliases": ["Vision Financial Markets"], "priority": 60},
    {
        "name": "National Financial Services LLC",
        "display_name": "NFS / Fidelity",
        "aliases": ["National Financial Services", "Fidelity Clearing", "Fidelity Brokerage Services"],
        "priority": 70,
    },
    {
        "name": "Wells Fargo Clearing Services LLC",
        "display_name": "Wells Fargo",
        "aliases": ["Wells Fargo Clearing", "First Clearing"],
        "priority": 80,
    },
    {
        "name": "Goldman Sachs & Co. LLC",
        "display_name": "Goldman Sachs",
        "aliases": ["Goldman Sachs", "Goldman Sachs & Co"],
        "priority": 90,
    },
    {
        "name": "J.P. Morgan Securities LLC",
        "display_name": "J.P. Morgan",
        "aliases": ["JP Morgan Securities", "J.P. Morgan Securities", "JPMorgan Securities"],
        "priority": 100,
    },
    {
        "name": "Charles Schwab & Co., Inc.",
        "display_name": "Schwab",
        "aliases": ["Charles Schwab", "Charles Schwab & Co", "TD Ameritrade"],
        "priority": 110,
    },
    {
        "name": "Raymond James & Associates",
        "display_name": "Raymond James",
        "aliases": ["Raymond James & Associates", "Raymond James Financial Services"],
        "priority": 120,
    },
    {
        "name": "LPL Financial LLC",
        "display_name": "LPL",
        "aliases": ["LPL Financial"],
        "priority": 130,
    },
    {
        "name": "Interactive Brokers LLC",
        "display_name": "Interactive Brokers",
        "aliases": ["Interactive Brokers"],
        "priority": 140,
    },
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
                "display_name": stmt.excluded.display_name,
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
