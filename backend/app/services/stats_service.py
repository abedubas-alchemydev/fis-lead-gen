from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer
from app.models.filing_alert import FilingAlert

# Keep the UI range tokens (7D/30D/90D/1Y) and their day counts co-located so
# the endpoint, the service, and the tests all read the same source of truth.
RANGE_DAYS: dict[str, int] = {"7D": 7, "30D": 30, "90D": 90, "1Y": 365}


@dataclass(frozen=True)
class TimeSeriesBucket:
    """One calendar day of the lead-volume trend series."""

    date: date
    registrations: int
    alerts: int


def range_to_days(range_key: str) -> int:
    """Map a UI range token to a window length in days.

    Raises KeyError for unknown tokens; the endpoint converts that into a
    400 so the client sees a clear validation error instead of a 500.
    """
    return RANGE_DAYS[range_key]


def assemble_time_series(
    *,
    start: date,
    end: date,
    registration_rows: list[tuple[date, int]],
    alert_rows: list[tuple[date, int]],
) -> list[TimeSeriesBucket]:
    """Fold sparse per-day counts into a contiguous zero-filled daily series.

    Pure function — kept separate from the DB-bound query so the shape of the
    series can be unit-tested without a live Postgres connection. Rows whose
    date falls outside [start, end] are silently dropped so the client never
    receives leakage from the query's boundary conditions.
    """
    if end < start:
        return []

    reg_map = {row_date: count for row_date, count in registration_rows}
    alert_map = {row_date: count for row_date, count in alert_rows}

    buckets: list[TimeSeriesBucket] = []
    cursor = start
    while cursor <= end:
        buckets.append(
            TimeSeriesBucket(
                date=cursor,
                registrations=reg_map.get(cursor, 0),
                alerts=alert_map.get(cursor, 0),
            )
        )
        cursor = cursor + timedelta(days=1)
    return buckets


async def fetch_time_series(
    db: AsyncSession,
    *,
    range_key: str,
    today: date | None = None,
) -> list[TimeSeriesBucket]:
    """Load broker-dealer registrations + deficiency alerts bucketed by day.

    `today` is a seam for tests; production callers omit it and get the
    current UTC date. The end boundary is inclusive, so `30D` returns 30
    buckets ending on `today`.
    """
    days = range_to_days(range_key)
    anchor = today or datetime.now(timezone.utc).date()
    start = anchor - timedelta(days=days - 1)

    reg_stmt = (
        select(
            BrokerDealer.registration_date.label("day"),
            func.count(BrokerDealer.id).label("c"),
        )
        .where(BrokerDealer.registration_date >= start)
        .where(BrokerDealer.registration_date <= anchor)
        .group_by(BrokerDealer.registration_date)
    )
    registration_rows: list[tuple[date, int]] = [
        (row.day, int(row.c))
        for row in (await db.execute(reg_stmt)).all()
        if row.day is not None
    ]

    # filed_at is a timestamptz; cast to DATE for day-level grouping. The
    # lower bound uses start-of-day in UTC so the window lines up with the
    # registration_date window exactly.
    alert_day = cast(FilingAlert.filed_at, Date).label("day")
    window_start = datetime.combine(start, time.min, tzinfo=timezone.utc)
    alert_stmt = (
        select(alert_day, func.count(FilingAlert.id).label("c"))
        .where(FilingAlert.filed_at >= window_start)
        .group_by(alert_day)
    )
    alert_rows: list[tuple[date, int]] = [
        (row.day, int(row.c))
        for row in (await db.execute(alert_stmt)).all()
        if row.day is not None
    ]

    return assemble_time_series(
        start=start,
        end=anchor,
        registration_rows=registration_rows,
        alert_rows=alert_rows,
    )
