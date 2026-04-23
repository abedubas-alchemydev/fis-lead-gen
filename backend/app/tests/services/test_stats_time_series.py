from __future__ import annotations

from datetime import date

import pytest

from app.services.stats_service import (
    RANGE_DAYS,
    TimeSeriesBucket,
    assemble_time_series,
    range_to_days,
)


class TestRangeToDays:
    def test_known_ranges_map_to_expected_days(self) -> None:
        assert range_to_days("7D") == 7
        assert range_to_days("30D") == 30
        assert range_to_days("90D") == 90
        assert range_to_days("1Y") == 365

    def test_unknown_range_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            range_to_days("bogus")

    def test_range_days_mapping_is_exhaustive(self) -> None:
        assert RANGE_DAYS == {"7D": 7, "30D": 30, "90D": 90, "1Y": 365}


class TestAssembleTimeSeries:
    def test_zero_fills_missing_days(self) -> None:
        start = date(2026, 4, 1)
        end = date(2026, 4, 5)

        buckets = assemble_time_series(
            start=start,
            end=end,
            registration_rows=[(date(2026, 4, 2), 3), (date(2026, 4, 5), 1)],
            alert_rows=[(date(2026, 4, 3), 2)],
        )

        assert len(buckets) == 5
        assert buckets[0] == TimeSeriesBucket(date=date(2026, 4, 1), registrations=0, alerts=0)
        assert buckets[1] == TimeSeriesBucket(date=date(2026, 4, 2), registrations=3, alerts=0)
        assert buckets[2] == TimeSeriesBucket(date=date(2026, 4, 3), registrations=0, alerts=2)
        assert buckets[3] == TimeSeriesBucket(date=date(2026, 4, 4), registrations=0, alerts=0)
        assert buckets[4] == TimeSeriesBucket(date=date(2026, 4, 5), registrations=1, alerts=0)

    def test_end_before_start_returns_empty(self) -> None:
        buckets = assemble_time_series(
            start=date(2026, 4, 5),
            end=date(2026, 4, 1),
            registration_rows=[],
            alert_rows=[],
        )
        assert buckets == []

    def test_single_day_returns_single_bucket(self) -> None:
        buckets = assemble_time_series(
            start=date(2026, 4, 5),
            end=date(2026, 4, 5),
            registration_rows=[(date(2026, 4, 5), 7)],
            alert_rows=[(date(2026, 4, 5), 2)],
        )

        assert buckets == [TimeSeriesBucket(date=date(2026, 4, 5), registrations=7, alerts=2)]

    def test_rows_outside_window_are_ignored(self) -> None:
        buckets = assemble_time_series(
            start=date(2026, 4, 2),
            end=date(2026, 4, 3),
            registration_rows=[(date(2026, 4, 1), 9), (date(2026, 4, 3), 4)],
            alert_rows=[(date(2026, 4, 10), 5)],
        )

        assert buckets == [
            TimeSeriesBucket(date=date(2026, 4, 2), registrations=0, alerts=0),
            TimeSeriesBucket(date=date(2026, 4, 3), registrations=4, alerts=0),
        ]
