"""Unit tests for the EDGAR EFTS-backed 13F filer enumeration.

Covers the partition + dedupe + retry logic without hitting EFTS.
respx provides the mocked responses; date-window math is verified
through the public ``_partition_window`` helper.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.thirteen_f_filter import (
    ThirteenFFilterService,
    _normalize_cik,
    _partition_window,
)


def test_partition_window_splits_into_weekly_chunks():
    windows = _partition_window(date(2026, 1, 1), date(2026, 1, 21), partition_days=7)
    assert windows == [
        (date(2026, 1, 1), date(2026, 1, 7)),
        (date(2026, 1, 8), date(2026, 1, 14)),
        (date(2026, 1, 15), date(2026, 1, 21)),
    ]


def test_partition_window_handles_remainder():
    windows = _partition_window(date(2026, 1, 1), date(2026, 1, 10), partition_days=7)
    assert windows == [
        (date(2026, 1, 1), date(2026, 1, 7)),
        (date(2026, 1, 8), date(2026, 1, 10)),
    ]


def test_partition_window_rejects_zero_partition():
    with pytest.raises(ValueError):
        _partition_window(date(2026, 1, 1), date(2026, 1, 7), partition_days=0)


def test_normalize_cik_strips_leading_zeros():
    assert _normalize_cik("0001521951") == "1521951"
    assert _normalize_cik("1521951") == "1521951"
    assert _normalize_cik("0000000") == "0"
    assert _normalize_cik("not-a-number") is None
    assert _normalize_cik(None) is None
    assert _normalize_cik("   ") is None


def _hit(cik: str, file_date: str) -> dict[str, object]:
    return {
        "_source": {
            "ciks": [cik],
            "file_date": file_date,
            "form": "13F-HR",
        }
    }


@pytest.mark.asyncio
@respx.mock
async def test_dedupes_cik_across_windows_keeps_latest_date():
    """Same CIK appearing in two windows ⇒ keep the later filing date."""
    # Stub every EFTS GET — respx routes by URL/path/params order.
    base = settings.sec_efts_search_url
    # First window returns one filer dated Jan 8.
    respx.get(base, params__contains={"startdt": "2026-01-01"}).mock(
        return_value=httpx.Response(
            200,
            json={"hits": {"hits": [_hit("0001521951", "2026-01-08")]}},
        )
    )
    # Second window returns the SAME filer dated Jan 15 + a NEW filer.
    respx.get(base, params__contains={"startdt": "2026-01-08"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        _hit("0001521951", "2026-01-15"),
                        _hit("0000914208", "2026-01-12"),
                    ]
                }
            },
        )
    )
    service = ThirteenFFilterService()
    # 13-day lookback ending Jan 14 → exactly 2 weekly windows starting
    # Jan 1 and Jan 8. (A 14-day lookback ending Jan 15 would create a
    # 3rd single-day window starting Jan 15 that we don't bother mocking.)
    result = await service.fetch_recent_filer_ciks(
        lookback_days=13, partition_days=7, as_of=date(2026, 1, 14)
    )
    # Stripped CIKs (no leading zeros) — see _normalize_cik.
    assert result == {
        "1521951": date(2026, 1, 15),  # later of (Jan 8, Jan 15)
        "914208": date(2026, 1, 12),
    }


@pytest.mark.asyncio
@respx.mock
async def test_paginates_within_window():
    """A window with >100 hits paginates via ``from`` until short page."""
    base = settings.sec_efts_search_url

    # First page: 100 hits, all unique CIKs.
    page1 = {
        "hits": {
            "hits": [_hit(f"{i:010d}", "2026-01-05") for i in range(1, 101)],
        }
    }
    # Second page: 50 hits — short, signals end of pagination.
    page2 = {
        "hits": {
            "hits": [_hit(f"{i:010d}", "2026-01-05") for i in range(101, 151)],
        }
    }
    respx.get(base, params__contains={"from": 0}).mock(
        return_value=httpx.Response(200, json=page1)
    )
    respx.get(base, params__contains={"from": 100}).mock(
        return_value=httpx.Response(200, json=page2)
    )

    service = ThirteenFFilterService()
    result = await service.fetch_recent_filer_ciks(
        lookback_days=7, partition_days=7, as_of=date(2026, 1, 7)
    )
    assert len(result) == 150


@pytest.mark.asyncio
@respx.mock
async def test_empty_window_returns_no_results():
    respx.get(settings.sec_efts_search_url).mock(
        return_value=httpx.Response(200, json={"hits": {"hits": []}})
    )
    service = ThirteenFFilterService()
    result = await service.fetch_recent_filer_ciks(
        lookback_days=7, partition_days=7, as_of=date(2026, 1, 7)
    )
    assert result == {}


@pytest.mark.asyncio
@respx.mock
async def test_drops_hit_with_invalid_file_date():
    respx.get(settings.sec_efts_search_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {"_source": {"ciks": ["0001521951"], "file_date": "not-a-date"}},
                        _hit("0000914208", "2026-01-05"),
                    ]
                }
            },
        )
    )
    service = ThirteenFFilterService()
    result = await service.fetch_recent_filer_ciks(
        lookback_days=7, partition_days=7, as_of=date(2026, 1, 7)
    )
    # Only the valid hit survives.
    assert result == {"914208": date(2026, 1, 5)}
