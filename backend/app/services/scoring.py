from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.models.financial_metric import FinancialMetric
from app.models.scoring_setting import ScoringSetting


def calculate_yoy_growth(metrics: Sequence[FinancialMetric]) -> float | None:
    if len(metrics) < 2:
        return None

    ordered = sorted(metrics, key=lambda metric: metric.report_date, reverse=True)
    latest = float(ordered[0].net_capital)
    previous = float(ordered[1].net_capital)

    if previous == 0:
        return None

    return round(((latest - previous) / previous) * 100, 2)


def classify_health_status(
    *,
    latest_net_capital: float | None,
    required_min_capital: float | None,
    yoy_growth: float | None,
) -> str | None:
    if latest_net_capital is None or required_min_capital is None:
        return None

    if required_min_capital <= 0:
        return None

    if latest_net_capital < required_min_capital:
        return "at_risk"

    ratio = latest_net_capital / required_min_capital
    # PRD: Healthy = net_capital > 120% of required min AND positive YoY growth.
    # yoy_growth must be a known positive number — None (unknown) does not qualify.
    if ratio > 1.2 and yoy_growth is not None and yoy_growth > 0:
        return "healthy"

    return "ok"


def calculate_lead_score(
    *,
    yoy_growth: float | None,
    clearing_type: str | None,
    is_competitor: bool,
    health_status: str | None,
    registration_date: date | None,
    weights: ScoringSetting,
) -> float:
    growth_component = _score_growth(yoy_growth)
    clearing_component = _score_clearing(clearing_type, is_competitor)
    health_component = _score_health(health_status)
    recency_component = _score_registration_recency(registration_date)

    weighted_score = (
        growth_component * weights.net_capital_growth_weight
        + clearing_component * weights.clearing_arrangement_weight
        + health_component * weights.financial_health_weight
        + recency_component * weights.registration_recency_weight
    )
    return round(weighted_score, 2)


def classify_lead_priority(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 75:
        return "hot"
    if score >= 45:
        return "warm"
    return "cold"


def _score_growth(yoy_growth: float | None) -> float:
    if yoy_growth is None:
        return 0.25
    if yoy_growth >= 15:
        return 1.0
    if yoy_growth >= 5:
        return 0.75
    if yoy_growth >= 0:
        return 0.5
    return 0.1


def _score_clearing(clearing_type: str | None, is_competitor: bool) -> float:
    if clearing_type == "self_clearing":
        return 1.0
    if is_competitor and clearing_type == "fully_disclosed":
        return 0.95
    if is_competitor:
        return 0.8
    if clearing_type == "fully_disclosed":
        return 0.6
    if clearing_type == "omnibus":
        return 0.45
    return 0.2


def _score_health(health_status: str | None) -> float:
    if health_status == "healthy":
        return 1.0
    if health_status == "ok":
        return 0.6
    if health_status == "at_risk":
        return 0.1
    return 0.25


def _score_registration_recency(registration_date: date | None) -> float:
    if registration_date is None:
        return 0.3

    age_days = max((date.today() - registration_date).days, 0)
    if age_days <= 365:
        return 1.0
    if age_days <= 365 * 3:
        return 0.75
    if age_days <= 365 * 7:
        return 0.5
    return 0.25
