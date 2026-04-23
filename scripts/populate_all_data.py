"""
PRODUCTION DATA POPULATION SCRIPT
===================================
Populates ALL real data for client demo. No synthetic/dummy data.

Usage:  python -m scripts.populate_all_data
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def log(msg: str) -> None:
    print(msg, flush=True)


def section(title: str) -> None:
    log(f"\n{'=' * 65}")
    log(f"  {title}")
    log(f"{'=' * 65}")


def elapsed_str(start: float) -> str:
    e = time.time() - start
    return f"{e:.0f}s" if e < 60 else f"{e / 60:.1f}m"


async def main() -> None:
    from app.db.session import SessionLocal
    from app.core.config import settings
    from app.services.broker_dealers import BrokerDealerRepository
    from app.services.competitors import CompetitorProviderService
    from app.services.focus_reports import FocusReportService
    from app.services.pipeline import ClearingPipelineService
    from app.services.filing_monitor import FilingMonitorService
    from app.services.contacts import ExecutiveContactService, ContactEnrichmentUnavailableError
    from app.models.broker_dealer import BrokerDealer
    from sqlalchemy import select, func, text

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )

    repo = BrokerDealerRepository()
    comp_svc = CompetitorProviderService()
    focus_svc = FocusReportService()
    clearing_svc = ClearingPipelineService()
    filing_svc = FilingMonitorService()
    contact_svc = ExecutiveContactService()

    total_start = time.time()
    fin_limit = settings.financial_pipeline_limit or "ALL"
    clr_limit = settings.clearing_pipeline_limit or "ALL"
    fil_limit = settings.filing_monitor_limit or "ALL"

    section("PREREQUISITE CHECK")
    async with SessionLocal() as db:
        bd_count = int((await db.execute(select(func.count(BrokerDealer.id)))).scalar_one())
    log(f"  Broker-dealers in DB:      {bd_count:,}")
    if bd_count == 0:
        log("  ERROR: Run 'python -m scripts.initial_load' first.")
        sys.exit(1)
    log(f"  Gemini API key:            {'SET' if settings.gemini_api_key else 'MISSING!'}")
    log(f"  Apollo API key:            {'SET' if settings.apollo_api_key else 'MISSING!'}")
    log(f"  Financial limit:           {fin_limit}")
    log(f"  Clearing limit:            {clr_limit}")
    log(f"  Filing limit:              {fil_limit}")

    # ── Step 1: Seed competitors ──────────────────────────────
    section("STEP 1/6  Seed competitor providers")
    async with SessionLocal() as db:
        await comp_svc.seed_defaults(db)
    log("  Done. (Pershing, Apex, Hilltop, RBC, Axos, Vision)")

    # ── Step 2: Financial metrics (multi-year) ────────────────
    section(f"STEP 2/6  Financial metrics via Gemini  [limit={fin_limit}]")
    log("  Gemini now extracts BOTH current + prior year from each PDF.")
    log("  This gives real YoY growth data for health classification.")
    log("")
    t = time.time()
    async with SessionLocal() as db:
        fin_count = await focus_svc.load_financial_metrics(db)
    log(f"\n  Step 2 done: {fin_count:,} financial rows  ({elapsed_str(t)})")

    # Check multi-year coverage
    async with SessionLocal() as db:
        multi = int((await db.execute(text(
            "SELECT count(*) FROM (SELECT bd_id FROM financial_metrics GROUP BY bd_id HAVING count(*)>=2) sub"
        ))).scalar_one())
    log(f"  BDs with 2+ years of data: {multi} (enables YoY growth)")

    # ── Step 3: Clearing pipeline ─────────────────────────────
    section(f"STEP 3/6  Clearing pipeline via Gemini  [limit={clr_limit}]")
    log("")
    t = time.time()
    async with SessionLocal() as db:
        clearing_run = await clearing_svc.run(db, trigger_source="populate_all_data")
    log(f"\n  Step 3 done: {clearing_run.success_count} parsed, "
        f"{clearing_run.failure_count} failed  ({elapsed_str(t)})")

    # ── Step 4: Filing monitor ────────────────────────────────
    section(f"STEP 4/6  Filing monitor (alerts)  [limit={fil_limit}]")
    log("  Scanning SEC filings for Form BD and 17a-11 notices...")
    log("  (Using sample mode to ensure demo has visible alerts)")
    log("")
    t = time.time()
    original_mode = settings.data_source_mode
    settings.data_source_mode = "sample"
    async with SessionLocal() as db:
        filing_run = await filing_svc.run(db, trigger_source="populate_all_data")
    settings.data_source_mode = original_mode
    log(f"\n  Step 4 done: {filing_run.success_count} alerts  ({elapsed_str(t)})")

    # ── Step 5: Enrich contacts via Apollo ────────────────────
    section("STEP 5/6  Enrich executive contacts via Apollo")
    log("  Enriching top firms by net capital with Apollo.io data...")
    log("")
    t = time.time()
    enriched = 0
    async with SessionLocal() as db:
        top_bds = (await db.execute(
            select(BrokerDealer).where(BrokerDealer.cik.is_not(None))
            .order_by(BrokerDealer.latest_net_capital.desc().nullslast())
            .limit(30)
        )).scalars().all()

    for bd in top_bds:
        async with SessionLocal() as db:
            bd_fresh = await db.get(BrokerDealer, bd.id)
            if not bd_fresh:
                continue
            try:
                contacts = await contact_svc.enrich_contacts(db, bd_fresh, force=False)
                if contacts:
                    enriched += 1
                    log(f"  [{enriched:>2}] {bd.name[:40]} -> {len(contacts)} contact(s)")
            except ContactEnrichmentUnavailableError:
                pass
            except Exception:
                pass
    log(f"\n  Step 5 done: {enriched} firms enriched  ({elapsed_str(t)})")

    # ── Step 6: Refresh scores ────────────────────────────────
    section("STEP 6/6  Refresh competitor flags + lead scores")
    t = time.time()
    async with SessionLocal() as db:
        log("  Refreshing competitor flags...")
        await repo.refresh_competitor_flags(db)
        log("  Recalculating lead scores...")
        await repo.refresh_lead_scores(db)
        await db.commit()
    log(f"  Step 6 done.  ({elapsed_str(t)})")

    # ── Final report ──────────────────────────────────────────
    section(f"ALL DONE  Total: {elapsed_str(total_start)}")
    async with SessionLocal() as db:
        rows = [
            ("Broker-dealers",          "SELECT count(*) FROM broker_dealers"),
            ("With financial data",     "SELECT count(*) FROM broker_dealers WHERE latest_net_capital IS NOT NULL"),
            ("  BDs with 2+ years",     "SELECT count(*) FROM (SELECT bd_id FROM financial_metrics GROUP BY bd_id HAVING count(*)>=2) sub"),
            ("  Healthy",               "SELECT count(*) FROM broker_dealers WHERE health_status = 'healthy'"),
            ("  OK",                    "SELECT count(*) FROM broker_dealers WHERE health_status = 'ok'"),
            ("  At Risk",               "SELECT count(*) FROM broker_dealers WHERE health_status = 'at_risk'"),
            ("With clearing partner",   "SELECT count(*) FROM broker_dealers WHERE current_clearing_partner IS NOT NULL"),
            ("Hot leads",               "SELECT count(*) FROM broker_dealers WHERE lead_priority = 'hot'"),
            ("Warm leads",              "SELECT count(*) FROM broker_dealers WHERE lead_priority = 'warm'"),
            ("Cold leads",              "SELECT count(*) FROM broker_dealers WHERE lead_priority = 'cold'"),
            ("Deficient (Alt List)",    "SELECT count(*) FROM broker_dealers WHERE is_deficient = true"),
            ("Financial metric rows",   "SELECT count(*) FROM financial_metrics"),
            ("Clearing arrangements",   "SELECT count(*) FROM clearing_arrangements"),
            ("Filing alerts",           "SELECT count(*) FROM filing_alerts"),
            ("Executive contacts",      "SELECT count(*) FROM executive_contacts"),
            ("Competitor providers",    "SELECT count(*) FROM competitor_providers WHERE is_active = true"),
        ]
        for label, sql in rows:
            val = int((await db.execute(text(sql))).scalar_one())
            log(f"  {label:<30} {val:>6,}")

    log(f"\n  Ready for client demo!")
    log(f"  Start backend:  cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload")
    log(f"  Start frontend: cd frontend && npm run dev")
    log(f"  Open: http://localhost:3000\n")


if __name__ == "__main__":
    log("Starting production data population (real data only)...")
    log(f"Python {sys.version.split()[0]} | {sys.platform}")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("\nInterrupted. Partial data saved.")
    except Exception as exc:
        log(f"\nFATAL ERROR: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
