"""Watched broker-dealer predicate used by the filing monitor's
auto re-extraction hook.

A firm is "watched" if EITHER:
  - any user has pinned it to a favorite_list (user-scoped favorites
    ORed across the user base), OR
  - its system-computed ``lead_priority`` is ``'hot'`` (ACG score >= 70.0;
    see ``services/scoring.py``).

The union is intentionally global rather than per-user: the filing monitor
fan-outs auto-extractions once per qualifying firm regardless of how many
users follow it. Warm/cold lead priorities are deliberately excluded —
the goal is to bound Gemini cost to the firms we actually care about
keeping fresh.
"""

from __future__ import annotations

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteListItem


async def get_watched_bd_ids(db: AsyncSession) -> set[int]:
    """Return the set of broker_dealer ids that qualify as watched.

    One round trip for each leg (favorites, hot priority). Both are
    short ``SELECT DISTINCT id`` queries with indexed predicates so the
    cost is negligible even on a 3,400-row broker_dealers table.
    """
    favorited_stmt = (
        select(distinct(FavoriteListItem.broker_dealer_id))
        .where(FavoriteListItem.broker_dealer_id.is_not(None))
    )
    favorited: set[int] = set(
        (await db.execute(favorited_stmt)).scalars().all()
    )

    hot_stmt = select(BrokerDealer.id).where(BrokerDealer.lead_priority == "hot")
    hot: set[int] = set((await db.execute(hot_stmt)).scalars().all())

    return favorited | hot
