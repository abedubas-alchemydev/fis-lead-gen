"""OCC Corporate Applications Search (CAS) client — official, public, keyless.

Source of record for national-bank / federal-trust *charter applications*
(the pending pipeline the FDIC can't see: an applicant has no FDIC cert
until it opens).

Endpoint (verified live 2026-07-02):

    GET https://apps.occ.gov/CAS/api/search?fromDte=MM/DD/YYYY&toDte=MM/DD/YYYY&filingTypes=2

``filingTypes=2`` = "New Bank Charter". Response envelope::

    {"items": [{"ftId": "2", "fId": "344925", "fstId": "1093",
                "act": "Approved", "actDte": "2026-06-18",
                "ft": "New Bank Charter", "cn": "2026-Charter-344925",
                "bn": "Morgan Stanley Digital TR, NA",
                "addr": "...", "cty": "...", "st": "NY", "zip": "10577",
                "cnty": "", "cped": "2026-03-20"}, ...],
     "error": ""}

Observed ``act`` vocabulary: Receipt / Approved / Consummated-Effective /
Withdrawn (plus rescission variants). CAS is *official but undocumented*,
so parsing is schema-tolerant end-to-end: unknown fields ride along in
``raw``, unknown actions are stored verbatim and map to no status change,
and a malformed item is logged and skipped, never raised.

The date window is on the ACTION date, so one filing (one ``cn``) appears
in multiple windows as it accretes actions — exactly what the watcher
wants for status transitions. Requests MUST carry the query params: the
bare endpoint 302-redirects to the search UI (verified).

Also here: the two ACTIVE-institutions directory sources used to reconcile
an opened application to its FDIC row.

PRIMARY — the OCC's official, documented **Institutions API**
(``api.occ.gov``; the docs SPA at the api.occ.gov portal is JS-rendered,
its content lives in ``assets/data/institution.json``). Verified live
2026-07-02::

    GET https://api.occ.gov/institutions/active   (X-Api-Key header)

990 records with CharterNumber / BankName / BankCity / BankStateCode /
BankAddress / ZipCode / InstNationalTrustCompanyInd / CharterType (e.g.
"TrustCo-National") / CharterDate (ISO) / FDICCertificateNumber /
FDICInsuranceStatus / FRBRSSDNumber / LegalEntityIdentifier / RCON.
Auth is an api.data.gov key (env ``OCC_API_KEY``), rate limit ~1000/hr —
the watcher makes one call per run. Missing key / HTTP failure / schema
surprise all log a warning and return None so callers can fall back.

FALLBACK — the OCC "Financial Institution Lists" national-banks workbook
(``national-by-name.xlsx``), keyless. Verified live 2026-07-02: header
row 4 = CHARTER NO | NAME | ADDRESS (LOC) | CITY | STATE | CERT | RSSD —
it carries the FDIC CERT directly, so charter-number reconciliation is a
lookup, not a fuzzy match, in both sources.

And the OCC "Digital Assets Licensing Applications" page (client
addition): a single HTML table (verified live 2026-07-02 at
``index-digital-assets-licensing-applications.html`` — the bare
``.../digital-assets-licensing-applications/`` URL 404s) with columns
"Date received | Applicant | Application", where Application is either a
relative link to the public-portion application PDF or "N/A"
(conversions aren't subject to public comment, so they have no PDF).
Parsed with the stdlib ``html.parser`` — no new dependency. Only the PDF
*URLs* are stored; the PDFs are never fetched or rendered here (the
existing ``services/_pdf_render_worker.py`` subprocess pattern stands by
if a future feature needs their contents — every artifact this vertical
consumes today is JSON, XLSX, or plain HTML).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

OCC_CAS_SEARCH_URL = "https://apps.occ.gov/CAS/api/search"
OCC_CAS_DETAILS_URL = "https://apps.occ.gov/CAS/home/details"
# OCC Institutions API (official + documented) — the PRIMARY directory of
# active national banks / federal trust companies. One GET per watcher run.
# Auth: api.data.gov key via the X-Api-Key header (?api_key= also works,
# but the header keeps the key out of URLs and logged exception messages).
OCC_INSTITUTIONS_API_URL = "https://api.occ.gov/institutions/active"
OCC_API_KEY_ENV = "OCC_API_KEY"
# OCC "Financial Institution Lists" — active national banks with charter
# number, FDIC CERT, and RSSD. ~80 KB workbook, refreshed monthly. The
# keyless FALLBACK directory when the Institutions API is unavailable.
OCC_NATIONAL_BANKS_XLSX_URL = (
    "https://www.occ.gov/topics/charters-and-licensing/"
    "financial-institution-lists/national-by-name.xlsx"
)
# OCC "Digital Assets Licensing Applications" — novel / de-novo
# digital-asset national bank charters and conversions, with links to the
# public-portion application PDFs. NOTE: the directory URL without the
# index filename 404s (verified live 2026-07-02).
OCC_DIGITAL_ASSETS_URL = (
    "https://www.occ.gov/topics/charters-and-licensing/"
    "digital-assets-licensing-applications/"
    "index-digital-assets-licensing-applications.html"
)
_OCC_BASE_URL = "https://www.occ.gov"

# Wayback Machine (public Internet Archive) — one-off history backfill of
# the digital-assets page (it prunes decided applications, so its PAST
# tenants exist only in archived captures of OCC's own page). CDX is the
# archive's capture index; the ``id_`` ("identity") URL flag serves the
# ORIGINAL captured bytes — no archive chrome, no link rewriting — so
# ``parse_digital_assets_page`` works unchanged on a snapshot.
WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
_WAYBACK_SNAPSHOT_URL = "https://web.archive.org/web/{timestamp}id_/{original}"
# ~one capture per month: collapse on the first 6 timestamp digits (YYYYMM).
_WAYBACK_CDX_PARAMS = {
    "output": "json",
    "fl": "timestamp,statuscode",
    "filter": "statuscode:200",
    "collapse": "timestamp:6",
}
# Polite crawl delay between snapshot fetches.
_WAYBACK_DELAY_SECONDS = 1.0
# The archive's latency is spiky (a CDX call was observed to exceed 30s and
# then answer in 6s on retry) — give the one-off backfill a generous
# timeout instead of the service's 30s default.
_WAYBACK_TIMEOUT_SECONDS = 120.0

# CAS FilingTypeID for "New Bank Charter".
OCC_FILING_TYPE_NEW_BANK_CHARTER = "2"

_TIMEOUT_SECONDS = 30.0

# CAS action -> banks.charter_status. Substring-matched case-insensitively
# so vocabulary drift ("Approved with Conditions", "Consummated-Effective")
# still lands. Order matters: rescinded/withdrawn before the bare
# "approv" so "Approval Rescinded" resolves to rescinded.
_ACTION_STATUS_RULES: tuple[tuple[str, str], ...] = (
    ("rescind", "rescinded"),
    ("withdraw", "withdrawn"),
    ("consummat", "opened"),
    ("effective", "opened"),
    ("open", "opened"),
    ("approv", "approved"),
    ("receipt", "pending"),
    ("received", "pending"),
)

# Lifecycle rank so a stale re-run can't demote a bank's status (e.g. a
# window that re-sees "Receipt" after "Approved" already landed).
CHARTER_STATUS_RANK: dict[str, int] = {
    "pending": 0,
    "approved": 1,
    "withdrawn": 2,
    "rescinded": 2,
    "opened": 3,
}


def charter_status_for_action(action: str | None) -> str | None:
    """Map a CAS action string to a charter_status, or None when unknown.

    Unknown actions deliberately map to None (event recorded, status
    untouched) — CAS is undocumented and new vocabulary must not corrupt
    the lifecycle.
    """
    if not action:
        return None
    lowered = action.strip().lower()
    for needle, status in _ACTION_STATUS_RULES:
        if needle in lowered:
            return status
    return None


def _parse_cas_date(value: object) -> date | None:
    """CAS emits ISO dates (``2026-06-18``); tolerate US format too."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    logger.warning("occ_cas: unparseable date %r", value)
    return None


