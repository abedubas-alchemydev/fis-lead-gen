"""Watch official public sources for NEW BANK CHARTERS (national + state)
and keep the ``banks`` vertical fresh — the banking sibling of
``standalone_extract_new_bds.py``.

Sources (all official + public — no gray-area methods; keyless except the
OPTIONAL ``OCC_API_KEY`` for source 3's primary path, which degrades to a
keyless fallback when unset):

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
3. **OCC active-institutions directory** — used to RECONCILE an opened OCC
   application onto its FDIC row (charter number, FDIC CERT, and RSSD side
   by side). PRIMARY: the official, documented **OCC Institutions API**
   (``api.occ.gov/institutions/active``, api.data.gov key via the
   ``OCC_API_KEY`` env var; also carries LEI + CharterType enrichment).
   FALLBACK when the key is unset or the API is down: the keyless
   ``national-by-name.xlsx`` workbook. Identical match semantics either
   way — links only on an exact normalized-name (+state when both sides
   have one) match that is UNIQUE; the summary logs which source ran
   (``reconcile_source=api|xlsx``).
4. **OCC Digital Assets Licensing Applications page** (client addition) —
   novel / de-novo digital-asset national bank charters and conversions.
   Matching banks get ``digital_assets=true`` plus the public-portion
   application PDF *URLs* (the PDFs are never fetched or rendered). The
   page only lists CURRENT applications, so the phase also applies the
   curated ``KNOWN_DIGITAL_ASSET_APPLICANTS`` seed for publicly-known
   digital-asset charters that rolled off the page before our first scrape
   — and the one-off ``--backfill-digital-assets-history`` mode makes that
   data-driven by unioning every archived monthly capture of the page
   (OCC's own content, served from the public Internet Archive) through
   the same tagging path.

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

    # opt-in one-off: seed the ENTIRE active bank universe (every active
    # FDIC-insured institution + every OCC-only trust) before the windowed
    # phases — backfills far beyond just new/pending charters
    python scripts/watch_bank_charters.py --apply --full-sync

    # one-off: union the Wayback Machine's monthly captures of the OCC
    # digital-assets page (applicants that rolled off pre-scrape)
    python scripts/watch_bank_charters.py --apply --skip-fdic --skip-occ \
        --backfill-digital-assets-history

    # opt-in: extract PEOPLE (contact person / organizers / proposed
    # officers / counsel) from the public-portion application PDFs into
    # bank_contacts (dry-run first; --apply writes)
    python scripts/watch_bank_charters.py --skip-fdic --skip-occ --extract-contacts
    python scripts/watch_bank_charters.py --apply --skip-fdic --skip-occ --extract-contacts

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

# Curated digital-assets seed. The OCC digital-assets page lists only
# CURRENT applications (decided ones are pruned), so a publicly-known
# digital-asset charter that rolled off the page before our first scrape
# can never be tagged from the page alone. Entries here are
# client-confirmed PUBLIC knowledge (OCC news releases / press coverage) —
# extend with one line each: (name, state, occ_charter_number or None).
# Matching follows the page policy (unique match or log-and-skip, never
# guess); the tag is sticky and seed entries carry no PDFs. See
# docs/runbooks/bank-charter-watch.md, "Manually tagging known
# digital-asset banks".
KNOWN_DIGITAL_ASSET_APPLICANTS: tuple[tuple[str, str, str | None], ...] = (
    ("Erebor Bank, N.A.", "OH", "25357"),  # opened 2026-02; off the page pre-scrape
)


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
    parser.add_argument(
        "--backfill-digital-assets-history",
        action="store_true",
        help=(
            "One-off: union every archived monthly capture of the OCC "
            "digital-assets page (Wayback Machine CDX) and feed the rows "
            "through the same sticky digital-assets tagging. Implies the "
            "digital-assets machinery — it runs even under "
            "--skip-digital-assets (which then skips just the live page + "
            "seed); composes with --apply and the other --skip-* flags."
        ),
    )
    parser.add_argument(
        "--full-sync",
        action="store_true",
        help=(
            "Opt-in: BEFORE the trailing-window phases, seed the ENTIRE "
            "active bank universe — every active FDIC-insured institution "
            "(~4,267) via fetch_all_active_institutions, plus the whole OCC "
            "active-institutions directory (the ~50-150 OCC-only trusts that "
            "carry no FDIC cert) — through the same idempotent upserts the "
            "windowed phases use. NOT part of the nightly Cloud Run args "
            "(kept opt-in like --extract-contacts): run it once, or "
            "occasionally, to backfill beyond new/pending charters. Composes "
            "with --apply (dry-run logs the counts, --apply commits)."
        ),
    )
    parser.add_argument(
        "--extract-contacts",
        action="store_true",
        help=(
            "Opt-in: for banks whose digital_asset_pdfs carries public-"
            "portion application PDF links, download each PDF (https "
            "occ.gov only) and conservatively extract the PEOPLE it names "
            "(contact person, organizers, proposed officers, counsel) into "
            "bank_contacts. Runs after the digital-assets/history phases; "
            "composes with --apply (dry-run logs what it WOULD write). "
            "Ambiguous hits are logged and skipped, never guessed."
        ),
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

    summary: dict[str, int | str] = {}
    try:
        # ── Phase 0: opt-in FULL SYNC (the entire active bank universe) ────
        # Runs BEFORE the windowed phases so the window / reconcile /
        # digital-assets passes then refine the rows it seeded. Opt-in like
        # --extract-contacts and deliberately NOT in the nightly args.
        if args.full_sync:
            summary.update(
                await _run_full_sync(
                    session_maker,
                    fdic=fdic,
                    occ=occ,
                    repository=repository,
                    apply=args.apply,
                )
            )

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
                    linked, reconcile_source = await _reconcile_occ_banks(
                        db,
                        repository=repository,
                        occ=occ,
                        fdic=fdic,
                        unreconciled=unreconciled,
                        apply=args.apply,
                    )
                    summary["occ_reconciled"] = linked
                    summary["reconcile_source"] = reconcile_source
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
                # Curated seed: publicly-known digital-asset charters that
                # rolled off the OCC page before our first scrape (the page
                # lists only current applications). Same session/commit as
                # the page matches.
                seed_tagged, seed_unmatched = await _apply_digital_asset_seed(
                    db, repository=repository, apply=args.apply
                )
                if args.apply:
                    await db.commit()
            summary["digital_assets_tagged"] = tagged
            summary["digital_assets_unmatched"] = unmatched
            summary["digital_assets_seed_tagged"] = seed_tagged
            summary["digital_assets_seed_unmatched"] = seed_unmatched

        # ── Phase 4b: one-off Wayback backfill of the digital-assets page ─
        # The live page lists only CURRENT applications; the public
        # Internet Archive holds monthly captures of OCC's own page, so
        # this mode unions every applicant row EVER listed and feeds the
        # union through the SAME sticky tagging path as live-page rows.
        # Runs regardless of --skip-digital-assets (which skips just the
        # live page + seed); own session/commit like every other phase.
        if args.backfill_digital_assets_history:
            snapshots_parsed, history_entries = (
                await occ.fetch_digital_asset_application_history()
            )
            summary["digital_assets_history_snapshots"] = snapshots_parsed
            summary["digital_assets_history_rows"] = len(history_entries)
            async with session_maker() as db:
                history_tagged, history_unmatched = await _tag_digital_asset_history_entries(
                    db, repository=repository, entries=history_entries, apply=args.apply
                )
                if args.apply:
                    await db.commit()
            summary["digital_assets_history_tagged"] = history_tagged
            summary["digital_assets_history_unmatched"] = history_unmatched

        # ── Phase 5: people from the application PDFs (opt-in) ────────────
        # The banks-vertical sibling of the FOCUS "PERSON TO CONTACT"
        # extraction: contact person / organizers / proposed officers /
        # counsel out of the public-portion PDFs already linked on
        # digital_asset_pdfs. Conservative by construction — ambiguous hits
        # are logged and skipped, never guessed. Runs after the
        # digital-assets phases so a PDF link tagged tonight is extracted
        # tonight.
        if args.extract_contacts:
            from app.services.bank_contact_extraction import BankContactExtractionService

            summary.update(
                await _extract_bank_contacts(
                    session_maker,
                    repository=repository,
                    service=BankContactExtractionService(),
                    apply=args.apply,
                )
            )

        logger.info(
            "summary (%s): %s", mode,
            " ".join(f"{key}={value}" for key, value in sorted(summary.items())) or "nothing to do",
        )
        return 0
    finally:
        await engine.dispose()


async def _run_full_sync(
    session_maker,
    *,
    fdic,
    occ,
    repository,
    apply: bool,
) -> dict[str, int | str]:
    """Phase 0 (opt-in ``--full-sync``): seed the ENTIRE active bank
    universe, not just the trailing window.

    Two phases, each its own short write session/commit (a Cloud Run
    timeout can only lose the in-flight phase), both idempotent upserts so
    re-running is a no-op:

    - **FDIC full sync** — every active FDIC-insured institution
      (``fetch_all_active_institutions``) through the SAME
      ``upsert_fdic_institutions`` the windowed FDIC phase uses; picks up
      the ~4,267 already-established banks a trailing-30-day window never
      sees.
    - **OCC full sync** — the entire OCC active-institutions directory
      through ``upsert_occ_institutions``; picks up the OCC-only trusts that
      carry no FDIC cert. Source selection is IDENTICAL to the reconcile
      phase (official Institutions API primary, keyless
      ``national-by-name.xlsx`` fallback when the key is unset / API down),
      so a missing ``OCC_API_KEY`` degrades the same way here as there.

    Dry-run logs the counts and writes nothing; ``--apply`` commits after
    each phase. Returns the ``fdic_full_*`` / ``occ_full_*`` summary keys.
    """
    summary: dict[str, int | str] = {}

    # ── FDIC full sync: every active insured institution ──────────────────
    records = await fdic.fetch_all_active_institutions()
    summary["fdic_full_records"] = len(records)
    logger.info("full-sync fdic: %d active insured institution(s)", len(records))
    if apply and records:
        async with session_maker() as db:
            written = await repository.upsert_fdic_institutions(db, records)
            await db.commit()
        summary["fdic_full_upserted"] = written
        logger.info("full-sync fdic: upserted %d row(s)", written)
    elif records:
        logger.info("dry-run: would upsert %d FDIC full-sync row(s)", len(records))

    # ── OCC full sync: the entire active-institutions directory ───────────
    # Same source order the reconcile phase uses (API primary, XLSX fallback
    # on None) so the two phases agree on which directory served.
    directory = await occ.fetch_active_institutions()
    occ_source = "api"
    if directory is None:
        occ_source = "xlsx"
        directory = await occ.fetch_national_bank_directory()
    summary["occ_full_rows"] = len(directory)
    summary["occ_full_source"] = occ_source
    logger.info(
        "full-sync occ: %d directory row(s) (source=%s)", len(directory), occ_source
    )
    if apply and directory:
        async with session_maker() as db:
            written = await repository.upsert_occ_institutions(db, directory)
            await db.commit()
        summary["occ_full_upserted"] = written
        logger.info("full-sync occ: upserted %d row(s)", written)
    elif directory:
        logger.info(
            "dry-run: would upsert %d OCC full-sync row(s) (source=%s)",
            len(directory), occ_source,
        )

    return summary


async def _reconcile_occ_banks(
    db,
    *,
    repository,
    occ,
    fdic,
    unreconciled,
    apply: bool,
) -> tuple[int, str]:
    """Link opened OCC application rows to their FDIC/charter identity.

    Uses the OCC active-institutions directory (charter no ↔ CERT ↔ RSSD):
    PRIMARY the official Institutions API (``fetch_active_institutions``,
    None on missing OCC_API_KEY / HTTP error / schema surprise), FALLBACK
    the keyless national-by-name.xlsx workbook. Both sources yield the
    same row shape, so the match semantics below are IDENTICAL either way.
    Match rule (conservative, in order):
      1. exact normalized-name match, narrowed by state when BOTH sides
         have one — must be UNIQUE among directory rows;
      2. on a match: stamp ``occ_charter_number`` / ``fed_rssd`` (plus the
         API-only ``lei`` / ``charter_type`` enrichment when present —
         never overwriting); when the directory carries a CERT, either
         fold this row into an existing FDIC row for that cert (both were
         inserted independently) or stamp the cert and pull the FDIC
         record to enrich in place.
    Returns ``(linked, source)`` — rows linked (or that WOULD link, in
    dry-run) and which directory served: ``"api"`` or ``"xlsx"``.
    """
    from app.services.occ_cas import normalize_bank_name

    directory = await occ.fetch_active_institutions()
    reconcile_source = "api"
    if directory is None:
        # Key unset or API down — fetch_active_institutions already logged
        # the specific reason at WARNING level.
        reconcile_source = "xlsx"
        directory = await occ.fetch_national_bank_directory()
    logger.info(
        "reconcile: directory source=%s (%d row(s))", reconcile_source, len(directory)
    )
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
        # Institutions-API enrichment (XLSX rows carry neither): additive
        # only, never overwrite. Stamped before any merge so the values
        # ride onto the surviving FDIC row.
        bank.lei = bank.lei or match.lei
        bank.charter_type = bank.charter_type or match.charter_type
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
    return linked, reconcile_source


async def _apply_digital_asset_seed(
    db,
    *,
    repository,
    apply: bool,
    seed: tuple[tuple[str, str, str | None], ...] = KNOWN_DIGITAL_ASSET_APPLICANTS,
) -> tuple[int, int]:
    """Tag the curated ``KNOWN_DIGITAL_ASSET_APPLICANTS`` entries.

    Runs after the page matcher in the digital-assets phase. Match rule
    (conservative, same never-guess policy as the page matcher):

    1. ``occ_charter_number`` when the entry carries one (strong key) — a
       unique hit wins outright; two rows claiming one charter number is a
       data problem and skips without falling through;
    2. else — including when no row carries that charter number yet (e.g.
       reconciliation hasn't stamped it) — the same normalized-name matcher
       the page rows use, narrowed by the entry's state like the reconcile
       pass; must be UNIQUE.

    On a match the sticky tag flips via ``apply_digital_asset_tag`` with no
    PDF and no received-date (seed entries carry neither, so the row's
    ``digital_asset_pdfs`` / ``application_received_date`` are untouched
    and re-runs are no-ops). Zero or ambiguous matches log and skip.
    Returns ``(tagged, unmatched)``; dry-run counts matches without writing.
    """
    tagged = unmatched = 0
    for name, state, charter_number in seed:
        matches: list = []
        matched_by = ""
        if charter_number:
            matches = await repository.find_banks_by_occ_charter_number(db, charter_number)
            matched_by = f"charter={charter_number}"
            if len(matches) > 1:
                unmatched += 1
                logger.info(
                    "  digital-assets: seed %r charter=%s -> %d match(es); skipping",
                    name, charter_number, len(matches),
                )
                continue
        if not matches:
            candidates = await repository.find_banks_by_normalized_name(db, name)
            matches = [bank for bank in candidates if not bank.state or bank.state == state]
            matched_by = f"name+state={state}"
            if len(matches) != 1:
                unmatched += 1
                logger.info(
                    "  digital-assets: seed %r (%s) -> %d match(es); skipping",
                    name, state, len(matches),
                )
                continue
        bank = matches[0]
        if not apply:
            tagged += 1
            logger.info(
                "  dry-run: would seed-tag %r (bank id=%s, matched by %s)",
                name, bank.id, matched_by,
            )
            continue
        changed = repository.apply_digital_asset_tag(
            bank, pdf_url=None, pdf_title=None, received_date=None
        )
        tagged += int(changed)
        if changed:
            logger.info(
                "  digital-assets: seed-tagged %r (bank id=%s, matched by %s)",
                name, bank.id, matched_by,
            )
        else:
            logger.info(
                "  digital-assets: seed %r already tagged (bank id=%s); no-op",
                name, bank.id,
            )
    return tagged, unmatched


async def _extract_bank_contacts(
    session_maker,
    *,
    repository,
    service,
    apply: bool,
) -> dict[str, int]:
    """Phase 5 (opt-in ``--extract-contacts``): extract people from the
    public-portion application PDFs into ``bank_contacts``.

    Architecture mirrors the FOCUS batch: the slow work (occ.gov downloads +
    the crash-isolated text subprocess + parsing) runs with NO database
    session open; each bank's upsert is its own short write session/commit,
    so a mid-run failure loses at most the in-flight bank. Idempotent — the
    upsert keys on ``(bank_id, name, coalesce(title,''), source)``, so
    re-running over the same PDFs is a no-op.

    Returns the five summary keys:

    - ``bank_contacts_pdfs_fetched``      — PDFs actually downloaded
      (allowlist-passed, HTTP 200, under the 20MB cap) across all banks;
    - ``bank_contacts_extracted``         — people in the final merged set
      (regex + grounded Gemini, deduped; would-write set in dry-run;
      upserted set under --apply — same number on a re-run, with the DB
      unchanged);
    - ``bank_contacts_skipped_ambiguous`` — pattern hits refused by the
      conservative regex validators (logged, never written);
    - ``bank_contacts_llm_extracted``     — people the Gemini recall pass
      ADDED beyond regex (grounded + novel; ``source='application_pdf_llm'``);
    - ``bank_contacts_llm_dropped_ungrounded`` — Gemini-returned records
      refused by the grounding gate (not printed verbatim on the source
      pages / org vocabulary / unmappable role — logged, never written).

    The Gemini pass is best-effort: without GEMINI_API_KEY (or on any API
    error) the service logs a warning and this phase behaves exactly as the
    regex-only extractor — it never fails because Gemini is down.
    """
    async with session_maker() as db:
        eligible = await repository.list_banks_with_application_pdfs(db)
        rows = [
            (bank.id, bank.name, list(bank.digital_asset_pdfs or []))
            for bank in eligible
        ]
    logger.info("contacts: %d bank(s) carry application PDF link(s)", len(rows))

    fetched = extracted = ambiguous = llm_extracted = llm_dropped = 0
    for bank_id, bank_name, pdf_entries in rows:
        contacts, stats = await service.collect_contacts(
            bank_id=bank_id, bank_name=bank_name, pdf_entries=pdf_entries
        )
        fetched += stats.pdfs_fetched
        extracted += stats.contacts_extracted
        ambiguous += stats.skipped_ambiguous
        llm_extracted += stats.llm_extracted
        llm_dropped += stats.llm_dropped_ungrounded
        if not contacts:
            logger.info(
                "  contacts: bank id=%d %r -> none extracted "
                "(pdfs_fetched=%d, ambiguous=%d)",
                bank_id, bank_name, stats.pdfs_fetched, stats.skipped_ambiguous,
            )
            continue
        if apply:
            async with session_maker() as db:
                inserted, updated = await service.upsert_contacts(db, bank_id, contacts)
                await db.commit()
            logger.info(
                "  contacts: bank id=%d %r -> %d contact(s) "
                "(inserted=%d, updated=%d, ambiguous=%d)",
                bank_id, bank_name, len(contacts), inserted, updated,
                stats.skipped_ambiguous,
            )
        else:
            for contact in contacts:
                logger.info(
                    "  dry-run: would write bank_contact bank id=%d %r "
                    "name=%r title=%r role=%s email=%s phone=%s page=%s source=%s",
                    bank_id, bank_name, contact.name, contact.title,
                    contact.role_context, contact.email or "-",
                    contact.phone or "-", contact.page_number or "-",
                    contact.source,
                )

    return {
        "bank_contacts_pdfs_fetched": fetched,
        "bank_contacts_extracted": extracted,
        "bank_contacts_skipped_ambiguous": ambiguous,
        "bank_contacts_llm_extracted": llm_extracted,
        "bank_contacts_llm_dropped_ungrounded": llm_dropped,
    }


async def _tag_digital_asset_history_entries(
    db,
    *,
    repository,
    entries,
    apply: bool,
) -> tuple[int, int]:
    """Feed Wayback-unioned applicant rows through the page tagging path.

    ``entries`` are ``OccDigitalAssetHistoryEntry`` rows from
    ``OccCasService.fetch_digital_asset_application_history`` (already
    deduped by normalized name + received date, PDF URLs already
    normalized to their original occ.gov form and allowlisted). Policy is
    IDENTICAL to the live-page loop: a UNIQUE normalized-name match tags
    via the sticky ``apply_digital_asset_tag``; zero or ambiguous matches
    log and skip, never guess. The only delta: an entry carries the
    newest non-empty PDF URL *set* for its key, so every URL in the set
    is merged (the repository dedupes by URL, keeping re-runs no-ops).

    Returns ``(tagged, unmatched)``; apply-mode ``tagged`` counts rows
    that actually CHANGED, mirroring the live loop.
    """
    tagged = unmatched = 0
    for entry in entries:
        matches = await repository.find_banks_by_normalized_name(db, entry.applicant)
        if len(matches) != 1:
            # 0 = a conversion / never-ingested applicant (expected for
            # decided history); >1 = ambiguous. Both log-only — never
            # guess, exactly like the live-page rows.
            unmatched += 1
            logger.info(
                "  digital-assets-history: %r received %s -> %d match(es); skipping",
                entry.applicant, entry.received_date, len(matches),
            )
            continue
        bank = matches[0]
        if not apply:
            tagged += 1
            logger.info(
                "  dry-run: would tag bank id=%d %r digital_assets=true pdf=%s",
                bank.id, bank.name, ", ".join(entry.pdf_urls) or "-",
            )
            continue
        changed = False
        for pdf_url in entry.pdf_urls or (None,):
            changed = (
                repository.apply_digital_asset_tag(
                    bank,
                    pdf_url=pdf_url,
                    pdf_title=entry.applicant,
                    received_date=entry.received_date,
                )
                or changed
            )
        tagged += int(changed)
        if changed:
            logger.info(
                "  digital-assets-history: tagged bank id=%d %r (%d pdf url(s))",
                bank.id, bank.name, len(entry.pdf_urls),
            )
    return tagged, unmatched


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
