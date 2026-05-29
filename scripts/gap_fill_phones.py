"""Phase 5 of the unified backfill: bulk phone-reveal gap-fill for BDs + 13F IAs.

Picks firms whose contact rows still lack a phone and runs the per-firm
contact-discovery gap-fill on each — the same path the "Enrich all" button uses
(PDL phones synchronously + Apollo async phone-reveal via webhook). Apollo bills
per mobile reveal, so this is the metered phase: default --limit 50 firms/run
plus a soft --reveal-cap on estimated reveals.

WHY A SEPARATE DEDUP (not last_gap_fill_attempt_at): that column is shared with
the refresh gap-fill (gap_fill_broker_dealers / gap_fill_investment_advisors), so
keying phone selection off it would make phones skip every firm refresh just
stamped. Instead we dedup off the pipeline_runs history — skip firms with a
completed phone-gap-fill run in the last 30 days. (The per-firm gap-fill still
stamps last_gap_fill_attempt_at; that's harmless here — refresh for the firm is
already done, and phones select independently.)

Usage (from repo root):
    python -m scripts.gap_fill_phones --scan-only
    python -m scripts.gap_fill_phones --apply --limit 50
    python -m scripts.gap_fill_phones --apply --kind bd --limit 25 --reveal-cap 200
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.pipeline_run import PipelineRun  # noqa: E402
from app.services.advisor_refresh_orchestrator import (  # noqa: E402
    GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME,
    run_gap_fill_advisor_contacts_background,
)
from app.services.bd_contact_gap_fill import (  # noqa: E402
    GAP_FILL_BD_CONTACTS_PIPELINE_NAME,
    run_gap_fill_bd_contacts_background,
)

COOLDOWN_DAYS = 30
TRIGGER_SOURCE = "backfill_phones_script"
DEFAULT_LIMIT = 50
DEFAULT_REVEAL_CAP = 300
MAX_CONCURRENCY = int(os.environ.get("PHONE_GAP_FILL_CONCURRENCY", "3"))

# A contact row is a "phone gap" when the scalar phone is NULL and the phones
# JSONB is null/empty. Text-cast comparison avoids jsonb_array_length blowing up
# on a non-array value.
_PHONE_GAP = "c.phone IS NULL AND (c.phones IS NULL OR c.phones::text IN ('[]','null',''))"

# (kind, firm table, contact table, fk col, order col, pipeline_name, marker, runner)
_KINDS = {
    "bd": {
        "firm": "broker_dealers",
        "contact": "executive_contacts",
        "fk": "bd_id",
        "extra_where": "",
        "order": "f.lead_score DESC NULLS LAST, f.id ASC",
        "pipeline": GAP_FILL_BD_CONTACTS_PIPELINE_NAME,
        "marker": "bd_id",
        "runner": run_gap_fill_bd_contacts_background,
    },
    "ia": {
        "firm": "investment_advisors",
        "contact": "advisor_contacts",
        "fk": "advisor_id",
        "extra_where": "f.files_13f = TRUE AND",
        "order": "f.regulatory_aum DESC NULLS LAST, f.id ASC",
        "pipeline": GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME,
        "marker": "advisor_id",
        "runner": run_gap_fill_advisor_contacts_background,
    },
}


async def _recently_processed(db, pipeline_name: str, marker: str) -> set[int]:
    """Firm ids with a completed phone-gap-fill run inside the cooldown window.
    Parsed in Python (notes is JSON-as-text) to avoid a fragile ::jsonb cast over
    rows whose notes may not be valid JSON."""
    rows = (
        await db.execute(
            text(
                "SELECT notes FROM pipeline_runs "
                "WHERE pipeline_name = :p AND completed_at IS NOT NULL "
                "AND completed_at > now() - make_interval(days => :d)"
            ),
            {"p": pipeline_name, "d": COOLDOWN_DAYS},
        )
    ).all()
    seen: set[int] = set()
    for (notes,) in rows:
        if not notes:
            continue
        try:
            val = json.loads(notes).get(marker)
        except (ValueError, TypeError, AttributeError):
            continue
        if isinstance(val, int):
            seen.add(val)
    return seen


async def _candidates(db, cfg: dict) -> list[tuple[int, int]]:
    """Return (firm_id, phone_gap_contact_count) for firms with >=1 phone-gap
    contact, in priority order, excluding recently-processed firms."""
    sql = (
        f"SELECT f.id, "
        f"  (SELECT count(*) FROM {cfg['contact']} c "
        f"   WHERE c.{cfg['fk']} = f.id AND {_PHONE_GAP}) AS gaps "
        f"FROM {cfg['firm']} f "
        f"WHERE {cfg['extra_where']} EXISTS ("
        f"  SELECT 1 FROM {cfg['contact']} c "
        f"  WHERE c.{cfg['fk']} = f.id AND {_PHONE_GAP}) "
        f"ORDER BY {cfg['order']}"
    )
    rows = (await db.execute(text(sql))).all()
    recently = await _recently_processed(db, cfg["pipeline"], cfg["marker"])
    return [(fid, int(gaps)) for fid, gaps in rows if fid not in recently]


def _apply_caps(candidates: list[tuple[int, int]], limit: int, reveal_cap: int):
    """Greedily take firms in priority order until the firm limit OR the soft
    reveal cap (sum of phone-gap contacts ≈ max reveals) is reached."""
    chosen: list[int] = []
    est_reveals = 0
    for fid, gaps in candidates:
        if len(chosen) >= limit:
            break
        if chosen and est_reveals + gaps > reveal_cap:
            break
        chosen.append(fid)
        est_reveals += gaps
    return chosen, est_reveals


async def _run_one(cfg: dict, firm_id: int, sem: asyncio.Semaphore) -> bool:
    async with sem:
        async with SessionLocal() as db:
            run = PipelineRun(
                pipeline_name=cfg["pipeline"],
                trigger_source=TRIGGER_SOURCE,
                status="queued",
                total_items=1,
                notes=json.dumps({cfg["marker"]: firm_id, "stage": "queued"}),
                started_at=datetime.now(timezone.utc),
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
            run_id = run.id
        try:
            await cfg["runner"](run_id, firm_id, TRIGGER_SOURCE)
            return True
        except Exception as exc:  # noqa: BLE001 — log + continue the batch
            print(f"  {cfg['marker']} {firm_id}: EXCEPTION {type(exc).__name__}: {exc}")
            return False


async def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk phone gap-fill (Phase 5).")
    parser.add_argument("--apply", action="store_true", help="Actually run (else scan only).")
    parser.add_argument("--scan-only", action="store_true", help="Report candidate counts; no mutations.")
    parser.add_argument("--kind", choices=["bd", "ia", "both"], default="both")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max firms PER KIND (default 50).")
    parser.add_argument("--reveal-cap", type=int, default=DEFAULT_REVEAL_CAP, help="Soft cap on est. reveals per kind (default 300).")
    args = parser.parse_args()

    kinds = ["bd", "ia"] if args.kind == "both" else [args.kind]
    total_firms = 0
    for kind in kinds:
        cfg = _KINDS[kind]
        async with SessionLocal() as db:
            cands = await _candidates(db, cfg)
        chosen, est = _apply_caps(cands, args.limit, args.reveal_cap)
        pool_reveals = sum(g for _, g in cands)
        print(
            f"[{kind}] {len(cands)} firms need phones (~{pool_reveals} gap contacts); "
            f"this run: {len(chosen)} firms / ~{est} reveals "
            f"(caps: {args.limit} firms, {args.reveal_cap} reveals)."
        )
        if args.scan_only or not args.apply:
            continue
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        results = await asyncio.gather(*[_run_one(cfg, fid, sem) for fid in chosen])
        ok = sum(1 for r in results if r)
        print(f"[{kind}] done: {ok}/{len(chosen)} firms processed.")
        total_firms += len(chosen)

    if not args.apply and not args.scan_only:
        print("\nScan only (pass --apply to run). Phones are billed per reveal.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
