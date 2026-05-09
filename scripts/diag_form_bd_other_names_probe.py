"""Probe Form BD "Other Names of this Firm" coverage vs. existing dba_names.

One-shot diagnostic. For each CRD, prints a single comparison row:

    CRD=149777 name="MORGAN STANLEY SMITH BARNEY LLC"
      db_dba=[...]            # broker_dealers.dba_names today
      live_finra=[...]        # FINRA search endpoint firm_other_names, parsed
      form_bd=[...]           # Form BD PDF "Other Names of this Firm" subsection

Decision rule:
- If db_dba >= form_bd for all 6 multi-name CRDs (Wells Fargo, Morgan Stanley,
  Stifel, HSBC, Barclays, RBC) -> FINRA endpoint already covers Form BD
  Item 1.B; build only a regression test (Path A).
- Otherwise (form_bd \\ db_dba is non-empty for any CRD) -> Form BD adds
  unique data; build the PDF parser and merge into dba_names (Path B).

PDFs are read from local fixtures only; no PDF re-fetch. The live FINRA
call surfaces the search endpoint's firm_other_names exactly as the
production ingestion sees it.

Usage (from repo root):
    python -m scripts.diag_form_bd_other_names_probe
"""

from __future__ import annotations

import asyncio
import re
import selectors
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import httpx  # noqa: E402
import pdfplumber  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.broker_dealer import BrokerDealer  # noqa: E402
from app.services.finra import (  # noqa: E402
    BROKERCHECK_HEADERS,
    FinraService,
)


# Probe set. 6 multi-name + 5 known-empty controls. PDF paths are local —
# fixtures/ for Schwab (the canonical fixture), tmp/other_types_diag/ for
# the remaining 10 from a recent diag run.
PROBE_CRDS: list[tuple[str, Path]] = [
    ("19616", ROOT / "tmp" / "other_types_diag" / "firm_19616.pdf"),
    ("149777", ROOT / "tmp" / "other_types_diag" / "firm_149777.pdf"),
    ("793", ROOT / "tmp" / "other_types_diag" / "firm_793.pdf"),
    ("19585", ROOT / "tmp" / "other_types_diag" / "firm_19585.pdf"),
    ("19714", ROOT / "tmp" / "other_types_diag" / "firm_19714.pdf"),
    ("31194", ROOT / "tmp" / "other_types_diag" / "firm_31194.pdf"),
    ("5393", ROOT / "brokercheck_extractor" / "fixtures" / "firm_5393_schwab.pdf"),
    ("8174", ROOT / "tmp" / "other_types_diag" / "firm_8174.pdf"),
    ("79", ROOT / "tmp" / "other_types_diag" / "firm_79.pdf"),
    ("705", ROOT / "tmp" / "other_types_diag" / "firm_705.pdf"),
    ("7059", ROOT / "tmp" / "other_types_diag" / "firm_7059.pdf"),
]

# Lines to discard inside the Other Names subsection.
_STATE_LIST_LINE = re.compile(
    r"^([A-Z]{2}(?:\s*[,/]\s*[A-Z]{2})*)\s*$"
)
_BRAND_NAME_LINE = re.compile(
    r"^[A-Z][A-Z0-9 ,.\-&*()/']{1,80}$"
)
# Section terminator after "Other Names of this Firm". The subsection is
# followed by Firm Profile / Direct Owners / Branches depending on FINRA's
# layout for the firm.
_SUBSECTION_TERMINATORS = (
    "Firm Profile",
    "Direct Owners and Executive Officers",
    "Branches",
    "Firm History",
    "Registrations",
    "Firm Operations",
)


