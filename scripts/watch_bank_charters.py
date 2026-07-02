"""Watch official public sources for NEW BANK CHARTERS (national + state)
and keep the ``banks`` vertical fresh — the banking sibling of
``standalone_extract_new_bds.py``.

Sources (all official, public, keyless — no gray-area methods):

1. **FDIC BankFind** (``api.fdic.gov/banks/institutions``) — newly
   *opened* insured institutions, selected on an ``ESTYMD`` (establishment
   date) window, corroborated by ``/banks/history`` ``CHANGECODE:110``
   ("new institution") events so a cert that lands in the history stream a
   day before the institutions index rebuild still gets picked up via a
   targeted per-cert fetch.
2. **OCC Corporate Applications Search** (``apps.occ.gov/CAS/api/search``,
   ``filingTypes=2`` "New Bank Charter") — national-bank / federal-trust
   charter *applications* and their action timeline (Receipt → Approved →
   Consummated-Effective / Withdrawn). This is the pending-charter pipeline
   the FDIC can't see: an applicant has no FDIC cert until it opens.
3. **OCC national-banks directory** (``national-by-name.xlsx``) — used to
   RECONCILE an opened OCC application onto its FDIC row (the workbook
   carries charter number, FDIC CERT, and RSSD side by side). Conservative:
   links only on an exact normalized-name (+state when both sides have one)
   match that is UNIQUE.
4. **OCC Digital Assets Licensing Applications page** (client addition) —
   novel / de-novo digital-asset national bank charters and conversions.
   Matching banks get ``digital_assets=true`` plus the public-portion
   application PDF *URLs* (the PDFs are never fetched or rendered).

Idempotency. Every write is an upsert keyed on a stable source identifier
(``fdic_cert`` / ``occ_control_number`` / the ``(bank_id, action,
action_date)`` event key), charter-status transitions are forward-only, and
the digital-assets tag is sticky — so the nightly runs use an OVERLAPPING
date window (default: trailing ``--window-days`` 30) and re-seeing a row is
a no-op. Re-running any window is safe.

Dry-run is the default: it fetches everything, logs exactly what WOULD be
written, and touches nothing. ``--apply`` writes, committing after each
phase so a Cloud Run timeout can only lose the in-flight phase.

Usage::

    # dry-run the default trailing-30-day window (no writes)
    python scripts/watch_bank_charters.py

    # nightly apply (what the Cloud Run Job runs)
    python scripts/watch_bank_charters.py --apply

    # one-time backfill: widen the window (e.g. all of 2024-present)
    python scripts/watch_bank_charters.py --apply --from-date 2024-01-01

    # override DB URL (defaults to the DATABASE_URL env var)
    python scripts/watch_bank_charters.py --db-url <URL>

Runs as a Cloud Run Job (backend image) via
``--args=scripts/watch_bank_charters.py,--apply``; see
``docs/runbooks/bank-charter-watch.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Local checkouts run this script from the repo root without the backend
# package on the path; the backend lives at <repo>/backend (same bootstrap
# as scripts/standalone_extract_new_bds.py). In the backend image the dir
# doesn't exist (backend/ is copied to /app) and PYTHONPATH=/app already
# makes ``app.*`` importable, so this is a no-op there.
_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if _BACKEND_ROOT.is_dir() and str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Line-buffer stdout/stderr so Cloud Run streams logs promptly. Guarded so
# importing this module under pytest never blows up.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

logger = logging.getLogger("watch_bank_charters")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# Default trailing window. 30 days deliberately overlaps night-to-night:
# CAS filings accrete actions over weeks, the FDIC institutions index can
# lag its history stream, and upserts make the overlap free.
_DEFAULT_WINDOW_DAYS = 30


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - argparse surfaces the message
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def resolve_window(
    *,
    from_date: date | None,
    to_date: date | None,
    window_days: int,
    today: date | None = None,
) -> tuple[date, date]:
    """Resolve the [start, end] ingest window (pure; unit-tested).

    Explicit ``--from-date`` / ``--to-date`` win; otherwise the window is
    the trailing ``window_days`` ending today. A backwards window raises
    so a typo'd backfill can't silently no-op.
    """
    anchor = today or date.today()
    end = to_date or anchor
    start = from_date or (end - timedelta(days=window_days))
    if start > end:
        raise ValueError(f"window start {start} is after end {end}")
    return start, end


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Watch FDIC BankFind + OCC CAS (+ the OCC digital-assets page) for "
            "new bank charters and upsert them into the banks vertical."
        ),
    )
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually upsert rows. Without this, the script runs dry.",
    )
    parser.add_argument(
        "--from-date", type=_parse_iso_date, default=None,
        help="Window start (YYYY-MM-DD). Set wide (e.g. 2024-01-01) for the one-time backfill.",
    )
    parser.add_argument(
        "--to-date", type=_parse_iso_date, default=None,
        help="Window end (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--window-days", type=int, default=_DEFAULT_WINDOW_DAYS,
        help=f"Trailing window length when --from-date is omitted (default {_DEFAULT_WINDOW_DAYS}).",
    )
    parser.add_argument("--skip-fdic", action="store_true", help="Skip the FDIC BankFind phase.")
    parser.add_argument("--skip-occ", action="store_true", help="Skip the OCC CAS phase.")
    parser.add_argument(
        "--skip-digital-assets", action="store_true",
        help="Skip the OCC digital-assets page phase.",
    )
    args = parser.parse_args(argv)

    if not args.db_url:
        logger.error("no DATABASE_URL env var and no --db-url; aborting")
        return 2

    try:
        window_start, window_end = resolve_window(
            from_date=args.from_date, to_date=args.to_date, window_days=args.window_days
        )
    except ValueError as exc:
        logger.error("bad window: %s", exc)
        return 2

    # Imported lazily (after the sys.path bootstrap) so the module stays
    # cheap to import for the pure-function unit tests.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services.banks import BankRepository
    from app.services.fdic_bankfind import FdicBankFindService
    from app.services.occ_cas import OccCasService

    engine = create_async_engine(_normalize_db_url(args.db_url), pool_pre_ping=True)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    fdic = FdicBankFindService()
    occ = OccCasService()
    repository = BankRepository()

    mode = "apply" if args.apply else "dry-run"
    logger.info("watch_bank_charters: %s, window %s..%s", mode, window_start, window_end)

    summary: dict[str, int] = {}
    try:
        # ── Phase 1: FDIC BankFind (opened insured institutions) ──────────
        if not args.skip_fdic:
            records = await fdic.fetch_institutions_established_between(window_start, window_end)
            known_certs = {record.cert for record in records}
            # Corroboration: CHANGECODE:110 "New Institution" events whose cert is missing from
            # the institutions window (index lag) get a targeted fetch.
            history = await fdic.fetch_new_institution_history(window_start, window_end)
            missing_certs = sorted({ev.cert for ev in history} - known_certs)
            if missing_certs:
                logger.info(
                    "fdic: %d cert(s) seen only in /history (index lag): %s",
                    len(missing_certs), ", ".join(missing_certs),
                )
                records.extend(await fdic.fetch_institutions_by_certs(missing_certs))
            summary["fdic_records"] = len(records)
            for record in records:
                logger.info(
                    "  fdic cert=%s name=%r est=%s state=%s charter=%s",
                    record.cert, record.name, record.established_date,
                    record.state, record.charter_authority,
                )
            if args.apply and records:
                async with session_maker() as db:
                    written = await repository.upsert_fdic_institutions(db, records)
                    await db.commit()
                logger.info("fdic: upserted %d row(s)", written)
            elif records:
                logger.info("dry-run: would upsert %d FDIC row(s)", len(records))

        # ── Phase 2: OCC CAS (charter applications + action timeline) ─────
        if not args.skip_occ:
            filings = await occ.fetch_new_charter_filings(window_start, window_end)
            summary["occ_filings"] = len(filings)
            for filing in filings:
                logger.info(
                    "  occ cn=%s act=%s on %s name=%r st=%s",
                    filing.control_number, filing.action, filing.action_date,
                    filing.bank_name, filing.state,
                )
            if args.apply and filings:
                async with session_maker() as db:
                    banks_touched, events_inserted = await repository.upsert_occ_filings(db, filings)
                    await db.commit()
                summary["occ_banks_touched"] = banks_touched
                summary["occ_events_inserted"] = events_inserted
                logger.info(
                    "occ: touched %d bank(s), inserted %d new event(s)",
                    banks_touched, events_inserted,
                )
            elif filings:
                logger.info("dry-run: would upsert %d OCC filing action(s)", len(filings))

        # ── Phase 3: reconcile opened OCC applications -> FDIC identity ───
        # (Runs in both modes; writes only on --apply.)
        if not args.skip_occ:
            async with session_maker() as db:
                unreconciled = await repository.list_unreconciled_occ_banks(db)
                summary["occ_unreconciled"] = len(unreconciled)
                if unreconciled:
                    linked = await _reconcile_occ_banks(
                        db,
                        repository=repository,
                        occ=occ,
                        fdic=fdic,
                        unreconciled=unreconciled,
                        apply=args.apply,
                    )
                    summary["occ_reconciled"] = linked
                    if args.apply:
                        await db.commit()

        # ── Phase 4: OCC digital-assets page (tag + PDF links) ────────────
        if not args.skip_digital_assets:
            applications = await occ.fetch_digital_asset_applications()
            summary["digital_asset_rows"] = len(applications)
            tagged = unmatched = 0
            async with session_maker() as db:
                for application in applications:
                    matches = await repository.find_banks_by_normalized_name(
                        db, application.applicant
                    )
                    if len(matches) != 1:
                        # 0 = a conversion / not-yet-ingested applicant; >1 =
                        # ambiguous. Both are log-only by design — never
                        # guess. (The page has no state/CRD-like key to
                        # disambiguate on.)
                        unmatched += 1
                        logger.info(
                            "  digital-assets: %r received %s -> %d match(es); skipping",
                            application.applicant, application.received_date, len(matches),
                        )
                        continue
                    bank = matches[0]
                    if args.apply:
                        changed = repository.apply_digital_asset_tag(
                            bank,
                            pdf_url=application.pdf_url,
                            pdf_title=application.applicant,
                            received_date=application.received_date,
                        )
                        tagged += int(changed)
                    else:
                        tagged += 1
                        logger.info(
                            "  dry-run: would tag bank id=%d %r digital_assets=true pdf=%s",
                            bank.id, bank.name, application.pdf_url or "-",
                        )
                if args.apply:
                    await db.commit()
            summary["digital_assets_tagged"] = tagged
            summary["digital_assets_unmatched"] = unmatched

        logger.info(
            "summary (%s): %s", mode,
            " ".join(f"{key}={value}" for key, value in sorted(summary.items())) or "nothing to do",
        )
        return 0
    finally:
        await engine.dispose()


async def _reconcile_occ_banks(
    db,
    *,
    repository,
    occ,
    fdic,
    unreconciled,
    apply: bool,
) -> int:
    """Link opened OCC application rows to their FDIC/charter identity.

    Uses the OCC national-banks directory (charter no ↔ CERT ↔ RSSD).
    Match rule (conservative, in order):
      1. exact normalized-name match, narrowed by state when BOTH sides
         have one — must be UNIQUE among directory rows;
      2. on a match: stamp ``occ_charter_number`` / ``fed_rssd``; when the
         directory carries a CERT, either fold this row into an existing
         FDIC row for that cert (both were inserted independently) or
         stamp the cert and pull the FDIC record to enrich in place.
    Returns the number of rows linked (or that WOULD link, in dry-run).
    """
    from app.services.occ_cas import normalize_bank_name

    directory = await occ.fetch_national_bank_directory()
    by_name: dict[str, list] = {}
    for row in directory:
        by_name.setdefault(normalize_bank_name(row.name), []).append(row)

    linked = 0
    newly_linked_certs: list[str] = []
    for bank in unreconciled:
        candidates = by_name.get(normalize_bank_name(bank.name), [])
        if bank.state:
            narrowed = [c for c in candidates if not c.state or c.state == bank.state]
        else:
            narrowed = candidates
        if len(narrowed) != 1:
            if candidates:
                logger.info(
                    "  reconcile: %r -> %d directory candidate(s); skipping (needs unique)",
                    bank.name, len(narrowed),
                )
            continue
        match = narrowed[0]
        linked += 1
        if not apply:
            logger.info(
                "  dry-run: would link bank id=%d %r -> charter=%s cert=%s rssd=%s",
                bank.id, bank.name, match.charter_number, match.fdic_cert, match.fed_rssd,
            )
            continue

        bank.occ_charter_number = bank.occ_charter_number or match.charter_number
        bank.fed_rssd = bank.fed_rssd or match.fed_rssd
        if match.fdic_cert:
            existing = await repository.find_by_cert(db, match.fdic_cert)
            if existing is not None and existing.id != bank.id:
                # The opened institution already landed as its own FDIC row
                # before this application gained its cert — fold them.
                await repository.merge_occ_bank_into_fdic_row(db, bank, existing)
            else:
                bank.fdic_cert = match.fdic_cert
                newly_linked_certs.append(match.fdic_cert)
        logger.info(
            "  reconcile: linked bank %r -> charter=%s cert=%s",
            bank.name, match.charter_number, match.fdic_cert or "-",
        )

    # Enrich freshly-linked certs from FDIC (fills established/insured
    # dates, financials, and flips charter_status to opened via the
    # forward-only upsert).
    if apply and newly_linked_certs:
        await db.flush()
        records = await fdic.fetch_institutions_by_certs(newly_linked_certs)
        if records:
            await repository.upsert_fdic_institutions(db, records)
    return linked


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