# Placeholder values CAS emits for not-yet-known address fields (all
# observed live: 'TBD', 'To be confirmed' addresses, 'XXXXX' zips). Treated
# as absent so placeholder junk never lands in the DB.
_CAS_PLACEHOLDERS = frozenset(
    {"tbd", "tbd00", "n/a", "to be confirmed", "to be determined", "xxxxx", "unknown"}
)


def _opt_str(item: dict, key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in _CAS_PLACEHOLDERS:
        return None
    return text


@dataclass
class OccCharterFiling:
    """One CAS item = one ACTION on a New Bank Charter filing."""

    control_number: str  # cn, e.g. '2026-Charter-344521' — the stable filing key
    bank_name: str  # bn
    action: str  # act, verbatim
    action_date: date | None  # actDte
    filing_type: str | None = None  # ft ("New Bank Charter")
    filing_type_id: str | None = None  # ftId
    filing_id: str | None = None  # fId
    filing_subtype_id: str | None = None  # fstId
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    comment_period_end_date: date | None = None  # cped
    raw: dict = field(default_factory=dict)

    @property
    def details_url(self) -> str | None:
        """Official CAS details page (verified URL shape, returns 200)."""
        if not (self.filing_type_id and self.filing_id and self.filing_subtype_id):
            return None
        return (
            f"{OCC_CAS_DETAILS_URL}?FilingTypeID={self.filing_type_id}"
            f"&FilingID={self.filing_id}&FilingSubtypeID={self.filing_subtype_id}"
        )

    @property
    def charter_status(self) -> str | None:
        return charter_status_for_action(self.action)


def parse_cas_item(item: dict) -> OccCharterFiling | None:
    """Normalize one CAS item; None (logged) when the keys we upsert on are
    missing. Everything else is optional by design."""
    control_number = _opt_str(item, "cn")
    bank_name = _opt_str(item, "bn")
    action = _opt_str(item, "act")
    if not control_number or not bank_name or not action:
        logger.warning("occ_cas: skipping item missing cn/bn/act: %r", item)
        return None
    return OccCharterFiling(
        control_number=control_number,
        bank_name=bank_name,
        action=action,
        action_date=_parse_cas_date(item.get("actDte")),
        filing_type=_opt_str(item, "ft"),
        filing_type_id=_opt_str(item, "ftId"),
        filing_id=_opt_str(item, "fId"),
        filing_subtype_id=_opt_str(item, "fstId"),
        address=_opt_str(item, "addr"),
        city=_opt_str(item, "cty"),
        state=_opt_str(item, "st"),
        zip=_opt_str(item, "zip"),
        comment_period_end_date=_parse_cas_date(item.get("cped")),
        raw=dict(item),
    )


@dataclass
class OccNationalBankDirectoryRow:
    """One active-institutions directory row (charter no ↔ CERT ↔ RSSD).

    Produced by BOTH directory sources — the official Institutions API
    (``api.occ.gov``, primary) and the ``national-by-name.xlsx`` workbook
    (fallback) — so the watcher's reconcile phase is source-agnostic:
    identical unique-match semantics either way. ``lei`` / ``charter_type``
    are API-only enrichment; XLSX rows leave them None.
    """

    charter_number: str
    name: str
    city: str | None = None
    state: str | None = None
    fdic_cert: str | None = None
    fed_rssd: str | None = None
    # ISO 17442 Legal Entity Identifier (Institutions API only).
    lei: str | None = None
    # OCC CharterType verbatim, e.g. 'National' / 'TrustCo-National'
    # (Institutions API only). Descriptive — deliberately NOT a
    # digital-assets signal: plenty of TrustCo-National institutions
    # (e.g. Citicorp Trust Delaware) are not digital-asset businesses.
    charter_type: str | None = None


def _identifier_str(value: object) -> str | None:
    """Normalize a numeric-ish identifier (cert / RSSD / charter number).

    The Institutions API emits these as JSON numbers; the XLSX cell reader
    already handles its own float-formatting. ``0`` / ``"0"`` means "no
    such identifier" (uninsured trust companies carry cert 0), and MUST
    map to None — stamping a literal '0' cert onto two banks would trip
    the unique index and corrupt reconciliation.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text or text == "0" or text.lower() in _CAS_PLACEHOLDERS:
        return None
    return text


def parse_active_institution(item: dict) -> OccNationalBankDirectoryRow | None:
    """Normalize one Institutions-API record; None (logged) when the keys
    reconciliation matches on are missing. Schema-tolerant like the CAS
    parser: everything beyond CharterNumber/BankName is optional."""
    charter_number = _identifier_str(item.get("CharterNumber"))
    name = _opt_str(item, "BankName")
    if not charter_number or not name:
        logger.warning(
            "occ_cas: skipping institutions-API item missing CharterNumber/BankName: %r",
            item,
        )
        return None
    return OccNationalBankDirectoryRow(
        charter_number=charter_number,
        name=name,
        city=_opt_str(item, "BankCity"),
        state=_opt_str(item, "BankStateCode"),
        fdic_cert=_identifier_str(item.get("FDICCertificateNumber")),
        fed_rssd=_identifier_str(item.get("FRBRSSDNumber")),
        lei=_opt_str(item, "LegalEntityIdentifier"),
        charter_type=_opt_str(item, "CharterType"),
    )


def _institutions_api_items(body: object) -> list | None:
    """Locate the record list in the Institutions-API response body.

    Verified live as a bare JSON array; tolerate a wrapped envelope too
    (the API is documented but young). None = unrecognizable shape."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("institutions", "items", "data", "results"):
            value = body.get(key)
            if isinstance(value, list):
                return value
        lists = [value for value in body.values() if isinstance(value, list)]
        if len(lists) == 1:
            return lists[0]
    return None


def normalize_bank_name(name: str) -> str:
    """Collapse a bank name to a comparison token for OCC↔FDIC matching.

    Lowercase, strip punctuation, expand the abbreviations the two sources
    disagree on (OCC CAS says 'TR'/'NB', FDIC/directory spell them out),
    drop corporate-suffix noise. Conservative on purpose: reconciliation
    only auto-links on an exact normalized match (+ state, in the fallback
    path), never on similarity scores.
    """
    lowered = name.lower()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    tokens = lowered.split()
    # 'TR' -> trust, 'NB' -> bank ('national' itself is dropped below, so
    # OCC's 'Augustus NB, NA' and the directory's 'Augustus National Bank,
    # National Association' both collapse to 'augustus bank').
    expansions = {"tr": "trust", "nb": "bank"}
    dropped = {
        "na", "n", "a", "the", "inc", "llc", "co", "company",
        "association", "assn", "national", "natl",
    }
    out: list[str] = []
    for token in tokens:
        token = expansions.get(token, token)
        if token in dropped:
            continue
        out.append(token)
    return " ".join(out)


class OccCasService:
    """Async client for CAS search + the national-banks directory workbook."""

    def __init__(self, *, timeout_seconds: float = _TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds

    async def fetch_new_charter_filings(
        self, start: date, end: date
    ) -> list[OccCharterFiling]:
        """All New Bank Charter ACTIONS with actDte in [start, end]."""
        params = {
            "fromDte": start.strftime("%m/%d/%Y"),
            "toDte": end.strftime("%m/%d/%Y"),
            "filingTypes": OCC_FILING_TYPE_NEW_BANK_CHARTER,
        }
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(
                OCC_CAS_SEARCH_URL, params=params, headers={"Accept": "application/json"}
            )
            response.raise_for_status()
            body = response.json()
        error = (body.get("error") or "").strip() if isinstance(body, dict) else ""
        if error:
            # Official-but-undocumented: surface upstream complaints loudly
            # but keep whatever items did come back.
            logger.warning("occ_cas: search returned error=%r", error)
        items = body.get("items") if isinstance(body, dict) else None
        filings = [
            filing
            for item in (items or [])
            if isinstance(item, dict) and (filing := parse_cas_item(item)) is not None
        ]
        logger.info(
            "occ_cas: %s..%s filingTypes=%s -> %d item(s), %d parsed",
            params["fromDte"], params["toDte"], params["filingTypes"],
            len(items or []), len(filings),
        )
        return filings

    async def fetch_active_institutions(self) -> list[OccNationalBankDirectoryRow] | None:
        """Fetch the OCC Institutions API's active-institutions directory.

        The PRIMARY reconcile source (official + documented; the XLSX
        workbook below is the keyless fallback). One GET per run against
        ``OCC_INSTITUTIONS_API_URL`` with the api.data.gov key from the
        ``OCC_API_KEY`` env var in the ``X-Api-Key`` header.

        NEVER raises — returns None (with a logged warning) on a missing
        key, an HTTP/network failure, a non-JSON body, an unrecognizable
        response shape, or zero parseable records, so the caller can fall
        back to ``fetch_national_bank_directory``. Same schema-tolerance
        philosophy as the CAS parsing: a malformed record is logged and
        skipped, never raised.
        """
        api_key = (os.environ.get(OCC_API_KEY_ENV) or "").strip()
        if not api_key:
            logger.warning(
                "occ_cas: %s unset; skipping the Institutions API (XLSX fallback)",
                OCC_API_KEY_ENV,
            )
            return None
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            ) as client:
                response = await client.get(
                    OCC_INSTITUTIONS_API_URL,
                    headers={"X-Api-Key": api_key, "Accept": "application/json"},
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # str(exc) carries the URL at most — the key rides in a header,
            # so nothing secret can leak into this log line.
            logger.warning("occ_cas: Institutions API failed (%s); XLSX fallback", exc)
            return None
        items = _institutions_api_items(body)
        if items is None:
            logger.warning(
                "occ_cas: Institutions API body shape unrecognized (%s); XLSX fallback",
                type(body).__name__,
            )
            return None
        rows = [
            row
            for item in items
            if isinstance(item, dict)
            and (row := parse_active_institution(item)) is not None
        ]
        if not rows:
            # A directory that parses to 0 rows must not silently reconcile
            # nothing under reconcile_source=api — let the XLSX path try.
            logger.warning(
                "occ_cas: Institutions API -> 0 parseable row(s) of %d item(s); XLSX fallback",
                len(items),
            )
            return None
        logger.info("occ_cas: Institutions API -> %d active institution row(s)", len(rows))
        return rows

    async def fetch_national_bank_directory(self) -> list[OccNationalBankDirectoryRow]:
        """Download + parse national-by-name.xlsx (charter no ↔ CERT ↔ RSSD).

        openpyxl (already a backend dependency) reads the workbook from
        memory. The header row is located by content ("CHARTER NO"), not by
        position, so a cosmetic re-layout upstream doesn't break parsing.

        XML hardening: this workbook is untrusted remote content, and
        openpyxl's read-only sheet reader parses its XML with stdlib
        ``iterparse``. ``defusedxml`` is pinned in requirements.txt exactly
        for this — openpyxl auto-detects it at import and switches to the
        entity-expansion-safe parsers (openpyxl's own recommended
        mitigation for billion-laughs / quadratic-blowup input).
        """
        import openpyxl  # deferred: costs ~100ms and only the watcher needs it

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(OCC_NATIONAL_BANKS_XLSX_URL)
            response.raise_for_status()
            content = response.content

        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        sheet = workbook.active
        rows: list[OccNationalBankDirectoryRow] = []
        header_index: dict[str, int] | None = None
        for values in sheet.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in values]
            if header_index is None:
                upper = [c.upper() for c in cells]
                if "CHARTER NO" in upper and "NAME" in upper:
                    header_index = {label: i for i, label in enumerate(upper)}
                continue
            def cell(label: str) -> str | None:
                idx = header_index.get(label)
                if idx is None or idx >= len(cells):
                    return None
                value = cells[idx]
                # openpyxl renders numeric cells like 8709 as '8709' via the
                # str() above but floats can arrive as '8709.0'; trim that.
                if value.endswith(".0"):
                    value = value[:-2]
                return value or None
            charter = cell("CHARTER NO")
            name = cell("NAME")
            if not charter or not name:
                continue
            rows.append(
                OccNationalBankDirectoryRow(
                    charter_number=charter,
                    name=name,
                    city=cell("CITY"),
                    state=cell("STATE"),
                    fdic_cert=cell("CERT"),
                    fed_rssd=cell("RSSD"),
                )
            )
        workbook.close()
        if header_index is None:
            logger.warning("occ_cas: national-by-name.xlsx header row not found; 0 rows parsed")
        else:
            logger.info("occ_cas: national-by-name.xlsx -> %d directory row(s)", len(rows))
        return rows

    async def fetch_digital_asset_applications(self) -> list[OccDigitalAssetApplication]:
        """Download + parse the Digital Assets Licensing Applications page."""
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(
                OCC_DIGITAL_ASSETS_URL, headers={"Accept": "text/html"}
            )
            response.raise_for_status()
            html = response.text
        applications = parse_digital_assets_page(html)
        logger.info(
            "occ_cas: digital-assets page -> %d application row(s), %d with PDF",
            len(applications),
            sum(1 for a in applications if a.pdf_url),
        )
        return applications

    async def fetch_digital_asset_application_history(
        self, *, delay_seconds: float = _WAYBACK_DELAY_SECONDS
    ) -> tuple[int, list[OccDigitalAssetHistoryEntry]]:
        """Union the Wayback Machine's monthly captures of the digital-assets page.

        One-off backfill source: the live page lists only CURRENT
        applications, so an applicant that rolled off before our first
        scrape exists only in the public Internet Archive's captures of
        OCC's own page. The CDX index yields ~one 200-OK capture per month
        (``collapse=timestamp:6``); each is fetched via the raw ``id_``
        URL (original page bytes, no archive chrome, no link rewriting)
        with a polite delay, parsed by the UNCHANGED live-page parser
        (after ``strip_wayback_rewrites`` undoes any stray archive href
        rewriting so the occ.gov allowlist judges original URLs), then
        unioned oldest→newest. A failed capture logs a warning and is
        skipped; a CDX index failure also degrades — warning + empty
        history — so a composed run still completes and a rerun converges
        (the archive was observed dropping connections mid-run in
        production, 2026-07-02). Returns ``(snapshots_parsed, entries)``.
        """
        params = {
            # The CDX capture key is scheme-/www-less; query with the same
            # canonical form (verified to return the monthly 200s).
            "url": re.sub(r"^https?://(?:www\.)?", "", OCC_DIGITAL_ASSETS_URL),
            **_WAYBACK_CDX_PARAMS,
        }
        parsed_snapshots: list[tuple[str, list[OccDigitalAssetApplication]]] = []
        timeout = max(self._timeout, _WAYBACK_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            try:
                response = await client.get(
                    WAYBACK_CDX_URL, params=params, headers={"Accept": "application/json"}
                )
                response.raise_for_status()
                timestamps = parse_cdx_timestamps(response.json())
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "occ_cas: wayback CDX index fetch failed (%s); skipping "
                    "history backfill this run — rerun to converge", exc,
                )
                return 0, []
            logger.info("occ_cas: wayback CDX -> %d monthly capture(s)", len(timestamps))
            for index, timestamp in enumerate(timestamps):
                if index and delay_seconds > 0:
                    # Polite crawl delay — the archive is a shared public
                    # service and the whole history is a few dozen fetches.
                    await asyncio.sleep(delay_seconds)
                snapshot_url = _WAYBACK_SNAPSHOT_URL.format(
                    timestamp=timestamp, original=OCC_DIGITAL_ASSETS_URL
                )
                try:
                    snapshot = await client.get(
                        snapshot_url, headers={"Accept": "text/html"}
                    )
                    snapshot.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning(
                        "occ_cas: wayback snapshot %s failed (%s); continuing",
                        timestamp, exc,
                    )
                    continue
                applications = parse_digital_assets_page(
                    strip_wayback_rewrites(snapshot.text)
                )
                logger.info(
                    "occ_cas: wayback snapshot %s -> %d row(s), %d with PDF",
                    timestamp, len(applications),
                    sum(1 for a in applications if a.pdf_url),
                )
                parsed_snapshots.append((timestamp, applications))
        entries = union_digital_asset_history(parsed_snapshots)
        logger.info(
            "occ_cas: wayback history -> %d snapshot(s) parsed, %d unique applicant row(s)",
            len(parsed_snapshots), len(entries),
        )
        return len(parsed_snapshots), entries


# ── OCC "Digital Assets Licensing Applications" page (client addition) ──


@dataclass
class OccDigitalAssetApplication:
    """One row of the digital-assets applications table.

    ``applicant`` is plain text (the page currently links neither the CAS
    record nor the applicant); ``pdf_url`` is the ABSOLUTE public-portion
    application PDF link, or None for "N/A" rows (conversions aren't
    subject to public comment, so they publish no PDF).
    """

    applicant: str
    received_date: date | None = None
    pdf_url: str | None = None


class _DigitalAssetsTableParser(HTMLParser):
    """Extract (cells, hrefs) per <tr> from the page's first data table.

    Deliberately structural, not positional: any <table> whose rows carry
    exactly three <td> cells matching "date | text | pdf-or-N/A" yields
    rows, so cosmetic re-layouts upstream (extra wrappers, attribute
    churn) don't break parsing. stdlib-only — no bs4/lxml dependency.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_td = False
        self._current_cell: list[str] = []
        self._current_href: str | None = None
        self._row_cells: list[tuple[str, str | None]] = []
        self.rows: list[list[tuple[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row_cells = []
        elif tag == "td":
            self._in_td = True
            self._current_cell = []
            self._current_href = None
        elif tag == "a" and self._in_td:
            for name, value in attrs:
                if name == "href" and value:
                    self._current_href = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_td:
            self._in_td = False
            text = re.sub(r"\s+", " ", "".join(self._current_cell)).strip()
            self._row_cells.append((text, self._current_href))
        elif tag == "tr" and self._row_cells:
            self.rows.append(self._row_cells)
            self._row_cells = []

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._current_cell.append(data)


_DA_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


def _is_allowed_occ_url(url: str) -> bool:
    """True only for https URLs whose host is occ.gov or *.occ.gov.

    Host allowlist (security review): the digital-assets PDF links are
    persisted and later rendered as click-through links, so a vandalized
    page / parser drift must never let an off-domain or plain-http URL
    into the DB. Never raises — a malformed URL is simply not allowed.
    """
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:  # e.g. malformed IPv6 authority
        return False
    return parts.scheme == "https" and (host == "occ.gov" or host.endswith(".occ.gov"))


def parse_digital_assets_page(html: str) -> list[OccDigitalAssetApplication]:
    """Parse the digital-assets page into application rows.

    Schema-tolerant like the CAS parser: rows that don't look like
    "date | applicant | application" are skipped (header rows, unrelated
    tables), a bad date parses to None rather than raising, and relative
    PDF hrefs are absolutized against occ.gov.
    """
    parser = _DigitalAssetsTableParser()
    parser.feed(html)
    applications: list[OccDigitalAssetApplication] = []
    for cells in parser.rows:
        if len(cells) != 3:
            continue
        (date_text, _), (applicant, _), (pdf_text, pdf_href) = cells
        if not applicant or not _DA_DATE_RE.match(date_text):
            continue
        pdf_url: str | None = None
        # Only trust an href when the cell isn't an explicit "N/A" and the
        # target actually looks like a document link.
        if pdf_href and pdf_text.strip().lower() not in {"n/a", "na", ""}:
            candidate = (
                pdf_href
                if pdf_href.startswith(("http://", "https://"))
                else f"{_OCC_BASE_URL}{pdf_href}"
            )
            if _is_allowed_occ_url(candidate):
                pdf_url = candidate
            else:
                # Drop the link, keep the row (the applicant/date still
                # feed the digital-assets tag).
                logger.warning(
                    "occ_cas: dropping non-occ.gov PDF link %r for applicant %r",
                    candidate, applicant,
                )
        applications.append(
            OccDigitalAssetApplication(
                applicant=applicant,
                received_date=_parse_cas_date(date_text),
                pdf_url=pdf_url,
            )
        )
    if not applications:
        logger.warning("occ_cas: digital-assets page parsed to 0 rows — layout change?")
    return applications


# ── Wayback Machine history of the digital-assets page (one-off backfill) ──
# (URL constants live up top with the other endpoints; the parsing/union
# helpers below are pure and unit-tested.)

_WAYBACK_TIMESTAMP_RE = re.compile(r"\d{4,14}")


def parse_cdx_timestamps(body: object) -> list[str]:
    """CDX ``output=json`` body -> unique capture timestamps, oldest first.

    Verified shape: a JSON array whose first row is the header
    (``["timestamp","statuscode"]``) followed by one ``["<ts>","200"]``
    row per collapsed capture. Tolerant like every other parser here:
    junk rows, non-200 strays, and a non-list body skip / return empty
    rather than raise. Ascending order matters downstream — the history
    union lets the NEWEST snapshot win each applicant's PDF set.
    """
    if not isinstance(body, list):
        logger.warning("occ_cas: wayback CDX body is not a list (%s)", type(body).__name__)
        return []
    timestamps: set[str] = set()
    for row in body:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        timestamp = str(row[0]).strip()
        status = str(row[1]).strip() if len(row) > 1 else "200"
        if not _WAYBACK_TIMESTAMP_RE.fullmatch(timestamp):
            continue  # header row ('timestamp') or junk
        if status != "200":
            continue  # filter=statuscode:200 should guarantee this; belt-and-braces
        timestamps.add(timestamp)
    return sorted(timestamps)


# Wayback link rewriting: an archived page's hrefs can arrive as
# ``https://web.archive.org/web/<ts><flags>/<original-url>`` or the
# root-relative ``/web/<ts><flags>/<original-url>``. The ``id_`` fetch is
# supposed to return unrewritten bytes, but belt-and-braces: normalize any
# rewritten href back to its ORIGINAL URL *before* the parser applies the
# occ.gov allowlist — otherwise a root-relative ``/web/…`` href would
# absolutize onto www.occ.gov as a broken (but allowlist-passing!) URL.
_WAYBACK_REWRITE_RE = re.compile(
    r"^(?:https?://web\.archive\.org)?/web/\d{4,14}[a-z_]*/(?P<original>.*)$",
    re.IGNORECASE,
)
# Sentinel for a rewritten href with no recoverable original: it is not an
# occ.gov URL, so the parser's existing allowlist drops it (with its
# standard warning) — the "doesn't normalize cleanly -> drop" rule rides
# the same enforcement point as every other link.
_WAYBACK_UNRESOLVED = "https://web.archive.org/__unresolved-wayback-href__"

_HREF_ATTR_RE = re.compile(r"""(href\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)


def normalize_wayback_snapshot_href(href: str) -> str | None:
    """Undo Wayback rewriting on one href.

    Returns the embedded original URL for a cleanly rewritten href, the
    href unchanged when it is not archive-shaped at all (relative occ.gov
    paths, absolute occ.gov URLs — the normal id_ snapshot content), and
    None when it IS archive-shaped but no absolute http(s) original can be
    recovered. Pure; never raises.
    """
    match = _WAYBACK_REWRITE_RE.match(href.strip())
    if match is None:
        return href
    original = match.group("original").strip()
    if original.startswith(("http://", "https://")):
        return original
    if original.startswith("//"):
        # Scheme-relative rewrite variant (/web/<ts>///host/path).
        return f"https:{original}"
    return None


def strip_wayback_rewrites(html: str) -> str:
    """Normalize every quoted ``href`` attribute in snapshot HTML back to
    its original URL so the UNCHANGED live-page parser — and its occ.gov
    host allowlist — sees exactly what the original page served.
    Unresolvable archive-shaped hrefs become a web.archive.org sentinel
    the allowlist then drops."""

    def _replace(match: re.Match[str]) -> str:
        normalized = normalize_wayback_snapshot_href(match.group(3))
        if normalized is None:
            normalized = _WAYBACK_UNRESOLVED
        return f"{match.group(1)}{match.group(2)}{normalized}{match.group(2)}"

    return _HREF_ATTR_RE.sub(_replace, html)


@dataclass
class OccDigitalAssetHistoryEntry:
    """One applicant row unioned across every archived snapshot.

    Identity = (normalized applicant name, received date) — the page's
    only stable key. ``pdf_urls`` is the newest NON-EMPTY set of
    allowlisted public-portion PDF URLs seen for the key: a later capture
    that re-links the PDF elsewhere replaces the set; one that drops the
    link entirely (or the row itself) does not erase history.
    """

    applicant: str
    received_date: date | None = None
    pdf_urls: tuple[str, ...] = ()


def union_digital_asset_history(
    snapshots: Sequence[tuple[str, Sequence[OccDigitalAssetApplication]]],
) -> list[OccDigitalAssetHistoryEntry]:
    """Union parsed snapshot rows (``(timestamp, applications)`` pairs).

    Snapshots are processed oldest→newest regardless of input order.
    Dedupe key: ``normalize_bank_name(applicant)`` + received date, so the
    page re-spelling a name ("TR CO, NA" vs "Trust Company, N.A.") does
    not duplicate an applicant, while a re-filed application (new received
    date) stays a distinct row. The newest capture wins the display casing
    and, when it carries at least one PDF URL for the key, the PDF set.
    Output is sorted by received date then name for stable logs.
    """
    entries: dict[tuple[str, date | None], OccDigitalAssetHistoryEntry] = {}
    for _timestamp, applications in sorted(snapshots, key=lambda pair: pair[0]):
        snapshot_urls: dict[tuple[str, date | None], list[str]] = {}
        for application in applications:
            key = (normalize_bank_name(application.applicant), application.received_date)
            if not key[0]:
                continue
            entry = entries.get(key)
            if entry is None:
                entry = OccDigitalAssetHistoryEntry(
                    applicant=application.applicant,
                    received_date=application.received_date,
                )
                entries[key] = entry
            else:
                entry.applicant = application.applicant  # newest casing wins
            if application.pdf_url:
                urls = snapshot_urls.setdefault(key, [])
                if application.pdf_url not in urls:
                    urls.append(application.pdf_url)
        # Only a non-empty per-snapshot set REPLACES the stored set.
        for key, urls in snapshot_urls.items():
            entries[key].pdf_urls = tuple(urls)
    return sorted(
        entries.values(),
        key=lambda entry: (entry.received_date or date.min, entry.applicant.lower()),
    )
