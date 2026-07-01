"""Discover and ingest NEW broker-dealers that FINRA has registered since our
last full load, using an **enumerate-and-diff** strategy.

Why this exists. The ``broker_dealers`` table is populated in bulk by
``scripts/initial_load.py``. Between initial_load runs, FINRA registers fresh
broker-dealers but our DB doesn't pick them up. This script catches up that gap
without re-running the full initial_load.

Why enumerate-and-diff (and not a CRD probe). FINRA assigns CRDs to *every*
registrant — broker-dealers, investment advisers, and individuals — from one
sequential pool, and there is **no FINRA "recently-registered" endpoint** to
date-query. The old version of this script probed CRDs sequentially upward from
``MAX(crd_number)`` and stopped after N consecutive "misses". In production it
found nothing: the CRDs just above our watermark are almost always IA-only
firms or individuals (which count as misses), so the probe quit ~50 CRDs up and
never reached a genuinely new broker-dealer whose CRD sits hundreds or
thousands higher. Enumerate-and-diff sidesteps the watermark entirely: it lists
*all* active broker-dealers and diffs against the DB, so a net-new BD is found
no matter where its CRD lands.

Mechanism:
  1. Enumerate every active broker-dealer from FINRA BrokerCheck via
     ``FinraService().fetch_broker_dealers()`` — the same keyword + A-Z/0-9 Solr
     enumeration (paginated, deduped by CRD, ``active=true``, with 429 /
     Retry-After backoff) that ``scripts/initial_load.py`` uses. Cheap: one
     search payload per page, no per-firm detail.
  2. Load existing CRDs: ``SELECT crd_number FROM broker_dealers`` into a set.
  3. Diff (pure, unit-tested :func:`select_new_bds`): keep enumerated records
     whose ``crd_number`` is truthy and not already in the DB, deduped by CRD.
  4. For the net-new set ONLY (so the expensive per-firm work stays bounded):
       a. ``FinraService().enrich_with_detail(new)`` — Form BD PDF detail
          (types_of_business, officers, registration/formation dates, ...).
       b. ``EdgarService().fetch_records_for_sec_numbers(...)`` for their SEC
          file numbers.
       c. ``BrokerDealerMergeService().merge(edgar_records, new)`` — EDGAR
          first, unpack ``(merged, report)``; the same arg-order contract the
          initial-load pipeline uses.
       d. ``BrokerDealerRepository().upsert_many(db, merged)`` — idempotent
          upsert (re-running is safe; existing rows update in place).

Dry-run is the default and is cheap: it enumerates, diffs, and logs exactly
which firms WOULD be ingested (no Form BD PDF fetches, no EDGAR, no writes).
``--apply`` runs the full enrich → EDGAR → merge → upsert path.

Post-apply Doxie hook. After new BDs are committed, ``--apply`` chains
``_embed_backfill_after_apply``: it lazily imports ``app.services`` to embed the
new firms into the ``chatbot_firm_embedding`` semantic-search index. That hook
is best-effort and failure-isolated — when ``GEMINI_API_KEY`` is unavailable it
logs and moves on, never touching the extractor's exit code or its committed
rows.

Usage::

    # dry-run: enumerate + diff + report what WOULD be ingested (no writes)
    python scripts/standalone_extract_new_bds.py

    # actually enrich + merge + upsert the net-new firms
    python scripts/standalone_extract_new_bds.py --apply

    # override DB URL (defaults to the DATABASE_URL env var)
    python scripts/standalone_extract_new_bds.py --db-url <URL>

Runs as a Cloud Run Job (backend image) via
``--args=scripts/standalone_extract_new_bds.py,--apply``; see
``docs/runbooks/extract-new-bds.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:  # import only for type hints — keeps the module import cheap
    from app.services.service_models import FinraBrokerDealerRecord


# Local checkouts run this script from the repo root without the backend
# package on the path; the backend lives at <repo>/backend (same bootstrap as
# scripts/initial_load.py and the embed hook below). In the backend image the
# dir doesn't exist (backend/ is copied to /app) and PYTHONPATH=/app already
# makes ``app.*`` importable, so this is a no-op there.
_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if _BACKEND_ROOT.is_dir() and str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Line-buffer stdout/stderr so Cloud Run streams logs promptly. Guarded so
# importing this module under pytest (which swaps in capture streams that may
# lack ``reconfigure``) never blows up.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

logger = logging.getLogger("standalone_extract_new_bds")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def select_new_bds(
    enumerated: list["FinraBrokerDealerRecord"],
    existing_crds: set[str],
) -> list["FinraBrokerDealerRecord"]:
    """Pure diff: return the enumerated FINRA records that are net-new to us.

    A record is net-new when its ``crd_number`` is truthy (non-empty after
    stripping) AND not already present in ``existing_crds``. The result is
    deduped by CRD (first occurrence wins) so a firm surfaced under more than
    one enumeration query is ingested once.

    No I/O; the only mutation is stripping each selected record's
    ``crd_number`` in place (so a padded value from the enumeration isn't
    persisted padded by the downstream enrich → merge → upsert path — the live
    enumeration already strips, this is belt-and-suspenders). ``existing_crds``
    is expected to already be a set of stripped CRD strings (see ``main``);
    CRDs are compared as strings because ``broker_dealers.crd_number`` is
    ``String(32)``.
    """
    new: list["FinraBrokerDealerRecord"] = []
    seen: set[str] = set()
    for record in enumerated:
        crd = (record.crd_number or "").strip()
        if not crd:
            continue
        if crd in existing_crds:
            continue
        if crd in seen:
            continue
        seen.add(crd)
        if record.crd_number != crd:
            record.crd_number = crd  # normalize on store
        new.append(record)
    return new


# ---------------------------------------------------------------------------
# Doxie semantic-index freshness hook (best-effort, post-apply only)
# ---------------------------------------------------------------------------

async def _embed_backfill_after_apply() -> None:
    """Embed newly-committed BDs into Doxie's semantic-search index.

    The nightly Cloud Run Job is this script's main caller, and the BDs
    it inserts would otherwise sit unembedded until someone runs the
    populate-all pipeline (which almost never happens on staging). The
    backfill is incremental -- content-hash skip means already-embedded
    firms cost a hash lookup, not a Gemini call -- so chaining it here
    keeps ``chatbot_firm_embedding`` fresh for cheap.

    Failure-isolated by contract: lazy ``app.*`` imports, and every
    exception is caught and logged. This function must never change the
    extractor's exit code; the upserts it runs after are already committed,
    so there is nothing to roll back. In the backend image PYTHONPATH=/app
    makes ``app.*`` importable and the app reads the same DATABASE_URL env
    var this script defaults to; GEMINI_API_KEY missing downgrades to a
    logged skip.
    """
    try:
        from app.core.config import settings
        from app.db.session import SessionLocal
        from app.services.chatbot_semantic import ChatbotSemanticService
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "doxie embed backfill after extract skipped: app modules "
            "unavailable (%s)", exc,
        )
        return
    try:
        if not settings.gemini_api_key:
            logger.info(
                "doxie embed backfill after extract skipped: GEMINI_API_KEY "
                "not configured"
            )
            return
        service = ChatbotSemanticService()
        async with SessionLocal() as db:
            bd = await service.backfill_broker_dealers(db)
            ia = await service.backfill_investment_advisors(db)
        logger.info(
            "doxie embed backfill after extract: bd_embedded=%d bd_skipped=%d "
            "bd_failed=%d ia_embedded=%d ia_skipped=%d ia_failed=%d",
            bd.embedded, bd.skipped, bd.failed,
            ia.embedded, ia.skipped, ia.failed,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "doxie embed backfill after extract failed; extractor result "
            "unaffected"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Enumerate-and-diff extractor for net-new broker-dealers: lists all "
            "active FINRA BDs and ingests the ones not already in the DB."
        ),
    )
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually enrich + merge + upsert rows. Without this, the script runs dry.",
    )
    args = parser.parse_args(argv)

    if not args.db_url:
        logger.error("no DATABASE_URL env var and no --db-url; aborting")
        return 2

    # Imported lazily (after the sys.path bootstrap) so the module stays cheap
    # to import for the pure-function unit tests and so a missing app dependency
    # surfaces only when the script actually runs.
    from app.services.broker_dealers import BrokerDealerRepository
    from app.services.data_merge import BrokerDealerMergeService
    from app.services.edgar import EdgarService
    from app.services.finra import FinraService

    db_url = _normalize_db_url(args.db_url)
    engine = create_async_engine(db_url, pool_pre_ping=True)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    finra = FinraService()

    try:
        # ── Step 1: Enumerate all active broker-dealers (cheap; no detail) ──
        logger.info("enumerating active broker-dealers from FINRA BrokerCheck...")
        enumerated = await finra.fetch_broker_dealers()
        logger.info("enumerated %d active broker-dealer(s) from FINRA", len(enumerated))

        # ── Step 2: Load existing CRDs from the DB ──
        async with session_maker() as db:
            rows = (await db.execute(text("SELECT crd_number FROM broker_dealers"))).all()
        existing_crds = {
            str(row[0]).strip()
            for row in rows
            if row[0] is not None and str(row[0]).strip()
        }
        logger.info("loaded %d existing CRD(s) from broker_dealers", len(existing_crds))

        # ── Step 3: Diff (pure) ──
        new = select_new_bds(enumerated, existing_crds)
        logger.info(
            "diff: enumerated=%d existing=%d net_new=%d",
            len(enumerated), len(existing_crds), len(new),
        )

        if not new:
            logger.info("no net-new broker-dealers; nothing to do")
            return 0

        # ── Dry-run: report exactly what WOULD be ingested, then stop ──
        if not args.apply:
            logger.info(
                "dry-run: would ingest %d net-new broker-dealer(s) "
                "(re-run with --apply to enrich + merge + write):",
                len(new),
            )
            for rec in new:
                logger.info(
                    "  crd=%s sec=%s name=%r status=%s city=%r state=%r",
                    rec.crd_number,
                    rec.sec_file_number or "-",
                    rec.name,
                    rec.registration_status,
                    rec.address_city,
                    rec.address_state,
                )
            return 0

        # ── Step 4a: Enrich the net-new set with FINRA Form BD detail ──
        logger.info("enriching %d net-new firm(s) with FINRA Form BD detail...", len(new))
        await finra.enrich_with_detail(new)

        # ── Step 4b: Resolve SEC/EDGAR records for the net-new SEC numbers ──
        sec_numbers = [r.sec_file_number for r in new if r.sec_file_number]
        logger.info("resolving %d SEC file number(s) against EDGAR...", len(sec_numbers))
        edgar_records = await EdgarService().fetch_records_for_sec_numbers(sec_numbers)
        logger.info("EDGAR resolved %d record(s)", len(edgar_records))

        # ── Step 4c: Merge (EDGAR first — same contract as the pipeline) ──
        merged, report = BrokerDealerMergeService().merge(edgar_records, new)
        for line in report.summary_lines():
            logger.info("%s", line)

        if not merged:
            logger.info("merge produced 0 rows after QA filters; nothing to write")
            return 0

        # ── Step 4d: Idempotent upsert ──
        async with session_maker() as db:
            written = await BrokerDealerRepository().upsert_many(db, merged)
        logger.info(
            "summary: enumerated=%d existing=%d net_new=%d inserted=%d",
            len(enumerated), len(existing_crds), len(new), written,
        )

        # New BDs are committed (upsert_many commits) -- chain the Doxie embed
        # pass so they become semantically searchable tonight, not whenever
        # populate-all next runs. Best-effort: the hook swallows its own errors
        # and never affects this script's exit code.
        await _embed_backfill_after_apply()
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
