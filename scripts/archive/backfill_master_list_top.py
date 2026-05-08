"""Backfill missing master-list grid data for the top N rows.

Reuses the per-firm refresh-all orchestrator with ``scope="all"`` minus
the Apollo contact-enrichment sub-pipeline. Fills:

    Clearing Partner   -> current_clearing_partner   (health-check; classification only — partner naming comes from scripts.run_clearing_pipeline's LLM extraction over X-17A-5 PDFs)
    Clearing Type      -> current_clearing_type      (health-check + run_clearing_pipeline)
    Financial Health   -> health_status              (refresh-financials)
    Net Capital        -> latest_net_capital         (refresh-financials)
    YoY Growth         -> yoy_growth                 (refresh-financials)
    Last Filing        -> last_filing_date           (refresh-filings)
    Prospect Priority  -> lead_priority              (scoring, end-of-run)
    Registration Date  -> registration_date          (health-check, FINRA Form BD)
    Formation Date     -> formation_date             (health-check, FINRA Form BD)
    Website            -> website                    (resolve-website chain: Apollo / Hunter / SerpAPI fallback)

Apollo executive-contact enrichment (``enrich`` sub-pipeline) is force-
skipped — emails / phones aren't on the master-list grid and the FE has
its own dedicated "Find emails" button for per-officer discovery.

Mirrors the master-list page's default query: ``list_mode='primary'``
(``is_deficient = false``), ``ORDER BY name ASC NULLS LAST, id ASC``,
``LIMIT N``. Sub-pipeline gates already short-circuit when their target
column is populated, so this never overwrites existing data.

Driven sequentially per BD: each refresh-all is internally parallel via
``asyncio.gather``, so stacking BDs on top would multiply provider
concurrency. Provider cost per firm with everything still NULL: ~2
Gemini calls (financials) + ~1 Apollo call + ~1 Hunter call (resolve-
website chain) ≈ $0.05 / firm.

Usage (from repo root):
    python -m scripts.backfill_master_list_top
    python -m scripts.backfill_master_list_top --dry-run
    python -m scripts.backfill_master_list_top --limit 10
    python -m scripts.backfill_master_list_top --skip-scoring
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import selectors
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import or_, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.models.pipeline_run import PipelineRun  # noqa: E402
from app.services.refresh_all_orchestrator import (  # noqa: E402
    REFRESH_ALL_PIPELINE_NAME,
    SUB_ENRICH,
    GateDecision,
    decide_pipelines,
    has_executive_contacts,
    run_refresh_all,
)
from app.services.scoring import score_broker_dealers  # noqa: E402

logger = logging.getLogger(__name__)

TRIGGER_SOURCE = "script:backfill_master_list_top"

TARGET_COLUMNS: tuple[str, ...] = (
    "current_clearing_partner",
    "current_clearing_type",
    "health_status",
    "latest_net_capital",
    "yoy_growth",
    "last_filing_date",
    "lead_priority",
)


def _select_top_stmt(list_mode: str, limit: int, offset: int = 0):
    """Build the same query the master-list FE issues on initial load."""
    stmt = select(BrokerDealer)
    if list_mode == "primary":
        stmt = stmt.where(BrokerDealer.is_deficient.is_(False))
    elif list_mode == "alternative":
        stmt = stmt.where(
            or_(
                BrokerDealer.is_deficient.is_(True),
                BrokerDealer.health_status == "at_risk",
            )
        )
    return stmt.order_by(
        BrokerDealer.name.asc().nullslast(),
        BrokerDealer.id.asc(),
    ).offset(offset).limit(limit)


def _null_count(rows, attr: str) -> int:
    return sum(1 for r in rows if getattr(r, attr) is None)


def _strip_enrich(decision: GateDecision) -> GateDecision:
    """Move SUB_ENRICH from to_run into to_skip.

    The recipe runs ``scope="all"`` so ``resolve-website`` is included
    (the firm-detail page's Website cell is otherwise stuck NULL on
    every firm whose initial-load FINRA enrichment didn't pick up a
    homepage). But the Apollo contact-enrichment sub-pipeline is a
    separate cost concern — emails / phones aren't part of the
    master-list grid and the FE has its own dedicated "Find emails"
    button when the operator wants per-officer enrichment. Strip
    ``enrich`` so the recipe stays scoped to the master-list +
    detail-page columns we actually care about for backfill.
    """
    to_run = tuple(name for name in decision.to_run if name != SUB_ENRICH)
    to_skip = tuple(decision.to_skip) + (
        (SUB_ENRICH,) if SUB_ENRICH in decision.to_run else ()
    )
    # If SUB_ENRICH was already in to_skip, dedupe.
    seen: set[str] = set()
    deduped_skip = tuple(name for name in to_skip if not (name in seen or seen.add(name)))
    return GateDecision(to_run=to_run, to_skip=deduped_skip)


async def _process_bd(bd: BrokerDealer, *, dry_run: bool) -> dict:
    """Drive refresh-all for one BD and return a per-BD result row."""
    async with SessionLocal() as db:
        has_contacts = await has_executive_contacts(db, bd.id)
        decision = _strip_enrich(
            decide_pipelines(bd, has_contacts=has_contacts, scope="all")
        )

        if not decision.to_run:
            return {
                "bd_id": bd.id,
                "name": bd.name,
                "ran": [],
                "skipped": list(decision.to_skip),
                "outcome": "skipped_already_complete",
            }

        if dry_run:
            return {
                "bd_id": bd.id,
                "name": bd.name,
                "ran": list(decision.to_run),
                "skipped": list(decision.to_skip),
                "outcome": "dry_run",
            }

        parent = PipelineRun(
            pipeline_name=REFRESH_ALL_PIPELINE_NAME,
            trigger_source=TRIGGER_SOURCE,
            status="queued",
            total_items=len(decision.to_run),
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes=json.dumps(
                {
                    "bd_id": bd.id,
                    "stage": "queued",
                    "scope": "all_minus_enrich",
                    "ran": list(decision.to_run),
                    "skipped": list(decision.to_skip),
                }
            ),
        )
        db.add(parent)
        await db.commit()
        await db.refresh(parent)
        parent_id = parent.id

    await run_refresh_all(
        parent_id,
        bd.id,
        trigger_source=TRIGGER_SOURCE,
        pipelines_to_run=decision.to_run,
        pipelines_to_skip=decision.to_skip,
    )

    async with SessionLocal() as db:
        run = await db.get(PipelineRun, parent_id)
        terminal_status = run.status if run else "unknown"

    return {
        "bd_id": bd.id,
        "name": bd.name,
        "parent_run_id": parent_id,
        "ran": list(decision.to_run),
        "skipped": list(decision.to_skip),
        "outcome": terminal_status,
    }


async def main(*, list_mode: str, limit: int, offset: int, dry_run: bool, skip_scoring: bool) -> None:
    started_at = time.monotonic()
    print(
        f"backfill_master_list_top: list_mode={list_mode} limit={limit} "
        f"offset={offset} dry_run={dry_run} skip_scoring={skip_scoring}"
    )

    async with SessionLocal() as db:
        rows = (await db.execute(_select_top_stmt(list_mode, limit, offset))).scalars().all()
        before_nulls = {col: _null_count(rows, col) for col in TARGET_COLUMNS}
        bd_ids = [r.id for r in rows]

    print(f"selected {len(rows)} BDs")
    print("pre-run NULL counts (across selected set):")
    for col in TARGET_COLUMNS:
        print(f"  {col:28s} null={before_nulls[col]:>3d}")

    results: list[dict] = []
    for idx, bd in enumerate(rows, 1):
        print(f"[{idx}/{len(rows)}] BD {bd.id} {bd.name!r} ...", flush=True)
        result = await _process_bd(bd, dry_run=dry_run)
        results.append(result)
        print(
            f"  -> outcome={result['outcome']} "
            f"ran={result['ran']} skipped={result['skipped']}"
        )

    if not dry_run and not skip_scoring:
        print("\nscoring NULL-priority BDs (DB-wide; pure compute, no provider calls)...")
        async with SessionLocal() as db:
            scoring_summary = await score_broker_dealers(
                db,
                only_null_priority=True,
                limit=None,
                dry_run=False,
            )
            await db.commit()
        print(
            f"  scored={scoring_summary.scored} "
            f"target_count={scoring_summary.target_count} "
            f"skipped_no_data={scoring_summary.skipped_no_data}"
        )

    if not dry_run and bd_ids:
        async with SessionLocal() as db:
            after_stmt = select(BrokerDealer).where(BrokerDealer.id.in_(bd_ids))
            after_rows = (await db.execute(after_stmt)).scalars().all()
        after_nulls = {col: _null_count(after_rows, col) for col in TARGET_COLUMNS}
        print("\npost-run NULL counts (same BD IDs):")
        for col in TARGET_COLUMNS:
            delta = before_nulls[col] - after_nulls[col]
            sign = f"-{delta}" if delta > 0 else ("0" if delta == 0 else f"+{-delta}")
            print(f"  {col:28s} null={after_nulls[col]:>3d}  (delta {sign})")

    outcomes: dict[str, int] = {}
    for r in results:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    print("\noutcome tally:")
    for k, v in sorted(outcomes.items()):
        print(f"  {k}: {v}")

    elapsed = time.monotonic() - started_at
    print(f"\ntotal elapsed: {elapsed:.1f}s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fill missing master-list grid columns (clearing, financials, "
            "filings, lead priority) for the top-N rows in the default "
            "list view by reusing the refresh-all orchestrator + scoring."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Top-N rows to process (default: 25, matching the FE page size).",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first N rows in the same alphabetical order. Use with --limit "
        "to chunk a backfill across multiple foreground invocations when the full "
        "set won't fit in one timeout window.",
    )
    parser.add_argument(
        "--list-mode",
        default="primary",
        choices=("primary", "alternative", "all"),
        help="Match the FE list filter (default: primary = is_deficient=false).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print which BDs and which sub-pipelines would run; no DB writes, no provider calls.",
    )
    parser.add_argument(
        "--skip-scoring",
        action="store_true",
        help="Skip the trailing score_broker_dealers call.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    coro = main(
        list_mode=args.list_mode,
        limit=args.limit,
        offset=args.offset,
        dry_run=args.dry_run,
        skip_scoring=args.skip_scoring,
    )
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(coro)
    else:
        asyncio.run(coro)
