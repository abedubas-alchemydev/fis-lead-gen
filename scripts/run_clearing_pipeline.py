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
        "--dry-run",
        action="store_true",
        help=(
            "Print the target broker-dealer count (and a sample of IDs) "
            "without invoking Gemini or writing to the database."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Cap the number of broker-dealers processed in this run. "
            "Overrides settings.clearing_pipeline_limit for the lifetime "
            "of the process. Useful for canary runs."
        ),
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=None,
        help=(
            "Skip this many broker-dealers before processing. Overrides "
            "settings.clearing_pipeline_offset for the lifetime of the "
            "process."
        ),
    )
    return parser.parse_args()


async def _print_dry_run_targets(*, only_null_partner: bool, only_failed: bool) -> None:
    service = ClearingPipelineService()
    async with SessionLocal() as db:
        if only_null_partner:
            broker_dealers = await service._select_null_partner_targets(db)
            mode = "only_null_partner"
        elif only_failed:
            all_bds = (
                await db.execute(select(BrokerDealer).order_by(BrokerDealer.id.asc()))
            ).scalars().all()
            failed_ids = await service.repository.list_failed_clearing_broker_dealer_ids(db)
            broker_dealers = [item for item in all_bds if item.id in failed_ids]
            mode = "only_failed"
        else:
            broker_dealers = await service._select_default_targets(db)
            mode = "default"

    sample_ids = [bd.id for bd in broker_dealers[:25]]
    print(
        "Clearing pipeline dry-run:",
        {
            "mode": mode,
            "target_count": len(broker_dealers),
            "sample_bd_ids": sample_ids,
        },
    )


async def main() -> None:
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    args = _parse_args()

    if args.only_failed and args.only_null_partner:
        raise SystemExit("--only-failed and --only-null-partner are mutually exclusive.")

    # CLI flags take precedence over the env-driven settings for the
    # lifetime of this process. _select_*_targets() reads these values
    # off ``settings`` directly, so writing through here covers both the
    # default and only-null-partner paths without touching the service.
    from app.core.config import settings  # noqa: E402

    if args.limit is not None:
        settings.clearing_pipeline_limit = args.limit
    if args.offset is not None:
        settings.clearing_pipeline_offset = args.offset

    if args.dry_run:
        await _print_dry_run_targets(
            only_null_partner=args.only_null_partner,
            only_failed=args.only_failed,
        )
        return

    service = ClearingPipelineService()
    async with SessionLocal() as db:
        run = await service.run(
            db,
            only_failed=args.only_failed,
            only_null_partner=args.only_null_partner,
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
