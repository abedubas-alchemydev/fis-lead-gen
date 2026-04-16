"""Check current database state for delivery readiness."""
from __future__ import annotations
import asyncio, selectors, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main() -> None:
    from app.db.session import SessionLocal
    from sqlalchemy import text

    async with SessionLocal() as db:
        queries = [
            ("Broker-dealers", "SELECT count(*) FROM broker_dealers"),
            ("  With CIK", "SELECT count(*) FROM broker_dealers WHERE cik IS NOT NULL"),
            ("  With CRD", "SELECT count(*) FROM broker_dealers WHERE crd_number IS NOT NULL"),
            ("  With financial data", "SELECT count(*) FROM broker_dealers WHERE latest_net_capital IS NOT NULL"),
            ("  With health status", "SELECT count(*) FROM broker_dealers WHERE health_status IS NOT NULL"),
            ("  With clearing partner", "SELECT count(*) FROM broker_dealers WHERE current_clearing_partner IS NOT NULL"),
            ("  With lead score", "SELECT count(*) FROM broker_dealers WHERE lead_score IS NOT NULL"),
            ("  Hot leads", "SELECT count(*) FROM broker_dealers WHERE lead_priority = 'hot'"),
            ("  Warm leads", "SELECT count(*) FROM broker_dealers WHERE lead_priority = 'warm'"),
            ("  Cold leads", "SELECT count(*) FROM broker_dealers WHERE lead_priority = 'cold'"),
            ("  Deficient (Alt List)", "SELECT count(*) FROM broker_dealers WHERE is_deficient = true"),
            ("Financial metrics", "SELECT count(*) FROM financial_metrics"),
            ("  BDs with 2+ years", "SELECT count(*) FROM (SELECT bd_id FROM financial_metrics GROUP BY bd_id HAVING count(*) >= 2) sub"),
            ("Clearing arrangements", "SELECT count(*) FROM clearing_arrangements"),
            ("  Parsed OK", "SELECT count(*) FROM clearing_arrangements WHERE extraction_status = 'parsed'"),
            ("  Needs review", "SELECT count(*) FROM clearing_arrangements WHERE extraction_status = 'needs_review'"),
            ("Filing alerts", "SELECT count(*) FROM filing_alerts"),
            ("  Form BD", "SELECT count(*) FROM filing_alerts WHERE form_type = 'Form BD'"),
            ("  Form 17a-11", "SELECT count(*) FROM filing_alerts WHERE form_type = 'Form 17a-11'"),
            ("Executive contacts", "SELECT count(*) FROM executive_contacts"),
            ("  From Apollo", "SELECT count(*) FROM executive_contacts WHERE source = 'apollo'"),
            ("Pipeline runs", "SELECT count(*) FROM pipeline_runs"),
            ("Competitor providers", "SELECT count(*) FROM competitor_providers"),
            ("Scoring settings", "SELECT count(*) FROM scoring_settings"),
            ("Users", "SELECT count(*) FROM \"user\""),
            ("Audit log entries", "SELECT count(*) FROM audit_log"),
        ]

        print("=" * 55)
        print("  DATABASE STATE REPORT")
        print("=" * 55)
        for label, sql in queries:
            try:
                result = (await db.execute(text(sql))).scalar_one()
                status = "OK" if int(result) > 0 else "EMPTY"
                print(f"  {label:<35} {int(result):>6,}  [{status}]")
            except Exception as e:
                print(f"  {label:<35} ERROR: {e}")
        print("=" * 55)

if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