def _extract_form_bd_other_names(pdf_path: Path) -> list[str]:
    """Lift Other Names from the Form BD PDF.

    Walks the cover-page section "Firm Names and Locations" looking for the
    "Other Names of this Firm" subheader. When present, collects brand-name
    lines (uppercase, optional asterisks for E*TRADE) and discards the
    state-list column.

    Returns ``[]`` for firms that didn't file alternates (subheader omitted)
    or when the PDF can't be read.
    """
    if not pdf_path.exists():
        return []
    pages: list[str] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages[:6]:
                pages.append(page.extract_text() or "")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! pdfplumber failed on {pdf_path.name}: {exc}", file=sys.stderr)
        return []
    full = "\n".join(pages)

    # Locate the Other Names subheader. Handle two PDF kerning shapes:
    # spaced ("Other Names of this Firm") and tight ("OtherNamesofthisFirm").
    spaced_idx = full.find("Other Names of this Firm")
    tight_idx = full.find("OtherNamesofthisFirm")
    idx = spaced_idx if spaced_idx >= 0 else tight_idx
    if idx < 0:
        return []
    body = full[idx:]

    end_candidates = [
        body.find(term) for term in _SUBSECTION_TERMINATORS
    ]
    end_candidates = [c for c in end_candidates if c > 50]  # skip self-hit
    if end_candidates:
        body = body[: min(end_candidates)]

    names: list[str] = []
    seen: set[str] = set()
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Skip the subsection / column headers themselves.
        if line in ("Other Names of this Firm", "OtherNamesofthisFirm"):
            continue
        if line in ("Name", "Where is it used", "Whereisitused"):
            continue
        # Page footer / header survivors.
        if "User Guidance" in line or "www.finra.org" in line:
            continue
        if line.startswith("©") or "FINRA. All rights reserved" in line:
            continue
        # Pure state-list lines: "AK, AL, AR, AZ, CA," or "AK, AL, AR".
        if re.match(r"^[A-Z]{2}(\s*,\s*[A-Z]{2})*\s*,?\s*$", line):
            continue
        # Drop the trailing state-list suffix that pdfplumber concatenates
        # onto the brand-name row when a firm has multi-state coverage.
        # Shape: "<BRAND NAME> XX, XX, XX, XX, XX,"  (trailing comma optional).
        # The suffix has at minimum 2 state codes — guard against trimming
        # legitimate trailing 2-letter words from a firm name.
        line = re.sub(
            r"\s+[A-Z]{2}(?:\s*,\s*[A-Z]{2})+\s*,?\s*$",
            "",
            line,
        ).strip()
        # If the line is a single brand + single state code (rare — Stifel's
        # "EATON PARTNERS CT" or Barclays' "BARCLAYS SECURITIES INC. CA"),
        # strip the trailing 2-letter word IFF it's a known US state code.
        # Otherwise leave it (could be e.g. "BARCLAYS CAPITAL INC." where INC
        # isn't a state).
        _US_STATES = (
            "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL",
            "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
            "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
            "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "PR",
            "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "VI", "WA",
            "WV", "WI", "WY",
        )
        m = re.match(r"^(.+?)\s+([A-Z]{2})\s*$", line)
        if m and m.group(2) in _US_STATES:
            line = m.group(1).strip()
        # Drop trailing standalone digits (page numbers that survived split).
        line = re.sub(r"\s+\d+\s*$", "", line)
        if not _BRAND_NAME_LINE.match(line):
            continue
        norm = " ".join(line.lower().split())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        names.append(line)
    return names


async def _read_db(crd: str) -> dict[str, object] | None:
    """Read the broker_dealers row for a CRD. Returns None if DB is not
    reachable (no local DATABASE_URL in this dev box) — the FINRA-live vs.
    Form-BD-PDF comparison alone gates the Path A vs. Path B decision,
    so a missing DB only loses the "what's persisted today" view, not the
    decision."""
    try:
        async with SessionLocal() as db:
            stmt = select(BrokerDealer).where(BrokerDealer.crd_number == crd)
            bd = (await db.execute(stmt)).scalar_one_or_none()
            if bd is None:
                return None
            return {
                "id": bd.id,
                "name": bd.name,
                "dba_names": list(bd.dba_names) if bd.dba_names else [],
                "resolver_aliases": list(bd.resolver_aliases) if bd.resolver_aliases else [],
            }
    except Exception as exc:  # noqa: BLE001
        # Surface the error type once at top of run via _DB_ERROR sentinel.
        global _DB_ERROR
        if _DB_ERROR is None:
            _DB_ERROR = f"{type(exc).__name__}: {exc}"
        return None


_DB_ERROR: str | None = None


