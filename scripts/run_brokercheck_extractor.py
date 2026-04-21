"""Bridge script: Run the brokercheck_extractor pipeline and save results to the web app DB.

Reads CRD numbers from the web app's broker_dealers table, runs the FINRA + SEC
extraction pipeline, and maps the results back into the web app's database schema.

Usage:
    python -m scripts.run_brokercheck_extractor                # all firms
    python -m scripts.run_brokercheck_extractor --limit 10     # first 10 only
    python -m scripts.run_brokercheck_extractor --offset 500   # resume from #500
    python -m scripts.run_brokercheck_extractor --crd 12345    # single firm by CRD
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import selectors
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
EXTRACTOR_ROOT = ROOT / "brokercheck_extractor"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("bridge")

# ── Web app imports ──
from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.models.executive_contact import ExecutiveContact  # noqa: E402
from app.models.industry_arrangement import IndustryArrangement  # noqa: E402
from app.models.introducing_arrangement import IntroducingArrangement  # noqa: E402
from app.services.scoring import classify_health_status  # noqa: E402

# ── Extractor imports (FINRA only) ──
from brokercheck_extractor.acquisition.finra_client import FinraClient  # noqa: E402
from brokercheck_extractor.parsers.finra_parser import parse_finra_pdf  # noqa: E402
from brokercheck_extractor.derivation.clearing_classifier import apply_classification  # noqa: E402
from brokercheck_extractor.schema.models import FirmProfile  # noqa: E402

from sqlalchemy import select, delete, text  # noqa: E402


# ──────────────────────────────────────────────────────────────
# Mappers: extractor models → web app DB
# ──────────────────────────────────────────────────────────────

def _map_officers(profile: FirmProfile) -> list[dict]:
    """Map extractor Officer models to JSONB for broker_dealers.direct_owners."""
    if not profile.officers:
        return []
    result = []
    for officer in profile.officers:
        entry: dict = {"name": officer.name}
        if officer.position:
            entry["title"] = officer.position
        if officer.ownership_code:
            entry["ownership_pct"] = officer.ownership_code
        if officer.position_start:
            entry["position_start"] = officer.position_start
        result.append(entry)
    return result


def _map_types_of_business(profile: FirmProfile) -> tuple[list[str] | None, int | None, str | None]:
    """Returns (services_list, total, other_text)."""
    if not profile.types_of_business:
        return None, None, None
    tob = profile.types_of_business
    return tob.services or None, tob.total, tob.other


def _to_decimal(val) -> Decimal | None:
    """Safely convert to Decimal."""
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except Exception:
        return None


async def _save_firm_to_webapp(
    crd_number: str,
    bd_id: int,
    profile: FirmProfile | None,
    focus_current: FocusReport | None,
    focus_prior: FocusReport | None,
) -> dict:
    """Map extractor output to web app DB and save. Uses fresh session (Neon-safe)."""
    changes = []

    async with SessionLocal() as db:
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return {"error": f"BD {bd_id} not found"}

        # ── FINRA data ──
        if profile:
            # Officers
            officers = _map_officers(profile)
            if officers:
                bd.direct_owners = officers
                bd.executive_officers = [o for o in officers if "title" in o]
                changes.append("officers")

            # Types of business
            services, total, other = _map_types_of_business(profile)
            if services:
                bd.types_of_business = services
                changes.append("types_of_business")
            if total is not None:
                bd.types_of_business_total = total
            if other:
                bd.types_of_business_other = other

            # Clearing info
            if profile.operations:
                if profile.operations.clearing_statement:
                    bd.firm_operations_text = profile.operations.clearing_statement
                    changes.append("clearing_statement")
                if profile.operations.clearing_raw_text:
                    bd.clearing_raw_text = profile.operations.clearing_raw_text
                if profile.operations.clearing_type:
                    bd.clearing_classification = profile.operations.clearing_type.value
                    changes.append("clearing_classification")

                # Introducing arrangements
                if profile.operations.introducing_arrangements:
                    # Clear old ones
                    await db.execute(
                        delete(IntroducingArrangement).where(IntroducingArrangement.bd_id == bd_id)
                    )
                    for arr in profile.operations.introducing_arrangements:
                        eff_date = None
                        if arr.effective_date:
                            try:
                                eff_date = date.fromisoformat(str(arr.effective_date))
                            except (ValueError, TypeError):
                                pass
                        db.add(IntroducingArrangement(
                            bd_id=bd_id,
                            statement=arr.statement,
                            business_name=arr.business_name,
                            effective_date=eff_date,
                            description=arr.description,
                        ))
                    changes.append(f"introducing_arrangements({len(profile.operations.introducing_arrangements)})")

                # Industry arrangements (the three yes/no statements). Always
                # clear-then-insert keyed by bd_id so re-runs converge to the
                # parser's latest view; the UNIQUE(bd_id, kind) constraint in
                # the schema keeps at most one row per kind per firm.
                if profile.operations.industry_arrangements:
                    await db.execute(
                        delete(IndustryArrangement).where(IndustryArrangement.bd_id == bd_id)
                    )
                    for ind in profile.operations.industry_arrangements:
                        ind_eff = None
                        if ind.effective_date:
                            try:
                                # FINRA writes MM/DD/YYYY; convert to a real
                                # date for the Postgres DATE column.
                                month, day, year = ind.effective_date.split("/")
                                ind_eff = date(int(year), int(month), int(day))
                            except (ValueError, TypeError, AttributeError):
                                pass
                        db.add(IndustryArrangement(
                            bd_id=bd_id,
                            kind=ind.kind,
                            has_arrangement=ind.has_arrangement,
                            partner_name=ind.partner_name,
                            partner_crd=ind.partner_crd,
                            partner_address=ind.partner_address,
                            effective_date=ind_eff,
                            description=ind.description,
                        ))
                    changes.append(f"industry_arrangements({len(profile.operations.industry_arrangements)})")

            # Firm history
            if profile.history:
                if profile.history.formation_date:
                    bd.formation_date = profile.history.formation_date
                    changes.append("formation_date")
                if profile.history.registration_date and bd.registration_date is None:
                    bd.registration_date = profile.history.registration_date

            # Website
            if not bd.website and profile.firm_name:
                pass  # FINRA parser doesn't extract website

        # ── FOCUS data ──
        if focus_current:
            # Primary contact
            if focus_current.contact and focus_current.contact.full_name:
                c = focus_current.contact
                # Delete old focus_report contacts, insert new one
                await db.execute(
                    delete(ExecutiveContact).where(
                        ExecutiveContact.bd_id == bd_id,
                        ExecutiveContact.source == "focus_report",
                    )
                )
                db.add(ExecutiveContact(
                    bd_id=bd_id,
                    name=c.full_name,
                    title=(c.title or "Filing Contact")[:255],
                    email=c.email,
                    phone=c.phone,
                    source="focus_report",
                    enriched_at=datetime.now(timezone.utc),
                ))
                changes.append("focus_contact")

            # Financials
            if focus_current.financials:
                fin = focus_current.financials
                if fin.net_capital is not None:
                    bd.latest_net_capital = _to_decimal(fin.net_capital)
                    changes.append("net_capital")
                if fin.total_assets is not None:
                    bd.latest_total_assets = _to_decimal(fin.total_assets)
                    changes.append("total_assets")

            # YoY growth
            yoy = compute_all_yoy(focus_current, focus_prior)
            nc_yoy = yoy.get("net_capital_yoy")
            ta_yoy = yoy.get("total_assets_yoy")
            if nc_yoy and not nc_yoy.insufficient_data:
                bd.yoy_growth = Decimal(str(round(nc_yoy.growth_pct * 100, 2)))
                changes.append("yoy_growth")
            if ta_yoy and not ta_yoy.insufficient_data:
                bd.total_assets_yoy = Decimal(str(round(ta_yoy.growth_pct * 100, 2)))

            # Health status
            if bd.latest_net_capital is not None and bd.required_min_capital is not None:
                bd.health_status = classify_health_status(
                    latest_net_capital=float(bd.latest_net_capital),
                    required_min_capital=float(bd.required_min_capital),
                    yoy_growth=float(bd.yoy_growth) if bd.yoy_growth else None,
                )

        await db.commit()

    return {"changes": changes}


# ──────────────────────────────────────────────────────────────
# Main batch runner
# ──────────────────────────────────────────────────────────────

async def run(*, offset: int = 0, limit: int | None = None, single_crd: str | None = None, save_pdfs: bool = False):
    """Run the extractor for all CRDs from the web app DB."""

    # Step 1: Get CRDs from web app DB
    async with SessionLocal() as db:
        if single_crd:
            stmt = select(BrokerDealer.id, BrokerDealer.name, BrokerDealer.crd_number).where(
                BrokerDealer.crd_number == single_crd
            )
        else:
            stmt = (
                select(BrokerDealer.id, BrokerDealer.name, BrokerDealer.crd_number)
                .where(BrokerDealer.crd_number.is_not(None))
                .order_by(BrokerDealer.id.asc())
            )
        rows = list((await db.execute(stmt)).all())
        await db.close()

    if offset > 0:
        rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]

    total = len(rows)
    logger.info("Starting FINRA extraction for %d firms (SEC X-17A-5 skipped)", total)

    ok = 0
    failed = 0
    start_time = time.monotonic()

    async with FinraClient() as finra:
        for idx, (bd_id, bd_name, crd) in enumerate(rows):
            if (idx + 1) % 50 == 0 or idx == 0:
                logger.info("Progress: %d/%d (ok=%d, failed=%d)", idx + 1, total, ok, failed)

            profile: FirmProfile | None = None

            # ── Download + Parse FINRA BrokerCheck PDF ──
            try:
                pdf_bytes = await finra.download_pdf(crd)
                profile = parse_finra_pdf(pdf_bytes, queried_name=bd_name)
                apply_classification(profile)
                if save_pdfs:
                    Path(f".tmp/raw-pdfs/{crd}_finra.pdf").parent.mkdir(parents=True, exist_ok=True)
                    Path(f".tmp/raw-pdfs/{crd}_finra.pdf").write_bytes(pdf_bytes)
            except Exception as exc:
                logger.warning("  [%d/%d] %s (CRD %s): FINRA failed - %s", idx + 1, total, bd_name, crd, exc)
                failed += 1
                continue

            # ── Save to web app DB ──
            try:
                result = await _save_firm_to_webapp(crd, bd_id, profile, None, None)
                changes = result.get("changes", [])
                if changes:
                    print(f"  [{idx+1}/{total}] {bd_name}: {', '.join(changes)}")
                else:
                    print(f"  [{idx+1}/{total}] {bd_name}: no new data")
                ok += 1
            except Exception as exc:
                logger.error("  [%d/%d] %s: DB save failed - %s", idx + 1, total, bd_name, exc)
                failed += 1

    elapsed = time.monotonic() - start_time
    print(f"\n{'=' * 60}")
    print(f"  FINRA EXTRACTION COMPLETE")
    print(f"  Total: {total}, OK: {ok}, Failed: {failed}")
    print(f"  Time: {elapsed:.0f}s ({elapsed/max(total,1):.1f}s per firm)")
    print(f"{'=' * 60}")


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run BrokerCheck extractor and save to web app DB")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--crd", type=str, default=None, help="Single CRD number to process")
    parser.add_argument("--save-pdfs", action="store_true")
    args = parser.parse_args()

    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) as runner:
            runner.run(run(offset=args.offset, limit=args.limit, single_crd=args.crd, save_pdfs=args.save_pdfs))
    else:
        asyncio.run(run(offset=args.offset, limit=args.limit, single_crd=args.crd, save_pdfs=args.save_pdfs))
