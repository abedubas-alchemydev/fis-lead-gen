"""Tests for the YoY helper functions in services.scoring.

Two pure helpers under test:

* ``calculate_yoy_growth(metrics)`` — net_capital YoY %, ordered by report_date.
* ``calculate_total_assets_yoy(metrics)`` — total_assets YoY %, ordered by
  report_date, skipping rows with NULL total_assets.

Both feed BD-row rollups in ``services/focus_reports.py`` and
``services/focus_ceo_extraction.py``. The total_assets variant was
recently added because ``broker_dealers.total_assets_yoy`` had no writer
even though ``broker_dealers.yoy_growth`` did — so the column was 0%
filled across the entire master list. Tests here lock the contract so
the column populates whenever the financial pipeline runs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.financial_metric import FinancialMetric
from app.services.scoring import (
    calculate_three_year_cagr,
    calculate_total_assets_yoy,
    calculate_yoy_growth,
)


def _metric(
    *,
    report_date: date,
    net_capital: str = "100000.00",
    total_assets: str | None = None,
    bd_id: int = 1,
) -> FinancialMetric:
    """Build a synthetic FinancialMetric for unit tests."""
    return FinancialMetric(
        bd_id=bd_id,
        report_date=report_date,
        net_capital=Decimal(net_capital),
        total_assets=Decimal(total_assets) if total_assets is not None else None,
    )


# ── calculate_total_assets_yoy ──


def test_total_assets_yoy_two_metrics_returns_percentage_growth() -> None:
    metrics = [
        _metric(report_date=date(2024, 12, 31), total_assets="1000000.00"),
        _metric(report_date=date(2025, 12, 31), total_assets="1500000.00"),
    ]
    assert calculate_total_assets_yoy(metrics) == 50.0


def test_total_assets_yoy_orders_by_report_date_descending() -> None:
    """Input order must not matter; the function sorts internally."""
    metrics = [
        _metric(report_date=date(2025, 12, 31), total_assets="800000.00"),
        _metric(report_date=date(2024, 12, 31), total_assets="1000000.00"),
    ]
    assert calculate_total_assets_yoy(metrics) == -20.0


def test_total_assets_yoy_returns_none_when_fewer_than_two_metrics() -> None:
    metrics = [_metric(report_date=date(2025, 12, 31), total_assets="1000000.00")]
    assert calculate_total_assets_yoy(metrics) is None


def test_total_assets_yoy_returns_none_when_no_metrics() -> None:
    assert calculate_total_assets_yoy([]) is None


def test_total_assets_yoy_skips_rows_with_null_total_assets() -> None:
    """Some financial_metrics rows lack total_assets (extractor couldn't
    parse it). Those rows must be filtered before the YoY calc, not
    coerced to zero."""
    metrics = [
        _metric(report_date=date(2023, 12, 31), total_assets="1000000.00"),
        _metric(report_date=date(2024, 12, 31), total_assets=None),
        _metric(report_date=date(2025, 12, 31), total_assets="1200000.00"),
    ]
    # Latest two valid: 2025 (1.2M) and 2023 (1.0M) -> +20.0%
    assert calculate_total_assets_yoy(metrics) == 20.0


def test_total_assets_yoy_returns_none_when_only_one_non_null() -> None:
    metrics = [
        _metric(report_date=date(2024, 12, 31), total_assets=None),
        _metric(report_date=date(2025, 12, 31), total_assets="1000000.00"),
    ]
    assert calculate_total_assets_yoy(metrics) is None


def test_total_assets_yoy_returns_none_when_previous_is_zero() -> None:
    """Avoid ZeroDivisionError. Mirrors calculate_yoy_growth behaviour."""
    metrics = [
        _metric(report_date=date(2024, 12, 31), total_assets="0.00"),
        _metric(report_date=date(2025, 12, 31), total_assets="500000.00"),
    ]
    assert calculate_total_assets_yoy(metrics) is None


def test_total_assets_yoy_negative_growth() -> None:
    metrics = [
        _metric(report_date=date(2024, 12, 31), total_assets="2000000.00"),
        _metric(report_date=date(2025, 12, 31), total_assets="1500000.00"),
    ]
    assert calculate_total_assets_yoy(metrics) == -25.0


# ── calculate_yoy_growth (net_capital) — sanity ──


def test_yoy_growth_uses_net_capital_not_total_assets() -> None:
    """Lock the original contract: yoy_growth is computed on net_capital,
    NOT on total_assets. The two helpers cover separate columns."""
    metrics = [
        _metric(
            report_date=date(2024, 12, 31),
            net_capital="100000.00",
            total_assets="999999.00",
        ),
        _metric(
            report_date=date(2025, 12, 31),
            net_capital="125000.00",
            total_assets="111111.00",
        ),
    ]
    # net_capital: 100k -> 125k = +25%
    # total_assets: 999k -> 111k would be -88% (very different)
    assert calculate_yoy_growth(metrics) == 25.0


def test_yoy_growth_returns_none_when_fewer_than_two_metrics() -> None:
    metrics = [_metric(report_date=date(2025, 12, 31))]
    assert calculate_yoy_growth(metrics) is None


# ── calculate_three_year_cagr ──


def test_three_year_cagr_three_metrics_returns_annualised_percentage() -> None:
    """Locks the contract: CAGR uses latest + oldest of the three most-recent
    metrics, annualised across two compounding periods."""
    metrics = [
        _metric(report_date=date(2023, 12, 31), net_capital="100000.00"),
        _metric(report_date=date(2024, 12, 31), net_capital="120000.00"),
        _metric(report_date=date(2025, 12, 31), net_capital="144000.00"),
    ]
    # latest=144000, oldest=100000  ->  (144000/100000)^(1/2) - 1 = 0.20 -> 20.0%
    assert calculate_three_year_cagr(metrics) == 20.0


def test_three_year_cagr_orders_by_report_date_descending() -> None:
    """Input order must not matter; the function sorts internally."""
    metrics = [
        _metric(report_date=date(2024, 12, 31), net_capital="120000.00"),
        _metric(report_date=date(2025, 12, 31), net_capital="144000.00"),
        _metric(report_date=date(2023, 12, 31), net_capital="100000.00"),
    ]
    assert calculate_three_year_cagr(metrics) == 20.0


def test_three_year_cagr_uses_oldest_of_top_three_when_more_provided() -> None:
    """When 4+ metrics exist, only the latest 3 (by report_date) are used.
    The oldest of those 3 is the denominator, not the absolute oldest."""
    metrics = [
        _metric(report_date=date(2021, 12, 31), net_capital="50000.00"),   # ignored
        _metric(report_date=date(2022, 12, 31), net_capital="50000.00"),   # ignored
        _metric(report_date=date(2023, 12, 31), net_capital="100000.00"),  # oldest of top-3
        _metric(report_date=date(2024, 12, 31), net_capital="120000.00"),
        _metric(report_date=date(2025, 12, 31), net_capital="144000.00"),  # latest
    ]
    assert calculate_three_year_cagr(metrics) == 20.0


def test_three_year_cagr_returns_none_when_fewer_than_three_metrics() -> None:
    """Fail-closed when history is sparse — column stays NULL on the master
    list rather than reporting a noisy growth number from too few periods."""
    metrics = [
        _metric(report_date=date(2024, 12, 31), net_capital="100000.00"),
        _metric(report_date=date(2025, 12, 31), net_capital="120000.00"),
    ]
    assert calculate_three_year_cagr(metrics) is None


def test_three_year_cagr_returns_none_on_empty_metrics() -> None:
    assert calculate_three_year_cagr([]) is None


def test_three_year_cagr_returns_none_when_oldest_is_zero() -> None:
    """Avoid ZeroDivisionError. Growth from a zero base is undefined."""
    metrics = [
        _metric(report_date=date(2023, 12, 31), net_capital="0.00"),
        _metric(report_date=date(2024, 12, 31), net_capital="50000.00"),
        _metric(report_date=date(2025, 12, 31), net_capital="100000.00"),
    ]
    assert calculate_three_year_cagr(metrics) is None


def test_three_year_cagr_returns_none_when_oldest_is_negative() -> None:
    """net_capital is normally positive; a negative base would invert the
    growth sign and round-trip through ``** (1/2)`` in surprising ways.
    Treat as undefined."""
    metrics = [
        _metric(report_date=date(2023, 12, 31), net_capital="-1000.00"),
        _metric(report_date=date(2024, 12, 31), net_capital="50000.00"),
        _metric(report_date=date(2025, 12, 31), net_capital="100000.00"),
    ]
    assert calculate_three_year_cagr(metrics) is None


def test_three_year_cagr_negative_growth() -> None:
    """Negative CAGR is legitimate and returned as-is."""
    metrics = [
        _metric(report_date=date(2023, 12, 31), net_capital="100000.00"),
        _metric(report_date=date(2024, 12, 31), net_capital="80000.00"),
        _metric(report_date=date(2025, 12, 31), net_capital="64000.00"),
    ]
    # (64000/100000)^(1/2) - 1 = 0.8 - 1 = -0.20 -> -20.0%
    assert calculate_three_year_cagr(metrics) == -20.0


def test_three_year_cagr_uses_net_capital_not_total_assets() -> None:
    """Like calculate_yoy_growth, the CAGR helper reads net_capital, not
    total_assets. total_assets has its own helper if/when added."""
    metrics = [
        _metric(report_date=date(2023, 12, 31), net_capital="100000.00", total_assets="999999.00"),
        _metric(report_date=date(2024, 12, 31), net_capital="120000.00", total_assets="111111.00"),
        _metric(report_date=date(2025, 12, 31), net_capital="144000.00", total_assets="11111.00"),
    ]
    assert calculate_three_year_cagr(metrics) == 20.0
