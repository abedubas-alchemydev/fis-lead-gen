from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from app.models.financial_metric import FinancialMetric
from app.models.scoring_setting import ScoringSetting

if TYPE_CHECKING:
    from app.models.broker_dealer import BrokerDealer
    from app.models.competitor_provider import CompetitorProvider


# ── ACG ICP weight-bucket internal splits ──
# Admin configures four weight buckets via /settings (basis points summing
# to ~100). Inside each bucket, we apply ACG ICP's recommended sub-weighting
# via fixed ratios so the composite encodes 40/20/15/10/10/5 across six
# logical components without expanding the storage schema.
#
#   clearing_bucket  = 0.667 * competitor_match + 0.333 * classification
#                      (40/(40+20) and 20/(40+20))
#   recency_bucket   = 0.667 * firm_size + 0.333 * finra_status
#                      (10/(10+5) and 5/(10+5))
CLEARING_COMPETITOR_SHARE = 40 / 60
CLEARING_CLASSIFICATION_SHARE = 20 / 60
RECENCY_FIRM_SIZE_SHARE = 10 / 15
RECENCY_FINRA_STATUS_SHARE = 5 / 15


# ── Hot/Warm/Cold thresholds on the 0–100 stored scale ──
HOT_THRESHOLD = 70.0
WARM_THRESHOLD = 40.0


# ── Net-capital tier boundaries (USD) ──
NET_CAPITAL_TIER_TINY = 1_000_000
NET_CAPITAL_TIER_SMALL = 10_000_000
NET_CAPITAL_TIER_MID = 100_000_000


@dataclass(frozen=True)
class CompetitorLookup:
    """Pre-flattened lowercase tokens for competitor name + aliases."""

    tokens: tuple[str, ...]

    @classmethod
    def from_providers(cls, providers: Iterable["CompetitorProvider"]) -> "CompetitorLookup":
        collected: list[str] = []
        for provider in providers:
            if not provider.is_active:
                continue
            collected.append(provider.name.strip().lower())
            for alias in provider.aliases or []:
                if not isinstance(alias, str):
                    continue
                token = alias.strip().lower()
                if token:
                    collected.append(token)
        seen: set[str] = set()
        deduped: list[str] = []
        # Longest-first so a specific alias wins over a generic one.
        for token in sorted(collected, key=len, reverse=True):
            if not token or token in seen:
                continue
            seen.add(token)
            deduped.append(token)
        return cls(tokens=tuple(deduped))

    def matches(self, partner: str | None) -> bool:
        if not partner:
            return False
        haystack = partner.strip().lower()
        if not haystack:
            return False
        return any(token in haystack for token in self.tokens)


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


# ── Component scoring functions (each returns 0.0 – 1.0) ──

def score_competitor_match(firm: "BrokerDealer", lookup: CompetitorLookup) -> float:
    """ACG primary signal: 1.0 if firm clears through a tracked competitor."""
    return 1.0 if lookup.matches(firm.current_clearing_partner) else 0.0


def score_classification(firm: "BrokerDealer") -> float:
    """ACG secondary signal: clearing-relationship classification from #19."""
    classification = firm.clearing_classification
    if classification == "fully_disclosed":
        return 1.0
    if classification == "omnibus":
        return 0.7
    if classification == "self_clearing":
        return 0.3
    # needs_review and null are not actionable.
    return 0.0


def score_net_capital(firm: "BrokerDealer") -> float:
    """Log-scale net capital tiers (firm financial substance)."""
    if firm.latest_net_capital is None:
        return 0.0
    value = float(firm.latest_net_capital)
    if value >= NET_CAPITAL_TIER_MID:
        return 1.0
    if value >= NET_CAPITAL_TIER_SMALL:
        return 0.8
    if value >= NET_CAPITAL_TIER_TINY:
        return 0.5
    return 0.2


def score_filing_recency(firm: "BrokerDealer", *, today: date | None = None) -> float:
    """Days since last filing — proxy for ongoing operations."""
    if firm.last_filing_date is None:
        return 0.0
    reference = today or date.today()
    age_days = max((reference - firm.last_filing_date).days, 0)
    if age_days <= 90:
        return 1.0
    if age_days <= 180:
        return 0.7
    if age_days <= 365:
        return 0.4
    return 0.0


def score_firm_size(firm: "BrokerDealer") -> float:
    """Branch count as size proxy."""
    branches = firm.branch_count
    if branches is None or branches < 1:
        return 0.0
    if branches >= 100:
        return 1.0
    if branches >= 25:
        return 0.75
    if branches >= 5:
        return 0.5
    return 0.25


def score_finra_status(firm: "BrokerDealer") -> float:
    """Compliance signals from FINRA flags."""
    if firm.is_deficient:
        return 0.2
    if firm.is_niche_restricted:
        return 0.5
    return 1.0


def calculate_lead_score(
    *,
    firm: "BrokerDealer",
    competitor_lookup: CompetitorLookup,
    weights: ScoringSetting,
    today: date | None = None,
) -> float:
    """Composite ACG ICP lead score, returned on the 0.0–100.0 stored scale."""
    competitor = score_competitor_match(firm, competitor_lookup)
    classification = score_classification(firm)
    net_cap = score_net_capital(firm)
    recency = score_filing_recency(firm, today=today)
    size = score_firm_size(firm)
    finra = score_finra_status(firm)

    clearing_bucket = (
        CLEARING_COMPETITOR_SHARE * competitor
        + CLEARING_CLASSIFICATION_SHARE * classification
    )
    health_bucket = net_cap
    growth_bucket = recency
    recency_bucket = (
        RECENCY_FIRM_SIZE_SHARE * size
        + RECENCY_FINRA_STATUS_SHARE * finra
    )

    weight_total = (
        weights.clearing_arrangement_weight
        + weights.financial_health_weight
        + weights.net_capital_growth_weight
        + weights.registration_recency_weight
    )
    if weight_total <= 0:
        return 0.0

    weighted = (
        weights.clearing_arrangement_weight * clearing_bucket
        + weights.financial_health_weight * health_bucket
        + weights.net_capital_growth_weight * growth_bucket
        + weights.registration_recency_weight * recency_bucket
    )
    composite = weighted / weight_total
    return round(composite * 100, 2)


def classify_lead_priority(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= HOT_THRESHOLD:
        return "hot"
    if score >= WARM_THRESHOLD:
        return "warm"
    return "cold"
