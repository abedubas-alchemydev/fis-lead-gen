"""Seed five hand-picked Investment Advisor fixtures for staging visibility.

Idempotent — safe to re-run. Inserts new rows or updates existing ones
matched by ``crd_number``. Used by PR 1 of the Investment Advisor master
list rollout so the staging deploy renders something other than an empty
list while the PR 2 IAPD bulk-ingest pipeline is still in flight.

Usage:
    python -m scripts.seed_advisor_fixtures

Reminder (per project memory): staging shares one Neon DB with prod, so
"run on staging" is effectively a prod write. The five firms below are
public-domain; their CRDs and AUMs are taken from publicly-available
Form ADV filings as of 2025 and are intentionally rounded approximations
— the PR 3 Form ADV extractor will overwrite these values with parsed
fields once it lands.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal  # noqa: E402
from app.services.investment_advisors import InvestmentAdvisorRepository  # noqa: E402


SEED_FIXTURES: list[dict[str, object]] = [
    {
        "cik": "0001364742",
        "crd_number": "107218",
        "sec_file_number": "801-56972",
        "name": "BlackRock Fund Advisors",
        "legal_name": "BlackRock Fund Advisors",
        "city": "San Francisco",
        "state": "CA",
        "registration_date": date(1984, 6, 18),
        "matched_source": "iapd",
        "status": "active",
        "last_filing_date": date(2025, 3, 31),
        "website": "https://www.blackrock.com",
        "website_source": "iapd",
        "regulatory_aum": 4_500_000_000_000.0,
        "files_13f": True,
        "latest_13f_filing_date": date(2025, 2, 14),
    },
    {
        "cik": "0001350694",
        "crd_number": "109037",
        "sec_file_number": "801-58050",
        "name": "Bridgewater Associates, LP",
        "legal_name": "Bridgewater Associates, LP",
        "city": "Westport",
        "state": "CT",
        "registration_date": date(1990, 12, 18),
        "matched_source": "iapd",
        "status": "active",
        "last_filing_date": date(2025, 3, 28),
        "website": "https://www.bridgewater.com",
        "website_source": "iapd",
        "regulatory_aum": 112_500_000_000.0,
        "files_13f": True,
        "latest_13f_filing_date": date(2025, 2, 14),
    },
    {
        "cik": "0001056903",
        "crd_number": "104877",
        "sec_file_number": "801-37591",
        "name": "Goldman Sachs Asset Management, L.P.",
        "legal_name": "Goldman Sachs Asset Management, L.P.",
        "city": "New York",
        "state": "NY",
        "registration_date": date(1990, 4, 6),
        "matched_source": "iapd",
        "status": "active",
        "last_filing_date": date(2025, 3, 27),
        "website": "https://www.gsam.com",
        "website_source": "iapd",
        "regulatory_aum": 2_700_000_000_000.0,
        "files_13f": True,
        "latest_13f_filing_date": date(2025, 2, 14),
    },
    {
        "cik": "0001037389",
        "crd_number": "105741",
        "sec_file_number": "801-39054",
        "name": "Renaissance Technologies LLC",
        "legal_name": "Renaissance Technologies LLC",
        "city": "East Setauket",
        "state": "NY",
        "registration_date": date(1990, 12, 4),
        "matched_source": "iapd",
        "status": "active",
        "last_filing_date": date(2025, 3, 31),
        "website": "https://www.rentec.com",
        "website_source": "iapd",
        "regulatory_aum": 89_000_000_000.0,
        "files_13f": True,
        "latest_13f_filing_date": date(2025, 2, 14),
    },
    {
        "cik": "0001179392",
        "crd_number": "143301",
        "sec_file_number": "801-65933",
        "name": "Two Sigma Investments, LP",
        "legal_name": "Two Sigma Investments, LP",
        "city": "New York",
        "state": "NY",
        "registration_date": date(2002, 11, 6),
        "matched_source": "iapd",
        "status": "active",
        "last_filing_date": date(2025, 3, 30),
        "website": "https://www.twosigma.com",
        "website_source": "iapd",
        "regulatory_aum": 65_000_000_000.0,
        "files_13f": True,
        "latest_13f_filing_date": date(2025, 2, 14),
    },
]


async def main() -> None:
    repository = InvestmentAdvisorRepository()
    print(f"Seeding {len(SEED_FIXTURES)} Investment Advisor fixtures…")
    async with SessionLocal() as db:
        count = await repository.upsert_by_crd(db, SEED_FIXTURES)
        await db.commit()
    print(f"Done — upserted {count} rows.")


if __name__ == "__main__":
    asyncio.run(main())
