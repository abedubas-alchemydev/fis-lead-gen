"""Import clearing-agency / SRO memberships from committed directory files.

Why this exists. The OCC member directory and the DTCC DTC/NSCC/FICC
participant directories block automated fetches (HTTP 403) and split
their lists across gated, varied-format exports. So instead of a live
scraper, an operator drops the official exports — normalized to one CSV
per agency — into a seed directory, and this script parses them, matches
each listed firm to our broker-dealer / investment-advisor records by
*name*, and writes membership rows.

Matching. The directories expose firm names + their own member numbers
but no CRD, so name is the only join key. We build an in-memory index of
every firm's normalized name (plus DBA names and resolver aliases for
broker-dealers) once, then look up each directory entry in O(1). A name
that maps to exactly one firm auto-applies (``active``); a name shared by
multiple firms is ambiguous and routed to ``needs_review``. Entries that
match nothing are reported but not written. Matching logic lives in
``app.services.clearing_membership_matcher`` so it stays identical to the
tested implementation.

Seed files. ``backend/data/clearing_directories/`` with one CSV per
agency (see that directory's README for the column contract):

    occ_members.csv  dtc_participants.csv  nscc_members.csv
    ficc_gov_members.csv  ficc_mbs_members.csv

Columns: ``agency,member_number,member_name,city,state`` (``agency`` is
optional in-file; inferred from the filename when absent).

The ``clearing_membership_checked_at`` sentinel on each firm is stamped
only on a *full* run (all five agency files processed), so a firm with no
membership rows can be shown as "not a member" rather than "unknown".
Subset runs (``--agency``) update memberships without re-stamping.

Usage::

    # Dry-run (default): parse, match, print summary, no DB writes
    python scripts/import_clearing_agency_memberships.py

    # Apply: upsert memberships, stamp checked_at, record a PipelineRun
    python scripts/import_clearing_agency_memberships.py --apply

    # Subset + review report
    python scripts/import_clearing_agency_memberships.py --agency OCC,DTC \
        --review-out reports/clearing_review.csv

    # Deactivate active rows no longer in the loaded files (never touches
    # human-approved 'manual' rows)
    python scripts/import_clearing_agency_memberships.py --apply --reconcile

Dependencies (already in project requirements): sqlalchemy[asyncio],
psycopg (v3).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.clearing_agency_membership import CLEARING_AGENCIES  # noqa: E402
from app.services.clearing_membership_matcher import (  # noqa: E402
    FirmIndex,
    index_firm,
    match_name,
)

try:
    from app.core.config import settings  # noqa: E402
except Exception:  # pragma: no cover - settings optional if DATABASE_URL is set
    settings = None  # type: ignore[assignment]


if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

logger = logging.getLogger("import_clearing_agency_memberships")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


PIPELINE_NAME = "clearing_membership_import"
DEFAULT_DATA_DIR = BACKEND_ROOT / "data" / "clearing_directories"
FILENAME_TO_AGENCY = {
    "occ_members.csv": "OCC",
    "dtc_participants.csv": "DTC",
    "nscc_members.csv": "NSCC",
    "ficc_gov_members.csv": "FICC-GOV",
    "ficc_mbs_members.csv": "FICC-MBS",
}
BATCH_SIZE = 500
_METHOD_RANK = {"manual": 4, "exact_normalized": 3, "dba": 2, "alias": 1}
_STATUS_RANK = {"active": 2, "needs_review": 1}


@dataclass
class DirectoryEntry:
    agency: str
    member_number: str | None
    member_name: str
    source_file: str
    source_version: str


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _read_directory_file(path: Path, agency_filter: set[str] | None) -> list[DirectoryEntry]:
    """Parse one agency CSV into entries. Agency is inferred from filename
    unless a row carries its own ``agency`` column.
    """
    raw = path.read_bytes()
    source_version = hashlib.sha1(raw).hexdigest()[:12]
    file_agency = FILENAME_TO_AGENCY.get(path.name.lower())

    entries: list[DirectoryEntry] = []
    text_io = raw.decode("utf-8-sig").splitlines()
    reader = csv.DictReader(text_io)
    if reader.fieldnames is None:
        logger.warning("%s: no header row; skipping", path.name)
        return entries
    field_map = {(name or "").strip().lower(): name for name in reader.fieldnames}

    name_col = field_map.get("member_name") or field_map.get("name")
    if name_col is None:
        logger.warning("%s: no 'member_name' column; skipping", path.name)
        return entries
    num_col = field_map.get("member_number")
    agency_col = field_map.get("agency")

    for row in reader:
        member_name = _clean(row.get(name_col))
        if not member_name:
            continue
        row_agency = _clean(row.get(agency_col)) if agency_col else None
        agency = (row_agency or file_agency or "").upper()
        if agency not in CLEARING_AGENCIES:
            logger.warning(
                "%s: row '%s' has unknown/absent agency %r; skipping",
                path.name, member_name, agency or None,
            )
            continue
        if agency_filter and agency not in agency_filter:
            continue
        entries.append(
            DirectoryEntry(
                agency=agency,
                member_number=_clean(row.get(num_col)) if num_col else None,
                member_name=member_name,
                source_file=path.name,
                source_version=source_version,
            )
        )
    return entries


async def _load_bd_index(conn: AsyncConnection) -> tuple[FirmIndex, dict[int, str]]:
    index: FirmIndex = {}
    names: dict[int, str] = {}
    result = await conn.stream(
        text("SELECT id, name, dba_names, resolver_aliases FROM broker_dealers")
    )
    async for row in result:
        firm_id, name, dba_names, resolver_aliases = row
        names[firm_id] = name
        named: list[tuple[str | None, str]] = [(name, "exact_normalized")]
        for dba in dba_names or []:
            if isinstance(dba, str):
                named.append((dba, "dba"))
        for alias in resolver_aliases or []:
            if isinstance(alias, str):
                named.append((alias, "alias"))
        index_firm(index, firm_id, named)
    return index, names


async def _load_ia_index(conn: AsyncConnection) -> tuple[FirmIndex, dict[int, str]]:
    index: FirmIndex = {}
    names: dict[int, str] = {}
    result = await conn.stream(
        text("SELECT id, name, legal_name FROM investment_advisors")
    )
    async for row in result:
        firm_id, name, legal_name = row
        names[firm_id] = name
        named: list[tuple[str | None, str]] = [(name, "exact_normalized")]
        if legal_name and legal_name != name:
            named.append((legal_name, "exact_normalized"))
        index_firm(index, firm_id, named)
    return index, names


def _better(existing: dict, candidate: dict) -> dict:
    """Pick the stronger of two candidate rows for the same (firm, agency):
    active beats needs_review, then higher confidence, then stronger method.
    """
    if _STATUS_RANK[candidate["status"]] != _STATUS_RANK[existing["status"]]:
        return candidate if _STATUS_RANK[candidate["status"]] > _STATUS_RANK[existing["status"]] else existing
    ec = existing["match_confidence"] or 0.0
    cc = candidate["match_confidence"] or 0.0
    if cc != ec:
        return candidate if cc > ec else existing
    if _METHOD_RANK[candidate["match_method"]] > _METHOD_RANK[existing["match_method"]]:
        return candidate
    return existing


_UPSERT_SQL = """
INSERT INTO clearing_agency_memberships
    ({firm_col}, agency, member_number, member_name_raw, source_file,
     source_version, match_method, match_confidence, status,
     pipeline_run_id, created_at, updated_at)
