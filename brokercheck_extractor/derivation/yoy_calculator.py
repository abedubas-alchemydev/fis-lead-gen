"""
Year-over-year growth calculator for Net Capital and Total Assets.

Given a current-period FocusReport and a prior-period FocusReport, computes
growth as (current - prior) / prior. If either side is missing the value,
returns a YoYGrowth marked `insufficient_data`.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..schema.models import FocusReport, YoYGrowth


def compute_yoy(
    current: Optional[Decimal],
    prior: Optional[Decimal],
) -> YoYGrowth:
    if current is None or prior is None or prior == 0:
        return YoYGrowth(
            current_value=current,
            prior_value=prior,
            growth_pct=None,
            insufficient_data=True,
        )

    growth = float((current - prior) / prior)
    return YoYGrowth(
        current_value=current,
        prior_value=prior,
        growth_pct=growth,
        insufficient_data=False,
    )


def compute_all_yoy(
    current: Optional[FocusReport],
    prior: Optional[FocusReport],
) -> dict[str, YoYGrowth]:
    """Return YoY growth for the two client-required metrics."""
    cur_fin = current.financials if current else None
    prior_fin = prior.financials if prior else None

    return {
        "net_capital_yoy": compute_yoy(
            cur_fin.net_capital if cur_fin else None,
            prior_fin.net_capital if prior_fin else None,
        ),
        "total_assets_yoy": compute_yoy(
            cur_fin.total_assets if cur_fin else None,
            prior_fin.total_assets if prior_fin else None,
        ),
    }
