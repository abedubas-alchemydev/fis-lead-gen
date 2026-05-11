"""Audit ``financial_metrics`` rows whose ``report_date`` is not a
month-end — the bug shape from issue #398.

A legitimate X-17A-5 period-end falls on the last day of a calendar
month. Mid-month report_dates almost always mean the extractor grabbed
the filing date by mistake, producing a row that pollutes YoY / CAGR
rollups even when the dollar figures look fine.

This script mirrors ``audit_financial_metrics_scale.py``: read-only
report by default, ``--apply`` flips the suspect rows to
``needs_review`` and recomputes the affected ``broker_dealers``
rollup cache.

Usage:
    python -m scripts.audit_financial_metrics_report_date         # report
    python -m scripts.audit_financial_metrics_report_date --apply # flag

Idempotent on rows already marked ``needs_review``.
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
        STATUS_NEEDS_REVIEW,
        STATUS_PARSED,
        is_plausible_report_date,
    )
    from app.services.scoring import (
        calculate_three_year_cagr,
        calculate_total_assets_yoy,
        calculate_yoy_growth,
        classify_health_status,
    )

    # SQL pre-filter: any non-last-day-of-month date. Computing
    # last-day-of-month in pure ANSI SQL is awkward, so pull all parsed
    # rows and let the Python helper sift. financial_metrics is small
    # (~4k rows) — full scan is fine.
    async with SessionLocal() as db:
        result = await db.execute(
            text(
                """
                SELECT fm.id, fm.bd_id, bd.name AS bd_name,
                       fm.report_date, fm.net_capital, fm.total_assets,
                       fm.source_filing_url
                FROM financial_metrics fm
                JOIN broker_dealers bd ON bd.id = fm.bd_id
                WHERE fm.extraction_status = :status
                ORDER BY fm.bd_id, fm.report_date DESC
                """
            ),
            {"status": STATUS_PARSED},
        )
        all_parsed = result.fetchall()

    suspects = [r for r in all_parsed if not is_plausible_report_date(r.report_date)]

    if not suspects:
        print(
            f"No parsed financial_metrics rows have a mid-month report_date. "
            f"Scanned {len(all_parsed):,} parsed rows. Nothing to do."
        )
        return

    affected_bds: set[int] = {r.bd_id for r in suspects}
    print(
        f"Found {len(suspects):,} suspect rows across {len(affected_bds):,} "
        f"broker_dealers (parsed status, non-month-end report_date)."
    )
    print()
    print(
        f"  {'id':<6} {'BD':<6} {'date':<12} {'net_capital':>14} "
        f"{'total_assets':>16}  Name"
    )
    print("  " + "-" * 110)
    for r in suspects:
        nc = float(r.net_capital) if r.net_capital is not None else None
        ta = float(r.total_assets) if r.total_assets is not None else None
        nc_str = f"${nc:>13,.0f}" if nc is not None else "         -"
        ta_str = f"${ta:>15,.0f}" if ta is not None else "             -"
        print(
            f"  {r.id:<6} {r.bd_id:<6} {r.report_date.isoformat():<12} "
            f"{nc_str:>14} {ta_str:>16}  {(r.bd_name or '')[:48]}"
        )

    if not args.apply:
        print()
        print("(read-only — pass --apply to mark these needs_review and "
              "recompute affected broker_dealer rollups)")
        return

    suspect_ids = [r.id for r in suspects]
    async with SessionLocal() as db:
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