VALUES
    (:firm_id, :agency, :member_number, :member_name_raw, :source_file,
     :source_version, :match_method, :match_confidence, :status,
     :pipeline_run_id, now(), now())
ON CONFLICT ({firm_col}, agency) WHERE {firm_col} IS NOT NULL
DO UPDATE SET
    member_number = EXCLUDED.member_number,
    member_name_raw = EXCLUDED.member_name_raw,
    source_file = EXCLUDED.source_file,
    source_version = EXCLUDED.source_version,
    match_method = EXCLUDED.match_method,
    match_confidence = EXCLUDED.match_confidence,
    status = EXCLUDED.status,
    pipeline_run_id = EXCLUDED.pipeline_run_id,
    updated_at = now()
WHERE clearing_agency_memberships.match_method <> 'manual'
"""


async def _upsert(conn: AsyncConnection, firm_col: str, rows: list[dict]) -> None:
    stmt = text(_UPSERT_SQL.format(firm_col=firm_col))
    for start in range(0, len(rows), BATCH_SIZE):
        await conn.execute(stmt, rows[start : start + BATCH_SIZE])


def _write_review_csv(path: Path, needs_review: list[dict], unmatched: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "kind", "agency", "member_name", "member_number",
            "candidate_side", "candidate_firm_id", "candidate_firm_name",
            "match_method", "match_confidence", "source_file",
        ])
        for r in needs_review:
            writer.writerow([
                "needs_review", r["agency"], r["member_name_raw"], r["member_number"],
                r["side"], r["firm_id"], r["firm_name"],
                r["match_method"], r["match_confidence"], r["source_file"],
            ])
        for r in unmatched:
            writer.writerow([
                "unmatched", r["agency"], r["member_name"], r["member_number"],
                "", "", "", "", "", r["source_file"],
            ])


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import clearing-agency / SRO memberships from directory CSVs."
    )
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--apply", action="store_true", help="write to the DB; default is dry-run")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="dir with per-agency CSVs")
    parser.add_argument("--agency", default=None, help="comma-separated subset, e.g. OCC,DTC")
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="deactivate active rows for loaded agencies no longer matched (skips 'manual')",
    )
    parser.add_argument("--review-out", default=None, help="write needs_review + unmatched CSV here")
    args = parser.parse_args()

    db_url = args.db_url or (settings.database_url if settings else None)
    if not db_url:
        logger.error("no DATABASE_URL env var, no --db-url, and no settings; aborting")
        return 2
    db_url = _normalize_db_url(db_url)

    agency_filter: set[str] | None = None
    if args.agency:
        agency_filter = {a.strip().upper() for a in args.agency.split(",") if a.strip()}
        unknown = agency_filter - set(CLEARING_AGENCIES)
        if unknown:
            logger.error("unknown agency code(s): %s", ", ".join(sorted(unknown)))
            return 2

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        logger.error("data dir not found: %s", data_dir)
        return 2

    # --- read directory files ---
    entries: list[DirectoryEntry] = []
    loaded_agencies: set[str] = set()
    for csv_path in sorted(data_dir.glob("*.csv")):
        file_entries = _read_directory_file(csv_path, agency_filter)
        if file_entries:
            entries.extend(file_entries)
            loaded_agencies.update(e.agency for e in file_entries)
            logger.info("%s: %d entries", csv_path.name, len(file_entries))
    if not entries:
        logger.error("no directory entries parsed from %s; aborting", data_dir)
        return 2

    is_full_run = loaded_agencies == set(CLEARING_AGENCIES)

    engine = create_async_engine(db_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            logger.info("indexing firms ...")
            bd_index, bd_names = await _load_bd_index(conn)
            ia_index, ia_names = await _load_ia_index(conn)
            logger.info(
                "indexed %d BD keys, %d IA keys", len(bd_index), len(ia_index)
            )

        # --- match ---
        bd_cands: dict[tuple[int, str], dict] = {}
        ia_cands: dict[tuple[int, str], dict] = {}
        needs_review: list[dict] = []
        unmatched: list[dict] = []

        for entry in entries:
            matched = False
            for side, index, cands, names in (
                ("bd", bd_index, bd_cands, bd_names),
                ("ia", ia_index, ia_cands, ia_names),
            ):
                for m in match_name(index, entry.member_name):
                    matched = True
                    row = {
                        "firm_id": m.firm_id,
                        "agency": entry.agency,
                        "member_number": entry.member_number,
                        "member_name_raw": entry.member_name,
                        "source_file": entry.source_file,
                        "source_version": entry.source_version,
                        "match_method": m.method,
                        "match_confidence": m.confidence,
                        "status": m.status,
                    }
                    key = (m.firm_id, entry.agency)
                    cands[key] = _better(cands[key], row) if key in cands else row
                    if m.status == "needs_review":
                        needs_review.append({
                            **row, "side": side, "firm_name": names.get(m.firm_id, ""),
                        })
            if not matched:
                unmatched.append({
                    "agency": entry.agency,
                    "member_name": entry.member_name,
                    "member_number": entry.member_number,
                    "source_file": entry.source_file,
                })

        # Dedup needs_review rows that were superseded by an active match for
        # the same (side, firm, agency) coming from another directory entry.
        needs_review = [
            r for r in needs_review
            if (r["side"] == "bd" and bd_cands.get((r["firm_id"], r["agency"]), {}).get("status") == "needs_review")
            or (r["side"] == "ia" and ia_cands.get((r["firm_id"], r["agency"]), {}).get("status") == "needs_review")
        ]

        bd_active = sum(1 for v in bd_cands.values() if v["status"] == "active")
        ia_active = sum(1 for v in ia_cands.values() if v["status"] == "active")
        bd_review = sum(1 for v in bd_cands.values() if v["status"] == "needs_review")
        ia_review = sum(1 for v in ia_cands.values() if v["status"] == "needs_review")

        logger.info(
            "matched: BD active=%d review=%d | IA active=%d review=%d | unmatched=%d",
            bd_active, bd_review, ia_active, ia_review, len(unmatched),
        )
        logger.info(
            "full run=%s (loaded agencies: %s)",
            is_full_run, ", ".join(sorted(loaded_agencies)),
        )

        if args.review_out:
            review_path = Path(args.review_out)
            _write_review_csv(review_path, needs_review, unmatched)
            logger.info("wrote review report: %s", review_path)

        if not args.apply:
            logger.info("DRY RUN — no DB writes. Re-run with --apply to persist.")
            return 0

        # --- apply ---
        async with engine.begin() as conn:
            run_id = (
                await conn.execute(
                    text(
                        "INSERT INTO pipeline_runs "
                        "(pipeline_name, trigger_source, status, total_items) "
                        "VALUES (:n, :t, 'running', :total) RETURNING id"
                    ),
                    {
                        "n": PIPELINE_NAME,
                        "t": f"manual_import:{','.join(sorted(loaded_agencies))}",
                        "total": len(entries),
                    },
                )
            ).scalar_one()

            for v in bd_cands.values():
                v["pipeline_run_id"] = run_id
            for v in ia_cands.values():
                v["pipeline_run_id"] = run_id
            await _upsert(conn, "broker_dealer_id", list(bd_cands.values()))
            await _upsert(conn, "advisor_id", list(ia_cands.values()))

            if args.reconcile:
                agencies = sorted(loaded_agencies)
                bd_keep = [k[0] for k in bd_cands]  # firm ids still matched
                ia_keep = [k[0] for k in ia_cands]
                await conn.execute(
                    text(
                        "UPDATE clearing_agency_memberships SET status='rejected', updated_at=now() "
                        "WHERE agency = ANY(:agencies) AND status='active' "
                        "AND match_method <> 'manual' AND broker_dealer_id IS NOT NULL "
                        "AND NOT (broker_dealer_id = ANY(:keep))"
                    ),
                    {"agencies": agencies, "keep": bd_keep or [-1]},
                )
                await conn.execute(
                    text(
                        "UPDATE clearing_agency_memberships SET status='rejected', updated_at=now() "
                        "WHERE agency = ANY(:agencies) AND status='active' "
                        "AND match_method <> 'manual' AND advisor_id IS NOT NULL "
                        "AND NOT (advisor_id = ANY(:keep))"
                    ),
                    {"agencies": agencies, "keep": ia_keep or [-1]},
                )

            if is_full_run:
                await conn.execute(text("UPDATE broker_dealers SET clearing_membership_checked_at = now()"))
                await conn.execute(text("UPDATE investment_advisors SET clearing_membership_checked_at = now()"))
                logger.info("stamped clearing_membership_checked_at on all firms (full run)")
            else:
                logger.info("partial run — skipped checked_at stamp (use all 5 agency files for a full run)")

            await conn.execute(
                text(
                    "UPDATE pipeline_runs SET status='completed', completed_at=now(), "
                    "processed_items=:processed, success_count=:success, failure_count=:failure, "
                    "notes=:notes WHERE id=:id"
                ),
                {
                    "processed": len(entries),
                    "success": bd_active + ia_active,
                    "failure": bd_review + ia_review + len(unmatched),
                    "notes": (
                        f"BD active={bd_active} review={bd_review}; "
                        f"IA active={ia_active} review={ia_review}; unmatched={len(unmatched)}; "
                        f"full_run={is_full_run}"
                    ),
                    "id": run_id,
                },
            )
        logger.info("APPLIED. pipeline_run id=%s", run_id)
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