async def _live_finra_other_names(
    client: httpx.AsyncClient,
    service: FinraService,
    crd: str,
) -> tuple[list[str], str]:
    """Hit the FINRA search endpoint, return (parsed dbas, raw_other_names).

    ``raw_other_names`` is the unparsed string FINRA returned, useful when
    the parsed list is empty (so we can tell "endpoint returned nothing"
    from "parser dropped everything").
    """
    try:
        hits, _ = await service._search(client, query=str(crd), start=0, rows=20)
    except Exception as exc:  # noqa: BLE001
        return [], f"<search_error: {type(exc).__name__}: {exc}>"

    for hit in hits:
        source = hit.get("_source") or hit.get("source")
        if not isinstance(source, dict):
            continue
        if str(source.get("firm_source_id") or "").strip() != str(crd).strip():
            continue
        raw = source.get("firm_other_names")
        legal = str(source.get("firm_name") or "").strip() or "<unknown>"
        parsed = service._parse_dba_names(raw, legal_name=legal) or []
        return parsed, repr(raw)

    return [], "<no_matching_hit>"


def _coverage_verdict(form_bd: list[str], db_dba: list[str]) -> str:
    """Return 'OK', 'GAP', or 'EMPTY' based on whether db_dba covers form_bd."""
    if not form_bd:
        return "N/A"
    fb_lower = {n.lower().strip() for n in form_bd}
    db_lower = {n.lower().strip() for n in db_dba}
    missing = fb_lower - db_lower
    if not missing:
        return "OK"
    if not db_dba:
        return "EMPTY"
    return "GAP"


async def main() -> None:
    print(f"diag_form_bd_other_names_probe: {len(PROBE_CRDS)} CRDs")
    print()

    # Verdict tuning. The decision is driven by FINRA-live vs Form-BD-PDF
    # comparison; DB read is informational only.
    finra_gap_count = 0
    finra_ok_count = 0

    service = FinraService()

    gap_count = 0
    multi_name_total = 0

    async with httpx.AsyncClient(
        timeout=settings.finra_request_timeout_seconds,
        follow_redirects=True,
        headers=BROKERCHECK_HEADERS,
    ) as client:
        for crd, pdf_path in PROBE_CRDS:
            db_row = await _read_db(crd)
            db_dba = list(db_row["dba_names"]) if db_row else []
            db_name = db_row["name"] if db_row else "<not in DB>"
            db_id = db_row["id"] if db_row else None
            db_resolver_aliases = (
                list(db_row["resolver_aliases"]) if db_row else []
            )

            live_parsed, live_raw = await _live_finra_other_names(
                client, service, crd
            )

            form_bd = _extract_form_bd_other_names(pdf_path)
            if form_bd:
                multi_name_total += 1

            db_verdict = _coverage_verdict(form_bd, db_dba)
            finra_verdict = _coverage_verdict(form_bd, live_parsed)
            if db_verdict in ("GAP", "EMPTY"):
                gap_count += 1
            if finra_verdict in ("GAP", "EMPTY"):
                finra_gap_count += 1
            elif finra_verdict == "OK":
                finra_ok_count += 1

            print(f"CRD={crd} bd_id={db_id} name={db_name!r}")
            print(f"  pdf={pdf_path.name} (exists={pdf_path.exists()})")
            print(f"  db_dba         = {db_dba}")
            print(f"  db_resolver    = {db_resolver_aliases}")
            print(f"  live_parsed    = {live_parsed}")
            print(f"  live_raw       = {live_raw}")
            print(f"  form_bd        = {form_bd}")
            print(f"  db_vs_form_bd  = {db_verdict}")
            print(f"  finra_vs_formbd = {finra_verdict}")
            print()

    print("=" * 60)
    if _DB_ERROR is not None:
        print(f"  (DB unreachable: {_DB_ERROR}; ignoring db_dba comparisons)")
    print(f"  multi-name CRDs (form_bd non-empty): {multi_name_total}")
    print(f"  CRDs with FINRA GAP/EMPTY coverage:  {finra_gap_count}")
    print(f"  CRDs with FINRA OK coverage:         {finra_ok_count}")
    if finra_gap_count == 0 and multi_name_total > 0:
        print("  --> Path A: regression test only (FINRA covers Form BD).")
    elif finra_gap_count > 0:
        print("  --> Path B: build PDF parser + merge into dba_names.")
    else:
        print("  --> Inconclusive (no multi-name firms had Form BD content).")
    print("=" * 60)


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 14):
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
