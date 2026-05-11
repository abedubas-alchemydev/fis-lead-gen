"""Audit ``financial_metrics`` rows for the net_capital scale-strip bug.

The financial extractor occasionally drops the "in thousands" multiplier
from the Computation of Net Capital section, producing a value that's
1000× too small. Two production incidents on staging (RBC, DriveWealth)
were both caught only after a user noticed bogus master-list values.

This script finds rows where ``total_assets / net_capital`` exceeds the
:data:`PLAUSIBLE_LEVERAGE_RATIO_MAX` threshold (1000×) — well above any
real BD's leverage, definitely below the bug-shape ratios (~33,000×).

Usage:
    python -m scripts.audit_financial_metrics_scale            # report only
    python -m scripts.audit_financial_metrics_scale --apply    # flag rows
                                                               # needs_review
                                                               # + recompute
                                                               # affected
                                                               # broker_dealer
                                                               # rollups

Read-only by default. ``--apply`` mutates ``extraction_status`` and the
``broker_dealers`` rollup cache for affected firms. Idempotent: running
twice with ``--apply`` is a no-op on rows already marked needs_review.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import selectors
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


AUDIT_NOTE = (
    "[Auto-flagged by scripts/audit_financial_metrics_scale 2026-05-11] "
    "total_assets/net_capital ratio exceeds the plausible-leverage "
    "threshold (1000×). Almost certainly a scale-strip bug in the "
    "financial extractor — net_capital, excess_net_capital, and "
    "required_min_capital should likely be multiplied by 1000. Verify "
    "against the source FOCUS PDF before re-marking as parsed."
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Flag suspect rows as needs_review and refresh BD rollups.",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(2)

    from sqlalchemy import select, text

    from app.db.session import SessionLocal
    from app.models.broker_dealer import BrokerDealer
    from app.models.financial_metric import FinancialMetric
    from app.services.extraction_status import (
        PLAUSIBLE_LEVERAGE_RATIO_MAX,
        STATUS_NEEDS_REVIEW,
        STATUS_PARSED,
        is_plausible_net_capital_scale,
    )
    from app.services.scoring import (
        calculate_three_year_cagr,
        calculate_total_assets_yoy,
        calculate_yoy_growth,
        classify_health_status,
    )

    # Find rows with the bug shape: parsed status, both fields populated,
    # ratio above threshold. Computed in SQL to keep the scan cheap; the
    # Python helper is exercised by tests, this is just a faster pre-filter
    # against the population.
    async with SessionLocal() as db:
        result = await db.execute(
            text(
                """
                SELECT fm.id, fm.bd_id, bd.name AS bd_name,
                       fm.report_date, fm.net_capital, fm.total_assets,
                       fm.excess_net_capital, fm.required_min_capital,
                       fm.source_filing_url
                FROM financial_metrics fm
                JOIN broker_dealers bd ON bd.id = fm.bd_id
                WHERE fm.extraction_status = :status
                  AND fm.total_assets IS NOT NULL
                  AND fm.net_capital > 0
                  AND (fm.total_assets / fm.net_capital) > :threshold
                ORDER BY fm.bd_id, fm.report_date DESC
                """
            ),
            {"status": STATUS_PARSED, "threshold": PLAUSIBLE_LEVERAGE_RATIO_MAX},
        )
        rows = result.fetchall()

    if not rows:
        print(
            f"No financial_metrics rows exceed the plausible-leverage "
            f"threshold ({PLAUSIBLE_LEVERAGE_RATIO_MAX}×). Nothing to do."
        )
        return

    affected_bds: set[int] = {r.bd_id for r in rows}
    print(
        f"Found {len(rows):,} suspect rows across {len(affected_bds):,} "
        f"broker_dealers (threshold > {PLAUSIBLE_LEVERAGE_RATIO_MAX}×)."
    )
    print()
    print(
        f"  {'id':<6} {'BD':<6} {'date':<12} {'net_capital':>14} "
        f"{'total_assets':>16} {'ratio':>10}  Name"
    )
    print("  " + "-" * 110)
    for r in rows:
        nc = float(r.net_capital)
        ta = float(r.total_assets)
        ratio = ta / nc if nc > 0 else float("inf")
        print(
            f"  {r.id:<6} {r.bd_id:<6} {r.report_date.isoformat():<12} "
            f"${nc:>13,.0f}  ${ta:>15,.0f}  {ratio:>9.0f}×  {(r.bd_name or '')[:48]}"
        )
        # Validate via the Python helper too — belt-and-braces, would catch
        # any drift between the SQL pre-filter and the canonical predicate.
        assert not is_plausible_net_capital_scale(
            net_capital=nc, total_assets=ta
        ), f"Row id={r.id} passed SQL filter but Python helper disagreed."

    if not args.apply:
        print()
        print("(read-only — pass --apply to mark these needs_review and "
              "recompute affected broker_dealer rollups)")
        return

    # ── --apply path: flag the rows and refresh rollups ────────────────────
    flagged = 0
    suspect_ids = [r.id for r in rows]
    async with SessionLocal() as db:
        # 1. Flag the suspect rows. financial_metrics has no notes column
        #    today; the audit-trail rationale lives in this script's header
        #    docstring and the issue #398 thread.
        await db.execute(
            text(
                """
                UPDATE financial_metrics
                SET extraction_status = :ns
                WHERE id = ANY(:ids)
                """
            ),
            {"ns": STATUS_NEEDS_REVIEW, "ids": suspect_ids},
        )
        flagged = len(suspect_ids)

        # 2. Recompute each affected BD's rollup from its remaining parsed
        #    metrics. Mirrors services/focus_reports.py::_refresh_broker_dealer_rollups
        #    so the math stays single-source-of-truth.
        for bd_id in affected_bds:
            bd = await db.get(BrokerDealer, bd_id)
            if bd is None:
                continue
            metrics = list(
                (
                    await db.execute(
                        select(FinancialMetric)
                        .where(
                            FinancialMetric.bd_id == bd_id,
                            FinancialMetric.extraction_status == STATUS_PARSED,
                        )
                        .order_by(FinancialMetric.report_date.desc())
                    )
                ).scalars()
            )
            if not metrics:
                # No surviving parsed rows: clear the rollup so the
                # master-list grid doesn't keep stale data.
                bd.latest_net_capital = None
                bd.latest_excess_net_capital = None
                bd.latest_total_assets = None
                bd.required_min_capital = None
                bd.yoy_growth = None
                bd.three_year_cagr = None
                bd.total_assets_yoy = None
                bd.health_status = None
                continue
            latest = metrics[0]
            yoy = calculate_yoy_growth(metrics)
            bd.latest_net_capital = float(latest.net_capital)
            bd.latest_excess_net_capital = (
                float(latest.excess_net_capital)
                if latest.excess_net_capital is not None
                else None
            )
            bd.latest_total_assets = (
                float(latest.total_assets)
                if latest.total_assets is not None
                else None
            )
            bd.required_min_capital = (
                float(latest.required_min_capital)
                if latest.required_min_capital is not None
                else None
            )
            bd.yoy_growth = yoy
            bd.three_year_cagr = calculate_three_year_cagr(metrics)
            bd.total_assets_yoy = calculate_total_assets_yoy(metrics)
            bd.health_status = classify_health_status(
                latest_net_capital=float(latest.net_capital),
                required_min_capital=(
                    float(latest.required_min_capital)
                    if latest.required_min_capital is not None
                    else None
                ),
                yoy_growth=yoy,
            )

        await db.commit()

    print()
    print(
        f"Flagged {flagged} financial_metrics rows as needs_review and "
        f"recomputed rollups for {len(affected_bds)} broker_dealers."
    )


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
