"""One-shot audit of broker_dealers.types_of_business JSONB shape.

Counts how many rows hold each JSONB shape so we know how many records
were affected by the /broker-dealers/types-of-business 500. Reads
DATABASE_URL from the environment (or backend/.env) and runs read-only
queries — safe to run against prod.

Usage:

    # from repo root, with backend/.env populated
    python scripts/ops/audit-types-of-business-shape.py

    # or with an explicit URL
    DATABASE_URL=postgresql+asyncpg://... python scripts/ops/audit-types-of-business-shape.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / "backend" / ".env")
except ImportError:
    pass

from sqlalchemy import create_engine, text  # noqa: E402


SHAPE_QUERY = text(
    """
    SELECT
        CASE
            WHEN types_of_business IS NULL THEN 'sql_null'
            ELSE jsonb_typeof(types_of_business)
        END AS shape,
        COUNT(*) AS n
    FROM broker_dealers
    GROUP BY 1
    ORDER BY n DESC
    """
)

SAMPLE_BAD_ROWS = text(
    """
    SELECT id, cik, name, jsonb_typeof(types_of_business) AS shape,
           types_of_business::text AS value
    FROM broker_dealers
    WHERE types_of_business IS NOT NULL
      AND jsonb_typeof(types_of_business) <> 'array'
    ORDER BY id
    LIMIT 20
    """
)


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set; aborting.", file=sys.stderr)
        return 2
    # Force sync driver — async drivers (asyncpg, psycopg async) hit
    # ProactorEventLoop incompatibilities on Windows for one-shot scripts.
    url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            print("== broker_dealers.types_of_business shape distribution ==")
            for row in conn.execute(SHAPE_QUERY).all():
                print(f"  {row.shape:<10} {row.n}")

            print()
            print("== sample non-array rows (up to 20) ==")
            bad = conn.execute(SAMPLE_BAD_ROWS).all()
            if not bad:
                print("  (none — all non-NULL rows are JSONB arrays)")
            else:
                for row in bad:
                    truncated = (row.value or "")[:80]
                    print(f"  id={row.id} cik={row.cik} shape={row.shape} value={truncated}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
