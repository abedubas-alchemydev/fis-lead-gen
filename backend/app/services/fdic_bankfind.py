"""FDIC BankFind Suite client — official, public, keyless.

Source of record for *opened* insured institutions. Two endpoints:

- ``GET https://api.fdic.gov/banks/institutions`` — institution directory.
  Filtered on ``ESTYMD:[A TO B]`` (establishment date window) to surface
  newly-chartered banks. Verified live 2026-07-02: response envelope is
  ``{"meta": {...}, "data": [{"data": {FIELD: value, ...}, "score": 0}],
  "totals": {"count": N}}``; requested fields are simply ABSENT from a
  row when null upstream (e.g. WEBADDR/ASSET/DEP on week-old banks), so
  every accessor below is absence-tolerant.
- ``GET https://api.fdic.gov/banks/history`` — structure-change events.
  ``CHANGECODE:110`` ("New Institution" — verified live 2026-07-02:
  Portrait Bank cert 59381's event is 110/"New Institution", and it is the
  ONLY 1xx code emitted in the trailing year; 510 is "Change in Legal
  Name", NOT new-institution) corroborates the institutions window: a
  brand-new cert occasionally lands in /history a day before the
  /institutions index rebuild picks it up.

Date formats differ per endpoint (verified): /institutions uses
``MM/DD/YYYY`` (``"06/22/2026"``), /history uses ISO-with-time
(``"2026-06-23T00:00:00"``). ``_parse_fdic_date`` accepts both.

No API key, no auth. FDIC asks for polite volumes; the watcher fetches a
few hundred rows a night at most. Style mirrors ``services/finra.py``:
module constants + an httpx.AsyncClient per public method.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

FDIC_INSTITUTIONS_URL = "https://api.fdic.gov/banks/institutions"
FDIC_HISTORY_URL = "https://api.fdic.gov/banks/history"

# FDIC structure-change code for a newly organized institution
# (CHANGECODE_DESC "New Institution"). NOT 510 — that is "Change in Legal
# Name (FO or BR)"; using it would corroborate ~50 unrelated old certs a
# half-year into the vertical. Verified live 2026-07-02.
FDIC_CHANGECODE_NEW_INSTITUTION = 110

# Institution fields the watcher persists. Verified working against the
# live API on 2026-07-02 (all names accepted by the ``fields`` param).
INSTITUTION_FIELDS = (
    "NAME,CERT,FED_RSSD,ESTYMD,INSDATE,CITY,STALP,ZIP,ADDRESS,"
    "CHRTAGNT,REGAGNT,BKCLASS,ACTIVE,WEBADDR,ASSET,DEP,OFFICES"
)
HISTORY_FIELDS = "CERT,INSTNAME,CHANGECODE,PROCDATE,EFFDATE,PCITY,PSTALP"

# The API caps ``limit`` at 10,000; we page well below that so a partial
# failure loses less and memory stays flat.
_PAGE_SIZE = 500
_TIMEOUT_SECONDS = 30.0


def _parse_fdic_date(value: object) -> date | None:
    """Parse the two date shapes FDIC emits (absence- and junk-tolerant).

    /institutions: ``"06/22/2026"``; /history: ``"2026-06-23T00:00:00"``.
    Returns None for missing / empty / unrecognised values instead of
    raising — one malformed row must never abort a watcher run.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    logger.warning("fdic: unparseable date %r", value)
    return None


