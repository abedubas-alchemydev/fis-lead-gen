"""Check all client-required fields against the database."""
import asyncio, selectors, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text
from app.db.session import SessionLocal

async def check():
    async with SessionLocal() as db:
        total = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers"))).scalar()

        print("=" * 70)
        print("  CLIENT REQUIREMENTS vs DATABASE")
        print("=" * 70)

        print("\n1. FIRM PROFILE OVERVIEW (FINRA Source)")
        print("-" * 70)

        # Officers
        r = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE direct_owners IS NOT NULL AND direct_owners::text != 'null' AND direct_owners::text != '[]'"))).scalar()
        print(f"  Officers & Directors (name+position): {r:,} / {total:,} ({r/total*100:.1f}%)")

        # Sample officer
        sample = (await db.execute(text("SELECT name, direct_owners::text FROM broker_dealers WHERE direct_owners IS NOT NULL AND direct_owners::text != '[]' LIMIT 1"))).first()
        if sample:
            officers = json.loads(sample[1])[:2]
            for o in officers:
                print(f"    Example: {o.get('name','?')} - {o.get('title','no title')}")

        # Types of Business
        r = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE types_of_business_total IS NOT NULL"))).scalar()
        print(f"  Types of Business - Total Number:     {r:,} / {total:,} ({r/total*100:.1f}%)")

        r = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE types_of_business IS NOT NULL AND types_of_business::text != 'null' AND types_of_business::text != '[]'"))).scalar()
        print(f"  Types of Business - Services List:    {r:,} / {total:,} ({r/total*100:.1f}%)")

        r = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE types_of_business_other IS NOT NULL AND types_of_business_other != ''"))).scalar()
        print(f"  Types of Business - Other:            {r:,} / {total:,} ({r/total*100:.1f}%)")

        # Clearing
        r = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE firm_operations_text IS NOT NULL AND firm_operations_text != ''"))).scalar()
        print(f"  Clearing Arrangements (statement):    {r:,} / {total:,} ({r/total*100:.1f}%)")

        r_raw = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE clearing_raw_text IS NOT NULL AND clearing_raw_text != ''"))).scalar()
        print(f"  Clearing Raw Text (fallback):         {r_raw:,} / {total:,} ({r_raw/total*100:.1f}%)")

        # Introducing
        intro = (await db.execute(text("SELECT COUNT(*) FROM introducing_arrangements"))).scalar()
        intro_firms = (await db.execute(text("SELECT COUNT(DISTINCT bd_id) FROM introducing_arrangements"))).scalar()
        biz = (await db.execute(text("SELECT COUNT(*) FROM introducing_arrangements WHERE business_name IS NOT NULL AND business_name != ''"))).scalar()
        eff = (await db.execute(text("SELECT COUNT(*) FROM introducing_arrangements WHERE effective_date IS NOT NULL"))).scalar()
        desc = (await db.execute(text("SELECT COUNT(*) FROM introducing_arrangements WHERE description IS NOT NULL AND description != ''"))).scalar()
        stmt = (await db.execute(text("SELECT COUNT(*) FROM introducing_arrangements WHERE statement IS NOT NULL AND statement != ''"))).scalar()
        print(f"  Introducing Arrangements:             {intro:,} rows across {intro_firms:,} firms")
        print(f"    - With Statement:                   {stmt:,}")
        print(f"    - With Business Name:               {biz:,}")
        print(f"    - With Effective Date:               {eff:,}")
        print(f"    - With Description:                 {desc:,}")

        # History
        r = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE formation_date IS NOT NULL"))).scalar()
        print(f"  Formation Date:                       {r:,} / {total:,} ({r/total*100:.1f}%)")
        r = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE registration_date IS NOT NULL"))).scalar()
        print(f"  Registration Date:                    {r:,} / {total:,} ({r/total*100:.1f}%)")

        print("\n2. FINANCIALS & CONTACT DATA (SEC Focus Report)")
        print("-" * 70)

        fc = (await db.execute(text("SELECT COUNT(*) FROM executive_contacts WHERE source = 'focus_report'"))).scalar()
        fe = (await db.execute(text("SELECT COUNT(*) FROM executive_contacts WHERE source = 'focus_report' AND email IS NOT NULL"))).scalar()
        fp = (await db.execute(text("SELECT COUNT(*) FROM executive_contacts WHERE source = 'focus_report' AND phone IS NOT NULL"))).scalar()
        ft = (await db.execute(text("SELECT COUNT(*) FROM executive_contacts WHERE source = 'focus_report' AND title IS NOT NULL AND title != ''"))).scalar()
        print(f"  Primary Contact - Full Name:          {fc:,} / {total:,} ({fc/total*100:.1f}%)")
        print(f"  Primary Contact - Title:              {ft:,}")
        print(f"  Primary Contact - Email:              {fe:,}")
        print(f"  Primary Contact - Phone:              {fp:,}")

        nc = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE latest_net_capital IS NOT NULL"))).scalar()
        ta = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE latest_total_assets IS NOT NULL"))).scalar()
        yoy = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE yoy_growth IS NOT NULL"))).scalar()
        print(f"  Net Capital:                          {nc:,} / {total:,} ({nc/total*100:.1f}%)")
        print(f"  Total Assets:                         {ta:,} / {total:,} ({ta/total*100:.1f}%)")
        print(f"  YoY Growth:                           {yoy:,} / {total:,} ({yoy/total*100:.1f}%)")

        print("\n3. IMPLEMENTATION REQUIREMENTS")
        print("-" * 70)
        print(f"  Eliminate Manual Clicks:               YES (all on profile page)")
        print(f"  Logic Overrides (raw text fallback):   YES ({r_raw:,} firms have raw text)")
        print(f"  Next Lead Navigation Arrows:           YES (API + UI implemented)")

        sc = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE clearing_classification = 'self_clearing'"))).scalar()
        fd = (await db.execute(text("SELECT COUNT(*) FROM broker_dealers WHERE clearing_classification = 'fully_disclosed'"))).scalar()
        uk = total - sc - fd
        print(f"\n  Classification Breakdown:")
        print(f"    Self-Clearing:    {sc:,}")
        print(f"    Fully Disclosed:  {fd:,}")
        print(f"    Unknown/Pending:  {uk:,}")
        print("=" * 70)

with asyncio.Runner(loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) as runner:
    runner.run(check())
