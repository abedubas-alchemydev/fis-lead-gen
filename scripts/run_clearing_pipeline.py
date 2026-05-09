from __future__ import annotations

import argparse
import asyncio
import selectors
import sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.services.pipeline import ClearingPipelineService  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the clearing-arrangement extraction pipeline. "
            "Defaults to the standard target selector (firms with a filings "
            "index but no clearing row, then refresh tail)."
        )
    )
    parser.add_argument(
        "--only-failed",
        action="store_true",
        help="Re-run only firms whose previous clearing extraction failed.",
    )
    parser.add_argument(
        "--only-null-partner",
        action="store_true",
        help=(
            "Backfill mode: target only broker-dealers whose "
            "current_clearing_partner is NULL but have a filings_index_url "
            "on file. Mutually exclusive with --only-failed."
        ),
    )
    parser.add_argument(
        "--only-needs-review",
        action="store_true",
        help=(
            "Targeted re-run mode: target only broker-dealers whose latest "
            "clearing_arrangements row landed as extraction_status='needs_review'. "
            "Used after a prompt change to flip the fixable subset (e.g. "
            "no-customer-accounts firms) without re-processing the already-parsed "
            "universe. Mutually exclusive with --only-failed and --only-null-partner."
        ),
    )
    parser.add_argument(
        "--bd-ids",
        type=str,
        default=None,
        help=(
            "Comma-separated list of broker-dealer IDs to target explicitly. "
            "When set, supersedes the selector flags — the pipeline runs over "
            "exactly the BDs you name. Use this for one-off backfills scoped "
            "to a specific cohort (e.g., the master-list top-25)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the target broker-dealer count (and a sample of IDs) "
            "without invoking Gemini or writing to the database."
        ),
    )
    return parser.parse_args()


def _parse_bd_ids(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    ids = [int(token.strip()) for token in raw.split(",") if token.strip()]
    if not ids:
        raise SystemExit("--bd-ids was provided but parsed to an empty list.")
    return ids


async def _print_dry_run_targets(
    *,
    only_null_partner: bool,
    only_failed: bool,
    only_needs_review: bool,
    target_bd_ids: list[int] | None,
) -> None:
    service = ClearingPipelineService()
    async with SessionLocal() as db:
        if target_bd_ids is not None:
            fetched = (
                await db.execute(
                    select(BrokerDealer).where(BrokerDealer.id.in_(target_bd_ids))
                )
            ).scalars().all()
            by_id = {bd.id: bd for bd in fetched}
            broker_dealers = [by_id[i] for i in target_bd_ids if i in by_id]
            mode = "target_bd_ids"
        elif only_null_partner:
            broker_dealers = await service._select_null_partner_targets(db)
            mode = "only_null_partner"
        elif only_failed:
            all_bds = (
                await db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))
            ).scalars().all()
            failed_ids = await service.repository.list_failed_clearing_broker_dealer_ids(db)
            broker_dealers = [item for item in all_bds if item.id in failed_ids]
            mode = "only_failed"
        elif only_needs_review:
            broker_dealers = await service._select_needs_review_targets(db)
            mode = "only_needs_review"
        else:
            broker_dealers = await service._select_default_targets(db)
            mode = "default"

    sample_ids = [bd.id for bd in broker_dealers[:25]]
    target_count = len(broker_dealers)
    print(
        "Clearing pipeline dry-run:",
        {
            "mode": mode,
            "target_count": target_count,
            "sample_bd_ids": sample_ids,
        },
    )
    # The targeted needs-review re-run is operated by counters the planner
    # quotes back in the canary checklist — emit them as a flat readable
    # line in addition to the structured payload so the operator does not
    # have to grep the dict.
    if only_needs_review:
        print(
            f"target_needs_review={target_count}, would_attempt={target_count}"
        )


async def main() -> None:
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    args = _parse_args()

    if sum([args.only_failed, args.only_null_partner, args.only_needs_review]) > 1:
        raise SystemExit(
            "--only-failed, --only-null-partner, and --only-needs-review are mutually exclusive."
        )

    target_bd_ids = _parse_bd_ids(args.bd_ids)

    if args.dry_run:
        await _print_dry_run_targets(
            only_null_partner=args.only_null_partner,
            only_failed=args.only_failed,
            only_needs_review=args.only_needs_review,
            target_bd_ids=target_bd_ids,
        )
        return

    service = ClearingPipelineService()
    async with SessionLocal() as db:
        run = await service.run(
            db,
            only_failed=args.only_failed,
            only_null_partner=args.only_null_partner,
            only_needs_review=args.only_needs_review,
            target_bd_ids=target_bd_ids,
        )
    print(
        "Clearing pipeline completed:",
        {
            "status": run.status,
            "processed": run.processed_items,
            "success_count": run.success_count,
            "failure_count": run.failure_count,
            "notes": run.notes,
        },
    )


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
