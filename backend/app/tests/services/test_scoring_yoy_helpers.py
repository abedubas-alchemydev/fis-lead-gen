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
from app.services.scoring import calculate_total_assets_yoy, calculate_yoy_growth


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