def _opt_str(row: dict, key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_float(row: dict, key: str) -> float | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(row: dict, key: str) -> int | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass
class FdicInstitutionRecord:
    """One /institutions row, normalized. ``cert`` is the only required key."""

    cert: str
    name: str
    fed_rssd: str | None = None
    established_date: date | None = None
    insured_date: date | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    website: str | None = None
    charter_authority: str | None = None  # CHRTAGNT: 'OCC' | 'STATE'
    regulator: str | None = None  # REGAGNT
    bkclass: str | None = None
    active: bool | None = None
    asset: float | None = None  # $ thousands, as reported
    deposits: float | None = None  # $ thousands, as reported
    offices: int | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class FdicHistoryEvent:
    """One /history structure-change row (CHANGECODE 110 = New Institution)."""

    cert: str
    institution_name: str | None
    change_code: int | None
    process_date: date | None
    effective_date: date | None
    city: str | None = None
    state: str | None = None
    raw: dict = field(default_factory=dict)


def parse_institution_row(row: dict) -> FdicInstitutionRecord | None:
    """Normalize one ``data[].data`` object. Returns None (logged) when the
    row is missing CERT or NAME — nothing downstream can key on it."""
    cert = _opt_str(row, "CERT")
    name = _opt_str(row, "NAME")
    if not cert or not name:
        logger.warning("fdic: skipping institution row without CERT/NAME: %r", row)
        return None
    active_raw = row.get("ACTIVE")
    return FdicInstitutionRecord(
        cert=cert,
        name=name,
        fed_rssd=_opt_str(row, "FED_RSSD"),
        established_date=_parse_fdic_date(row.get("ESTYMD")),
        insured_date=_parse_fdic_date(row.get("INSDATE")),
        address=_opt_str(row, "ADDRESS"),
        city=_opt_str(row, "CITY"),
        state=_opt_str(row, "STALP"),
        zip=_opt_str(row, "ZIP"),
        website=_opt_str(row, "WEBADDR"),
        charter_authority=_opt_str(row, "CHRTAGNT"),
        regulator=_opt_str(row, "REGAGNT"),
        bkclass=_opt_str(row, "BKCLASS"),
        active=None if active_raw is None else bool(_opt_int(row, "ACTIVE")),
        asset=_opt_float(row, "ASSET"),
        deposits=_opt_float(row, "DEP"),
        offices=_opt_int(row, "OFFICES"),
        raw=dict(row),
    )


def parse_history_row(row: dict) -> FdicHistoryEvent | None:
    cert = _opt_str(row, "CERT")
    if not cert:
        logger.warning("fdic: skipping history row without CERT: %r", row)
        return None
    return FdicHistoryEvent(
        cert=cert,
        institution_name=_opt_str(row, "INSTNAME"),
        change_code=_opt_int(row, "CHANGECODE"),
        process_date=_parse_fdic_date(row.get("PROCDATE")),
        effective_date=_parse_fdic_date(row.get("EFFDATE")),
        city=_opt_str(row, "PCITY"),
        state=_opt_str(row, "PSTALP"),
        raw=dict(row),
    )


class FdicBankFindService:
    """Thin async client over the two BankFind endpoints the watcher needs."""

    def __init__(self, *, timeout_seconds: float = _TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds

    async def _fetch_all_pages(self, url: str, params: dict[str, str]) -> list[dict]:
        """Drain a filtered query via limit/offset pagination.

        Envelope tolerance: rows are read from ``data[].data`` with a
        fallback to the bare item, and iteration stops on the first short
        page OR when ``totals.count`` is reached, whichever comes first.
        """
        rows: list[dict] = []
        offset = 0
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            while True:
                page_params = {**params, "limit": str(_PAGE_SIZE), "offset": str(offset)}
                response = await client.get(url, params=page_params)
                response.raise_for_status()
                body = response.json()
                data = body.get("data") or []
                for item in data:
                    row = item.get("data") if isinstance(item, dict) and isinstance(item.get("data"), dict) else item
                    if isinstance(row, dict):
                        rows.append(row)
                total = ((body.get("totals") or {}).get("count")) or ((body.get("meta") or {}).get("total"))
                offset += len(data)
                if len(data) < _PAGE_SIZE:
                    break
                if isinstance(total, int) and offset >= total:
                    break
        return rows

    async def fetch_institutions_established_between(
        self, start: date, end: date
    ) -> list[FdicInstitutionRecord]:
        """All institutions with ESTYMD in [start, end] (inclusive)."""
        params = {
            "filters": f"ESTYMD:[{start.isoformat()} TO {end.isoformat()}]",
            "fields": INSTITUTION_FIELDS,
            "sort_by": "ESTYMD",
            "sort_order": "DESC",
        }
        raw_rows = await self._fetch_all_pages(FDIC_INSTITUTIONS_URL, params)
        records = [rec for row in raw_rows if (rec := parse_institution_row(row)) is not None]
        logger.info(
            "fdic: institutions ESTYMD %s..%s -> %d row(s), %d parsed",
            start, end, len(raw_rows), len(records),
        )
        return records

    async def fetch_all_active_institutions(self) -> list[FdicInstitutionRecord]:
        """Every ACTIVE FDIC-insured institution — the full base dataset.

        Drains ``/institutions`` filtered on ``ACTIVE:1`` through the shared
        limit/offset pagination (like ``fetch_institutions_established_between``),
        with a stable ``CERT`` ascending sort so the page boundaries are
        deterministic and a partial drain resumes on the same rows. Same
        keyless request and absence-tolerant parsing as the window fetch.

        ~4,300 rows today (~9 pages at ``_PAGE_SIZE`` 500). The
        new/pending/digital-asset views are filtered subsets of this set,
        so callers upsert the whole list and let ``list_banks`` narrow it.
        """
        params = {
            "filters": "ACTIVE:1",
            "fields": INSTITUTION_FIELDS,
            # Stable ordering so limit/offset paging can't skip or repeat a
            # row between page requests (ESTYMD is not unique; CERT is).
            "sort_by": "CERT",
            "sort_order": "ASC",
        }
        raw_rows = await self._fetch_all_pages(FDIC_INSTITUTIONS_URL, params)
        records = [rec for row in raw_rows if (rec := parse_institution_row(row)) is not None]
        logger.info(
            "fdic: all active institutions -> %d row(s), %d parsed",
            len(raw_rows), len(records),
        )
        return records

    async def fetch_institutions_by_certs(self, certs: list[str]) -> list[FdicInstitutionRecord]:
        """Targeted lookup for specific CERT numbers (reconciliation helper)."""
        clean = [c.strip() for c in certs if c and c.strip()]
        if not clean:
            return []
        params = {
            "filters": f"CERT:({' OR '.join(clean)})",
            "fields": INSTITUTION_FIELDS,
        }
        raw_rows = await self._fetch_all_pages(FDIC_INSTITUTIONS_URL, params)
        return [rec for row in raw_rows if (rec := parse_institution_row(row)) is not None]

    async def fetch_new_institution_history(
        self, start: date, end: date
    ) -> list[FdicHistoryEvent]:
        """CHANGECODE:110 events processed in [start, end] — corroboration
        stream for the institutions window (see module docstring)."""
        params = {
            "filters": (
                f"CHANGECODE:{FDIC_CHANGECODE_NEW_INSTITUTION}"
                f" AND PROCDATE:[{start.isoformat()} TO {end.isoformat()}]"
            ),
            "fields": HISTORY_FIELDS,
            "sort_by": "PROCDATE",
            "sort_order": "DESC",
        }
        raw_rows = await self._fetch_all_pages(FDIC_HISTORY_URL, params)
        events = [ev for row in raw_rows if (ev := parse_history_row(row)) is not None]
        logger.info(
            "fdic: history CHANGECODE=%s %s..%s -> %d event(s)",
            FDIC_CHANGECODE_NEW_INSTITUTION, start, end, len(events),
        )
        return events


def bankfind_public_url(cert: str) -> str:
    """Official BankFind institution page for the detail-view source links."""
    return f"https://banks.data.fdic.gov/bankfind-suite/bankfind/institution/{cert}"
