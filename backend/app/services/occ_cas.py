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

Also here: the OCC "Financial Institution Lists" national-banks directory
(``national-by-name.xlsx``) used to reconcile an opened application to its
FDIC row. Verified live 2026-07-02: header row 4 = CHARTER NO | NAME |
ADDRESS (LOC) | CITY | STATE | CERT | RSSD — it carries the FDIC CERT
directly, so charter-number reconciliation is a lookup, not a fuzzy match.

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

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

OCC_CAS_SEARCH_URL = "https://apps.occ.gov/CAS/api/search"
OCC_CAS_DETAILS_URL = "https://apps.occ.gov/CAS/home/details"
# OCC "Financial Institution Lists" — active national banks with charter
# number, FDIC CERT, and RSSD. ~80 KB workbook, refreshed monthly.
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
    """One row of national-by-name.xlsx (active national banks)."""

    charter_number: str
    name: str
    city: str | None = None
    state: str | None = None
    fdic_cert: str | None = None
    fed_rssd: str | None = None


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
