"""Unit tests for the ACG ICP lead-scoring formula in services.scoring.

These are pure unit tests: no DB, no async, no fixtures. The component
scorers and the composite reader only touch firm-shaped attributes and
the four-column ``ScoringSetting`` row, so a ``SimpleNamespace`` duck
substitute is enough.

Issue #21 — 2026-04-27 client meeting follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.scoring import (
    HOT_THRESHOLD,
    WARM_THRESHOLD,
    CompetitorLookup,
    calculate_lead_score,
    classify_lead_priority,
    score_classification,
    score_competitor_match,
    score_filing_recency,
    score_finra_status,
    score_firm_size,
    score_net_capital,
)


# ── Fake helpers ─────────────────────────────────────────────────────


@dataclass
class _FakeProvider:
    name: str
    aliases: list[str] = field(default_factory=list)
    is_active: bool = True


def _firm(**overrides) -> SimpleNamespace:
    base = dict(
        current_clearing_partner=None,
        clearing_classification=None,
        latest_net_capital=None,
        last_filing_date=None,
        branch_count=None,
        is_deficient=False,
        is_niche_restricted=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _settings(
    *,
    clearing: int = 60,
    health: int = 15,
    growth: int = 10,
    recency: int = 15,
) -> SimpleNamespace:
    return SimpleNamespace(
        clearing_arrangement_weight=clearing,
        financial_health_weight=health,
        net_capital_growth_weight=growth,
        registration_recency_weight=recency,
    )


@pytest.fixture
def lookup() -> CompetitorLookup:
    return CompetitorLookup.from_providers(
        [
            _FakeProvider(name="Pershing LLC", aliases=["Pershing", "BNY Pershing"]),
            _FakeProvider(name="Apex Clearing"),
            _FakeProvider(name="RBC Correspondent Services", aliases=["RBC Corr"]),
            _FakeProvider(name="Hilltop Securities"),
            _FakeProvider(name="Axos Clearing"),
            _FakeProvider(name="Vision Financial Markets"),
        ]
    )


# ── score_competitor_match ──────────────────────────────────────────


@pytest.mark.parametrize(
    "partner,expected",
    [
        ("Pershing LLC", 1.0),
        ("Pershing, LLC", 1.0),
        ("BNY Pershing", 1.0),
        ("Apex Clearing Corp", 1.0),
        ("RBC Correspondent Services", 1.0),
        ("Hilltop Securities Inc.", 1.0),
        ("Goldman Sachs", 0.0),
        ("J.P. Morgan", 0.0),
        ("", 0.0),
        (None, 0.0),
    ],
)
def test_score_competitor_match_partner_lookup(lookup, partner, expected):
    firm = _firm(current_clearing_partner=partner)
    assert score_competitor_match(firm, lookup) == expected


def test_score_competitor_match_inactive_provider_excluded():
    inactive = CompetitorLookup.from_providers(
        [
            _FakeProvider(name="Pershing LLC", is_active=False),
            _FakeProvider(name="Apex Clearing", is_active=True),
        ]
    )
    assert (
        score_competitor_match(_firm(current_clearing_partner="Pershing LLC"), inactive)
        == 0.0
    )
    assert (
        score_competitor_match(_firm(current_clearing_partner="Apex Clearing"), inactive)
        == 1.0
    )


# ── score_classification ────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("fully_disclosed", 1.0),
        ("omnibus", 0.7),
        ("self_clearing", 0.3),
        ("needs_review", 0.0),
        (None, 0.0),
        ("garbage", 0.0),
    ],
)
def test_score_classification(value, expected):
    assert score_classification(_firm(clearing_classification=value)) == expected


# ── score_net_capital ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "net_capital,expected",
    [
        (None, 0.0),
        (0, 0.2),
        (500_000, 0.2),
        (999_999, 0.2),
        (1_000_000, 0.5),
        (5_000_000, 0.5),
        (9_999_999, 0.5),
        (10_000_000, 0.8),
        (50_000_000, 0.8),
        (99_999_999, 0.8),
        (100_000_000, 1.0),
        (500_000_000, 1.0),
    ],
)
def test_score_net_capital_tiers(net_capital, expected):
    value = Decimal(str(net_capital)) if net_capital is not None else None
    assert score_net_capital(_firm(latest_net_capital=value)) == expected


# ── score_filing_recency ────────────────────────────────────────────


@pytest.mark.parametrize(
    "days_ago,expected",
    [
        (0, 1.0),
        (1, 1.0),
        (89, 1.0),
        (90, 1.0),
        (91, 0.7),
        (180, 0.7),
        (181, 0.4),
        (365, 0.4),
        (366, 0.0),
        (1000, 0.0),
    ],
)
def test_score_filing_recency_tiers(days_ago, expected):
    today = date(2026, 4, 29)
    firm = _firm(last_filing_date=today - timedelta(days=days_ago))
    assert score_filing_recency(firm, today=today) == expected


def test_score_filing_recency_null():
    assert score_filing_recency(_firm(last_filing_date=None)) == 0.0


def test_score_filing_recency_future_date_clamps_to_zero_age():
    today = date(2026, 4, 29)
    firm = _firm(last_filing_date=today + timedelta(days=30))
    # max((today - future).days, 0) → 0 → "today" tier → 1.0
    assert score_filing_recency(firm, today=today) == 1.0


# ── score_firm_size ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "branches,expected",
    [
        (None, 0.0),
        (0, 0.0),
        (1, 0.25),
        (4, 0.25),
        (5, 0.5),
        (24, 0.5),
        (25, 0.75),
        (99, 0.75),
        (100, 1.0),
        (500, 1.0),
    ],
)
def test_score_firm_size_tiers(branches, expected):
    assert score_firm_size(_firm(branch_count=branches)) == expected


# ── score_finra_status ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "deficient,niche,expected",
    [
        (False, False, 1.0),
        (False, True, 0.5),
        (True, False, 0.2),
        (True, True, 0.2),
    ],
)
def test_score_finra_status_truth_table(deficient, niche, expected):
    firm = _firm(is_deficient=deficient, is_niche_restricted=niche)
    assert score_finra_status(firm) == expected


# ── classify_lead_priority ──────────────────────────────────────────


@pytest.mark.parametrize(
    "score,expected",
    [
        (None, None),
        (0.0, "cold"),
        (39.99, "cold"),
        (40.0, "warm"),
        (69.99, "warm"),
        (HOT_THRESHOLD, "hot"),
        (WARM_THRESHOLD, "warm"),
        (100.0, "hot"),
    ],
)
def test_classify_lead_priority_thresholds(score, expected):
    assert classify_lead_priority(score) == expected


# ── calculate_lead_score (composite) ────────────────────────────────


def test_composite_pershing_user_fully_disclosed_scores_hot(lookup):
    """ACG ICP smoke: a healthy fully-disclosed firm using Pershing → Hot."""
    today = date(2026, 4, 29)
    firm = _firm(
        current_clearing_partner="Pershing LLC",
        clearing_classification="fully_disclosed",
        latest_net_capital=Decimal("50000000"),  # → 0.8
        last_filing_date=today - timedelta(days=30),  # → 1.0
        branch_count=30,  # → 0.75
        is_deficient=False,
        is_niche_restricted=False,  # → 1.0
    )
    score = calculate_lead_score(
        firm=firm,
        competitor_lookup=lookup,
        weights=_settings(),  # 60/15/10/15
        today=today,
    )
    assert score >= HOT_THRESHOLD
    assert classify_lead_priority(score) == "hot"


def test_composite_self_clearing_no_competitor_scores_cold(lookup):
    today = date(2026, 4, 29)
    firm = _firm(
        current_clearing_partner=None,
        clearing_classification="self_clearing",
        latest_net_capital=Decimal("2000000"),  # → 0.5
        last_filing_date=today - timedelta(days=200),  # → 0.4
        branch_count=2,  # → 0.25
        is_deficient=False,
        is_niche_restricted=False,  # → 1.0
    )
    score = calculate_lead_score(
        firm=firm,
        competitor_lookup=lookup,
        weights=_settings(),
        today=today,
    )
    assert score < WARM_THRESHOLD
    assert classify_lead_priority(score) == "cold"


def test_composite_needs_review_scores_cold(lookup):
    today = date(2026, 4, 29)
    firm = _firm(
        current_clearing_partner="Some Random Bank",
        clearing_classification="needs_review",
        latest_net_capital=None,
        last_filing_date=None,
        branch_count=0,
        is_deficient=False,
        is_niche_restricted=False,
    )
    score = calculate_lead_score(
        firm=firm,
        competitor_lookup=lookup,
        weights=_settings(),
        today=today,
    )
    assert score < WARM_THRESHOLD
    assert classify_lead_priority(score) == "cold"


def test_composite_respects_custom_weights_zeroing_clearing(lookup):
    """If admin zeros the clearing bucket, competitor signal is ignored."""
    today = date(2026, 4, 29)
    firm = _firm(
        current_clearing_partner="Pershing LLC",
        clearing_classification="fully_disclosed",
        latest_net_capital=Decimal("500"),
        last_filing_date=today - timedelta(days=400),
        branch_count=0,
        is_deficient=True,
    )
    score = calculate_lead_score(
        firm=firm,
        competitor_lookup=lookup,
        weights=_settings(clearing=0, health=50, growth=25, recency=25),
        today=today,
    )
    assert score < WARM_THRESHOLD


def test_composite_zero_total_weights_returns_zero(lookup):
    firm = _firm(
        current_clearing_partner="Pershing LLC",
        clearing_classification="fully_disclosed",
        latest_net_capital=Decimal("100000000"),
        branch_count=200,
    )
    score = calculate_lead_score(
        firm=firm,
        competitor_lookup=lookup,
        weights=_settings(clearing=0, health=0, growth=0, recency=0),
    )
    assert score == 0.0


def test_composite_known_arithmetic(lookup):
    """Spot-check the weighted-sum arithmetic against hand-computed values."""
    today = date(2026, 4, 29)
    firm = _firm(
        current_clearing_partner="Apex Clearing",  # competitor → 1.0
        clearing_classification="omnibus",  # → 0.7
        latest_net_capital=Decimal("15000000"),  # → 0.8
        last_filing_date=today - timedelta(days=100),  # → 0.7
        branch_count=10,  # → 0.5
        is_deficient=False,
        is_niche_restricted=True,  # → 0.5
    )
    score = calculate_lead_score(
        firm=firm,
        competitor_lookup=lookup,
        weights=_settings(),
        today=today,
    )
    expected_clearing = (40 / 60) * 1.0 + (20 / 60) * 0.7
    expected_recency = (10 / 15) * 0.5 + (5 / 15) * 0.5
    weighted = (
        60 * expected_clearing
        + 15 * 0.8
        + 10 * 0.7
        + 15 * expected_recency
    )
    expected = round(weighted / 100 * 100, 2)
    assert score == expected
