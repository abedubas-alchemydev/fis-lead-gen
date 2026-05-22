"""Standalone refresh-all loop: walk every active broker-dealer with a
NULL critical column and POST the existing ``/refresh-all`` endpoint.

Why this exists. PR #473 added 144 broker-dealers via standalone gap-fill
with only minimal-viable fields. Some older rows from initial_load also
have NULL values in the visible columns (Net Capital, Last Filing,
Clearing Type, Financial Health). The in-tree ``refresh-all`` orchestrator
already fills all of these per-firm via the FINRA Form BD PDF + SEC EDGAR
submissions + X-17A-5 FOCUS reports + LLM extraction. This script just
loops the orchestrator over every BD that still has a gap.

Standalone -- no imports from ``app.*``, ``brokercheck_extractor/``, or
any other project module. Talks to the in-tree backend via HTTP only.

Per-firm flow:
  1. POST ``{base_url}/api/v1/broker-dealers/{id}/refresh-all`` with
     ``{"scope": "list_only"}`` (skips website + contacts which don't
     drive grid columns).
  2. Three terminal response shapes:
       - 202 + ``run_id, status="queued"`` -> poll until terminal
       - 200 + ``status="skipped"`` -> firm already complete, no work
       - 409 + ``detail.run_id`` -> a refresh is already in flight, poll
         that run instead
  3. Poll ``GET /api/v1/pipeline/run/{run_id}`` every ``--poll-seconds``
     (default 5s) until status is terminal: ``completed``,
     ``completed_with_errors``, or ``failed``. Give up after
     ``--max-poll-seconds`` (default 180).
  4. On 429 (per-(user, BD) cooldown) -- respect Retry-After and retry.
     On any other 4xx/5xx -- log and move on.

The scope query targets every BD with at least one NULL among the visible
columns:

    status = 'Active' AND (
        latest_net_capital IS NULL
     OR last_filing_date IS NULL
     OR clearing_classification IS NULL
     OR firm_operations_text IS NULL
     OR direct_owners IS NULL
    )

Auth. The endpoint requires session auth (any authenticated user with
``MASTER_LIST`` feature). The script needs a session cookie:

  1. Log into the target environment in your browser (e.g.
     ``https://staging-dox.alchemydev.io``).
  2. Open DevTools -> Application -> Cookies -> copy the session cookie
     value (typically named ``session`` or ``access_token``).
  3. Pass via ``--cookie 'session=<value>'`` or set ``SESSION_COOKIE``
     in the env.

Usage::

    # dry-run (default): list candidates, don't POST anything
    python scripts/standalone_refresh_all_loop.py

    # apply (sequential)
    python scripts/standalone_refresh_all_loop.py --apply \\
        --base-url https://staging-dox.alchemydev.io \\
        --cookie "$SESSION_COOKIE"

    # smoke-test a single bd
    python scripts/standalone_refresh_all_loop.py --apply \\
        --bd-id 24127 --base-url https://staging-dox.alchemydev.io \\
        --cookie "$SESSION_COOKIE"

    # run a bounded chunk (first 25 from the candidate set)
    python scripts/standalone_refresh_all_loop.py --apply --limit 25 \\
        --base-url https://staging-dox.alchemydev.io --cookie "$SESSION_COOKIE"

Dependencies (already in project requirements): httpx,
sqlalchemy[asyncio], psycopg (v3).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

logger = logging.getLogger("standalone_refresh_all_loop")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


HTTP_TIMEOUT = 30.0
TERMINAL_STATUSES = {"completed", "completed_with_errors", "failed", "skipped"}


@dataclass(frozen=True)
class Candidate:
    bd_id: int
    crd_number: Optional[str]
    name: str
    gaps: str  # comma-separated names of NULL columns


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _gap_string(row: dict) -> str:
    """Render which NULL columns triggered this row's inclusion."""
    fields = [
        ("latest_net_capital", "net_cap"),
        ("last_filing_date", "filing"),
        ("clearing_classification", "clearing"),
        ("firm_operations_text", "ops"),
        ("direct_owners", "owners"),
    ]
    return ",".join(label for col, label in fields if row.get(col) is None)


