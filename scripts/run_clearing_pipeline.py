from __future__ import annotations

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

from app.db.session import SessionLocal  # noqa: E402
from app.services.pipeline import ClearingPipelineService  # noqa: E402


async def main() -> None:
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    service = ClearingPipelineService()
    async with SessionLocal() as db:
        run = await service.run(db)
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
