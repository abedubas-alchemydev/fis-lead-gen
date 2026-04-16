"""Batch FOCUS Report CEO + Net Capital extraction for all broker-dealers.

Iterates over every broker-dealer that has SEC filings, downloads the latest
X-17A-5 PDF, and uses Gemini to extract CEO name, phone, email, and net capital.

Usage:
    python -m scripts.run_focus_ceo_extraction                  # all firms, skip already-extracted
    python -m scripts.run_focus_ceo_extraction --limit 50       # first 50 firms only
    python -m scripts.run_focus_ceo_extraction --offset 100     # resume from firm #100
    python -m scripts.run_focus_ceo_extraction --force           # re-extract even if already done

Requirements:
    - GEMINI_API_KEY must be set in .env
    - Database must be populated (run initial_load first)
"""

from __future__ import annotations

import argparse
import asyncio
import selectors
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services.focus_ceo_extraction import FocusCeoExtractionService  # noqa: E402


def _print_section(title: str) -> None:
    print(f"\n{'-' * 60}")
    print(f"  {title}")
    print(f"{'-' * 60}")


async def main(*, offset: int, limit: int | None, force: bool) -> None:
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY is not set in .env. Cannot run extraction.")
        sys.exit(1)

    service = FocusCeoExtractionService()

    _print_section("FOCUS CEO + Net Capital Batch Extraction")
    print(f"  Gemini model:   {settings.gemini_pdf_model}")
    print(f"  Offset:         {offset}")
    print(f"  Limit:          {limit or 'all'}")
    print(f"  Skip existing:  {not force}")
    print()

    # Use a short-lived session ONLY for the initial query (list firms + skip check).
    # Close it immediately before the long batch loop starts, so Neon doesn't
    # kill the idle connection during the hours-long Gemini processing.
    async with SessionLocal() as db:
        counts = await service.run_batch(
            db,
            offset=offset,
            limit=limit,
            skip_existing=not force,
        )
        # Explicitly close before the context manager tries to rollback a dead conn
        await db.close()

    _print_section("BATCH COMPLETE")
    print(f"  Total processed:   {counts['total']:,}")
    print(f"  Successful:        {counts['success']:,}")
    print(f"  Low confidence:    {counts.get('low_confidence', 0):,}")
    print(f"  No PDF available:  {counts['no_pdf']:,}")
    print(f"  Errors:            {counts['error']:,}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch FOCUS Report CEO extraction")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N eligible firms")
    parser.add_argument("--limit", type=int, default=None, help="Max number of firms to process")
    parser.add_argument("--force", action="store_true", help="Re-extract even for firms that already have data")
    args = parser.parse_args()

    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) as runner:
            runner.run(main(offset=args.offset, limit=args.limit, force=args.force))
    else:
        asyncio.run(main(offset=args.offset, limit=args.limit, force=args.force))