async def _poll_run(
    client: httpx.AsyncClient,
    base_url: str,
    run_id: int,
    poll_seconds: float,
    max_poll_seconds: float,
) -> tuple[str, Optional[str]]:
    """Poll a PipelineRun by id until terminal. Returns (status, summary)."""
    url = f"{base_url.rstrip('/')}/api/v1/pipeline/run/{run_id}"
    deadline = time.monotonic() + max_poll_seconds
    while time.monotonic() < deadline:
        try:
            resp = await client.get(url, timeout=HTTP_TIMEOUT)
        except httpx.HTTPError as exc:
            logger.warning("run %d: poll network error: %s", run_id, exc)
            await asyncio.sleep(poll_seconds)
            continue
        if resp.status_code != 200:
            logger.warning("run %d: poll HTTP %s", run_id, resp.status_code)
            await asyncio.sleep(poll_seconds)
            continue
        data = resp.json()
        status_value = (data.get("status") or "").lower()
        if status_value in TERMINAL_STATUSES:
            return status_value, data.get("notes")
        await asyncio.sleep(poll_seconds)
    logger.warning("run %d: poll deadline exceeded (%.0fs)", run_id, max_poll_seconds)
    return "timed_out", None


async def _refresh_one(
    client: httpx.AsyncClient,
    base_url: str,
    bd: Candidate,
    poll_seconds: float,
    max_poll_seconds: float,
) -> tuple[str, Optional[int]]:
    """POST refresh-all for one BD and poll to completion.

    Returns (final_status, run_id).
    final_status is one of: completed / completed_with_errors / failed
    / skipped / timed_out / http_error.
    """
    url = f"{base_url.rstrip('/')}/api/v1/broker-dealers/{bd.bd_id}/refresh-all"
    body = {"scope": "list_only"}

    # Up to 3 retries on 429 cooldown
    for cooldown_attempt in range(3):
        try:
            resp = await client.post(url, json=body, timeout=HTTP_TIMEOUT)
        except httpx.HTTPError as exc:
            logger.warning("bd_id=%d: POST network error: %s", bd.bd_id, exc)
            return "http_error", None

        if resp.status_code == 200:
            data = resp.json()
            status_value = (data.get("status") or "").lower()
            logger.info("bd_id=%d (%s): %s (no run)", bd.bd_id, bd.name, status_value)
            return status_value or "skipped", None

        if resp.status_code == 202:
            data = resp.json()
            run_id = data.get("run_id")
            if run_id is None:
                logger.warning("bd_id=%d: 202 with no run_id", bd.bd_id)
                return "http_error", None
            logger.info("bd_id=%d (%s): queued run_id=%d  gaps=%s", bd.bd_id, bd.name, run_id, bd.gaps)
            final_status, _ = await _poll_run(client, base_url, run_id, poll_seconds, max_poll_seconds)
            logger.info("bd_id=%d (%s): final=%s", bd.bd_id, bd.name, final_status)
            return final_status, run_id

        if resp.status_code == 409:
            # In-flight conflict -- detail.run_id points at the existing run.
            data = resp.json()
            in_flight_id = (data.get("detail") or {}).get("run_id") if isinstance(data.get("detail"), dict) else None
            if in_flight_id:
                logger.info("bd_id=%d: 409 in-flight, polling existing run %d", bd.bd_id, in_flight_id)
                final_status, _ = await _poll_run(client, base_url, in_flight_id, poll_seconds, max_poll_seconds)
                return final_status, in_flight_id
            logger.warning("bd_id=%d: 409 with no detail.run_id", bd.bd_id)
            return "http_error", None

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "5"))
            logger.info(
                "bd_id=%d: 429 cooldown (attempt %d), sleeping %.1fs",
                bd.bd_id, cooldown_attempt + 1, retry_after,
            )
            await asyncio.sleep(retry_after)
            continue

        logger.warning("bd_id=%d: unexpected HTTP %s: %s", bd.bd_id, resp.status_code, resp.text[:200])
        return "http_error", None

    logger.warning("bd_id=%d: 429 still active after 3 cooldown waits", bd.bd_id)
    return "http_error", None


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standalone HTTP loop calling refresh-all for every BD with NULL critical columns.",
    )
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually POST refresh-all. Without this, the script lists candidates and exits.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("REFRESH_BASE_URL", "https://staging-dox.alchemydev.io"),
        help="Backend base URL (default: staging).",
    )
    parser.add_argument(
        "--cookie",
        default=os.environ.get("SESSION_COOKIE"),
        help="Browser session cookie header value, e.g. 'session=abc123'. Required for --apply.",
    )
    parser.add_argument(
        "--bd-id",
        type=int,
        default=None,
        help="Restrict to a single broker_dealers.id (smoke-test mode).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of candidate BDs.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="Seconds between poll requests for a queued run (default 5).",
    )
    parser.add_argument(
        "--max-poll-seconds",
        type=float,
        default=180.0,
        help="Per-run polling deadline; abandon a stuck run after this (default 180).",
    )
    args = parser.parse_args()

    if not args.db_url:
        logger.error("no DATABASE_URL env var and no --db-url; aborting")
        return 2
    if args.apply and not args.cookie:
        logger.error("--apply requires a session cookie via --cookie or SESSION_COOKIE env")
        return 2

    db_url = _normalize_db_url(args.db_url)
    engine = create_async_engine(db_url, pool_pre_ping=True)

    try:
        async with engine.connect() as conn:
            if args.bd_id is not None:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT id, crd_number, name, latest_net_capital, last_filing_date, "
                            "       clearing_classification, firm_operations_text, direct_owners "
                            "FROM broker_dealers WHERE id = :id"
                        ),
                        {"id": args.bd_id},
                    )
                ).mappings().all()
            else:
                lim = "" if args.limit is None else f" LIMIT {int(args.limit)}"
                rows = (
                    await conn.execute(
                        text(
                            "SELECT id, crd_number, name, latest_net_capital, last_filing_date, "
                            "       clearing_classification, firm_operations_text, direct_owners "
                            "FROM broker_dealers "
                            "WHERE status = 'Active' AND ("
                            "    latest_net_capital IS NULL "
                            " OR last_filing_date IS NULL "
                            " OR clearing_classification IS NULL "
                            " OR firm_operations_text IS NULL "
                            " OR direct_owners IS NULL"
                            ") "
                            "ORDER BY id" + lim
                        )
                    )
                ).mappings().all()

        candidates = [
            Candidate(bd_id=r["id"], crd_number=r["crd_number"], name=r["name"], gaps=_gap_string(dict(r)))
            for r in rows
        ]
        if not candidates:
            logger.info("no candidate BDs; nothing to do")
            return 0

        logger.info("found %d candidate BD(s) with NULL critical columns", len(candidates))

        if not args.apply:
            logger.info("dry-run: listing candidates (re-run with --apply to POST refresh-all)")
            for c in candidates[:20]:
                logger.info("  bd_id=%d crd=%s name=%r gaps=%s", c.bd_id, c.crd_number, c.name, c.gaps)
            if len(candidates) > 20:
                logger.info("  ... and %d more", len(candidates) - 20)
            return 0

        # --apply: sequential HTTP loop
        headers = {"Cookie": args.cookie} if args.cookie else {}
        counts = {
            "completed": 0,
            "completed_with_errors": 0,
            "failed": 0,
            "skipped": 0,
            "timed_out": 0,
            "http_error": 0,
        }
        run_start = time.time()
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True, headers=headers) as client:
            for i, c in enumerate(candidates, start=1):
                t0 = time.time()
                final_status, run_id = await _refresh_one(
                    client, args.base_url, c, args.poll_seconds, args.max_poll_seconds,
                )
                counts[final_status] = counts.get(final_status, 0) + 1
                dt = time.time() - t0
                elapsed = time.time() - run_start
                avg = elapsed / i
                remaining = avg * (len(candidates) - i)
                logger.info(
                    "progress %d/%d  %.1fs/firm  ETA=%dm%02ds  totals=%s",
                    i, len(candidates), dt,
                    int(remaining // 60), int(remaining % 60),
                    " ".join(f"{k}={v}" for k, v in counts.items() if v > 0),
                )

        logger.info("done. %s", " ".join(f"{k}={v}" for k, v in counts.items() if v > 0))
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
