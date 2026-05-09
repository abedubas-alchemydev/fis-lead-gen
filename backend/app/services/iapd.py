"""SEC IAPD Investment Adviser bulk download service.

Fetches and parses the monthly ``ia{MMDDYY}.zip`` Compilation Report
that the SEC publishes at:

    https://www.sec.gov/data-research/sec-markets-data/
    information-about-registered-investment-advisers-exempt-reporting-advisers

Each ZIP holds one CSV with ~17,000 rows × 448 columns covering every
SEC-registered Investment Adviser. Columns map to Form ADV Item numbers
(``"5F(2)(c)"``, ``"5G(1)"``, ...). This service:

1. Scrapes the index page for the most recent ``ia{MMDDYY}.zip`` link
   (the SEC moved the file path mid-Jan 2026 and may move it again, so
   we discover at runtime rather than constructing the URL).
2. Downloads to a TTL-cached local file (7 days) using the same
   identity-encoding + SEC user-agent pattern as the EDGAR bulk path.
3. Parses the CSV into ``IapdAdvisorRecord`` rows by column header name
   (not positional index) so a future SEC reorder doesn't silently
   break us.

The 13F filter join lives in ``thirteen_f_filter.py``; the upsert and
merge live in ``data_merge.py`` (``merge_advisor_records``).
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

import httpx

from app.core.config import settings
from app.services.service_models import IapdAdvisorRecord


logger = logging.getLogger(__name__)


# ── Compilation CSV column header names ──
# Locked in PR 2 from the May 2026 ZIP. We resolve column INDEXES at
# parse time by header lookup, so the SEC reordering columns won't
# silently misalign fields. If they rename a column, the parser logs a
# warning and that field comes through as None on every row — visible
# in QA before it can rot data.

_HEADER_CRD = "Organization CRD#"
_HEADER_SEC_FILE = "SEC#"
_HEADER_CIK = "CIK#"
_HEADER_NAME = "Primary Business Name"
_HEADER_LEGAL_NAME = "Legal Name"
_HEADER_CITY = "Main Office City"
_HEADER_STATE = "Main Office State"
_HEADER_STATUS = "SEC Current Status"
_HEADER_LAST_FILING = "Latest ADV Filing Date"
_HEADER_WEBSITE = "Website Address"

# Form ADV Item 5.F.(2) — regulatory AUM split.
# Per Form ADV instructions:
#   5F(2)(a) = discretionary RAUM
#   5F(2)(b) = non-discretionary RAUM
#   5F(2)(c) = total RAUM (a + b)
_HEADER_DISCRETIONARY_AUM = "5F(2)(a)"
_HEADER_NON_DISCRETIONARY_AUM = "5F(2)(b)"
_HEADER_REGULATORY_AUM = "5F(2)(c)"
# 5F(2)(f) = Total number of clients (per SEC Form ADV column code).
_HEADER_TOTAL_CLIENTS = "5F(2)(f)"

# Form ADV Item 5.G — types of advisory activities (12 checkboxes,
# matching the Form ADV Item 5.G mockup Deshorn shared in the original
# Slack thread).
_ADVISORY_ACTIVITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("5G(1)", "financial_planning_services"),
    ("5G(2)", "portfolio_management_individuals_or_small_businesses"),
    ("5G(3)", "portfolio_management_investment_companies"),
    ("5G(4)", "portfolio_management_pooled_investment_vehicles"),
    ("5G(5)", "portfolio_management_businesses_or_institutional_clients"),
    ("5G(6)", "pension_consulting_services"),
    ("5G(7)", "selection_of_other_advisers"),
    ("5G(8)", "publication_of_periodicals"),
    ("5G(9)", "security_ratings_or_pricing_services"),
    ("5G(10)", "market_timing_services"),
    ("5G(11)", "educational_seminars_workshops"),
    ("5G(12)", "other"),
)

# Form ADV Item 5.D.(1) — types of clients (yes/no per category). The
# 5D(2)(*) sibling cell carries the per-type client count.
_CLIENT_TYPE_HEADERS: tuple[tuple[str, str, str], ...] = (
    ("5D(1)(a)", "5D(2)(a)", "individuals"),
    ("5D(1)(b)", "5D(2)(b)", "high_net_worth_individuals"),
    ("5D(1)(c)", "5D(2)(c)", "banking_or_thrift_institutions"),
    ("5D(1)(d)", "5D(2)(d)", "investment_companies"),
    ("5D(1)(e)", "5D(2)(e)", "business_development_companies"),
    ("5D(1)(f)", "5D(2)(f)", "pooled_investment_vehicles"),
    ("5D(1)(g)", "5D(2)(g)", "pension_and_profit_sharing_plans"),
    ("5D(1)(h)", "5D(2)(h)", "charitable_organizations"),
    ("5D(1)(i)", "5D(2)(i)", "state_or_municipal_government_entities"),
    ("5D(1)(j)", "5D(2)(j)", "other_investment_advisers"),
    ("5D(1)(k)", "5D(2)(k)", "insurance_companies"),
    ("5D(1)(l)", "5D(2)(l)", "sovereign_wealth_funds"),
    ("5D(1)(m)", "5D(2)(m)", "other"),
)


# Pattern used to discover the latest registered-IA ZIP URL on the SEC
# index page. Excludes ``-exempt`` filenames (those are the Exempt
# Reporting Advisers feed, which is a different scope).
_IA_ZIP_HREF_PATTERN = re.compile(
    r'href="(?P<href>/files/[^"]*?ia(?P<mm>\d{2})(?P<dd>\d{2})(?P<yy>\d{2})\.zip)"',
    re.IGNORECASE,
)

# Same identity-encoding fix the EDGAR bulk service uses — Akamai's
# Content-Encoding handling on GCP egress IPs corrupts gzipped responses.
_REQUEST_HEADERS = {
    "User-Agent": settings.sec_user_agent,
    "Accept": "text/html,application/xhtml+xml,application/zip,*/*;q=0.8",
    "Accept-Encoding": "identity",
}


class IapdService:
    """Bulk Investment Adviser download + CSV parsing."""

    PROJECT_ROOT = Path(__file__).resolve().parents[3]

    def _resolve_project_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.PROJECT_ROOT / path
        return path

    async def fetch_compilation_records(
        self,
        *,
        limit: int | None = None,
        force_refresh: bool = False,
    ) -> list[IapdAdvisorRecord]:
        """Download (or use cached) latest ZIP, return parsed records.

        Pass ``force_refresh=True`` to bypass the TTL cache and re-fetch
        from SEC. ``limit`` truncates the parser early — useful for the
        ``--limit N`` flag on ``initial_load_advisors``.
        """

        zip_url = await self.fetch_latest_zip_url()
        zip_path = await self._ensure_zip_cached(zip_url, force_refresh=force_refresh)
        return await asyncio.to_thread(self._parse_compilation_zip, zip_path, limit)

    async def fetch_latest_zip_url(self) -> str:
        """Scrape the SEC index page for the most recent ``ia{MMDDYY}.zip``.

        The SEC moved the file path mid-Jan 2026 (added an ``/other/``
        segment) and may move it again. Discovering at runtime via the
        index page rather than constructing a URL by date keeps the
        ingest resilient to those reorganizations.
        """

        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        async with httpx.AsyncClient(
            timeout=timeout, headers=_REQUEST_HEADERS, follow_redirects=True
        ) as client:
            response = await client.get(settings.iapd_index_url)
            response.raise_for_status()
        html_text = response.text

        candidates: list[tuple[str, str]] = []  # [(yyyymmdd, full_url)]
        for match in _IA_ZIP_HREF_PATTERN.finditer(html_text):
            href = match.group("href")
            # Skip Drupal admin / edit URLs. The SEC index page leaks
            # ``/files/node/{id}/edit/...`` and ``/files/node/add/...``
            # link previews into the public HTML; those resolve to
            # historical 2020 ``.xlsx`` archives rather than the real
            # current ``.csv`` snapshots. The canonical download path
            # always starts with ``/files/investment/data/``.
            if not href.startswith("/files/investment/data/"):
                continue
            # Skip the Exempt Reporting Advisers companion file. Filter
            # on the FILENAME (not the whole path) — the parent
            # directory name itself carries "exempt-reporting-advisers"
            # so a path-wide substring check catches everything. The
            # registered file is ``ia{MMDDYY}.zip``; companions are
            # ``ia{MMDDYY}-exempt.zip`` / ``-exemptzip.zip`` (Feb 2026
            # typo) / ``-exempt_.zip`` (2018-era variant).
            filename = href.rsplit("/", 1)[-1].lower()
            if "exempt" in filename:
                continue
            mm, dd, yy = match.group("mm"), match.group("dd"), match.group("yy")
            iso_date = f"20{yy}-{mm}-{dd}"
            full_url = f"https://www.sec.gov{href}"
            candidates.append((iso_date, full_url))

        if not candidates:
            raise RuntimeError(
                f"No registered-IA zip links found on {settings.iapd_index_url}. "
                "The SEC may have changed the page layout — inspect manually."
            )

        # Sort by ISO date descending; the lexicographic order on
        # ``YYYY-MM-DD`` is also a chronological order.
        candidates.sort(reverse=True)
        latest_date, latest_url = candidates[0]
        logger.info("IAPD latest registered-IA snapshot: %s — %s", latest_date, latest_url)
        return latest_url

    async def _ensure_zip_cached(self, zip_url: str, *, force_refresh: bool) -> Path:
        """Download the ZIP if cache is missing or stale.

        Cache filename is constant (``registered_advisers.zip``) and gets
        atomically replaced on each fetch — we don't accumulate one file
        per snapshot. The 7-day TTL matches the BD bulk submissions
        cache so monthly cadence updates always trip the refresh.
        """

        zip_path = self._resolve_project_path(settings.iapd_zip_cache_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        if not force_refresh and zip_path.exists() and zip_path.stat().st_size > 0:
            age_seconds = time.time() - zip_path.stat().st_mtime
            if age_seconds < settings.iapd_zip_ttl_seconds:
                logger.info(
                    "Using cached IAPD ZIP at %s (%.1f hours old).",
                    zip_path,
                    age_seconds / 3600,
                )
                return zip_path
            logger.info(
                "Cached IAPD ZIP is %.1f days old — re-downloading.",
                age_seconds / 86400,
            )

        timeout = httpx.Timeout(
            connect=15.0,
            read=settings.iapd_request_timeout_seconds,
            write=15.0,
            pool=10.0,
        )
        temp_path = zip_path.with_suffix(f"{zip_path.suffix}.tmp")

        async with httpx.AsyncClient(
            timeout=timeout, headers=_REQUEST_HEADERS, follow_redirects=True
        ) as client:
            async with client.stream("GET", zip_url) as response:
                response.raise_for_status()
                # ``aiter_raw``, not ``aiter_bytes`` — the body is already
                # ZIP-compressed and httpx's auto-decompress on
                # Content-Encoding headers corrupts the bytes from
                # Akamai's GCP-egress POPs. Same fix the EDGAR bulk path
                # applies. The ZIP is small (~5 MB) so a 1MB chunk is
                # still cheap on memory.
                with temp_path.open("wb") as handle:
                    async for chunk in response.aiter_raw(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)

        temp_path.replace(zip_path)
        logger.info("Downloaded IAPD ZIP to %s (%d bytes).", zip_path, zip_path.stat().st_size)
        return zip_path

    def _parse_compilation_zip(
        self, zip_path: Path, limit: int | None
    ) -> list[IapdAdvisorRecord]:
        """Sync CSV parse. Run from ``asyncio.to_thread`` to keep loop free."""

        with zipfile.ZipFile(zip_path) as archive:
            members = [m for m in archive.namelist() if m.lower().endswith(".csv")]
            if not members:
                raise RuntimeError(f"No CSV in IAPD ZIP {zip_path}: {archive.namelist()}")
            csv_member = members[0]
            with archive.open(csv_member) as raw:
                # The CSV is UTF-8 with occasional latin-1 holdouts in
                # firm-name cells. ``replace`` preserves the row count
                # over a hard fail on a single bad byte.
                text = raw.read().decode("utf-8", errors="replace")

        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration:
            return []

        # Map header name → column index. Strip whitespace because some
        # headers have trailing spaces from the SEC export.
        index_by_header = {h.strip(): i for i, h in enumerate(header)}

        # Resolve every header we care about up-front so the per-row hot
        # loop is just dict-of-int lookups.
        try:
            i_crd = index_by_header[_HEADER_CRD]
            i_name = index_by_header[_HEADER_NAME]
        except KeyError as exc:
            raise RuntimeError(
                f"IAPD CSV missing required column {exc.args[0]!r}; "
                f"first 10 columns: {header[:10]}"
            ) from exc

        i_sec_file = index_by_header.get(_HEADER_SEC_FILE)
        i_cik = index_by_header.get(_HEADER_CIK)
        i_legal = index_by_header.get(_HEADER_LEGAL_NAME)
        i_city = index_by_header.get(_HEADER_CITY)
        i_state = index_by_header.get(_HEADER_STATE)
        i_status = index_by_header.get(_HEADER_STATUS)
        i_last_filing = index_by_header.get(_HEADER_LAST_FILING)
        i_website = index_by_header.get(_HEADER_WEBSITE)
        i_disc_aum = index_by_header.get(_HEADER_DISCRETIONARY_AUM)
        i_non_disc_aum = index_by_header.get(_HEADER_NON_DISCRETIONARY_AUM)
        i_reg_aum = index_by_header.get(_HEADER_REGULATORY_AUM)
        i_total_clients = index_by_header.get(_HEADER_TOTAL_CLIENTS)

        activity_lookups: list[tuple[int, str]] = [
            (index_by_header[h], code)
            for h, code in _ADVISORY_ACTIVITY_HEADERS
            if h in index_by_header
        ]
        client_type_lookups: list[tuple[int, int | None, str]] = [
            (
                index_by_header[h_yes],
                index_by_header.get(h_count),
                code,
            )
            for h_yes, h_count, code in _CLIENT_TYPE_HEADERS
            if h_yes in index_by_header
        ]

        records: list[IapdAdvisorRecord] = []
        for row in reader:
            if limit is not None and len(records) >= limit:
                break

            crd = _clean_text(_safe_get(row, i_crd))
            name = _clean_text(_safe_get(row, i_name))
            if not crd or not name:
                # Form ADV requires both — skip junk rows defensively.
                continue

            advisory_activities: list[str] = [
                code for idx, code in activity_lookups if _is_truthy_flag(_safe_get(row, idx))
            ]
            client_types: list[str] = []
            client_counts: dict[str, int] = {}
            for yes_idx, count_idx, code in client_type_lookups:
                if _is_truthy_flag(_safe_get(row, yes_idx)):
                    client_types.append(code)
                if count_idx is not None:
                    count = _parse_int(_safe_get(row, count_idx))
                    if count is not None:
                        client_counts[code] = count

            records.append(
                IapdAdvisorRecord(
                    crd_number=crd,
                    sec_file_number=_clean_text(_safe_get(row, i_sec_file))
                    if i_sec_file is not None
                    else None,
                    cik=_clean_text(_safe_get(row, i_cik))
                    if i_cik is not None
                    else None,
                    name=name,
                    legal_name=_clean_text(_safe_get(row, i_legal))
                    if i_legal is not None
                    else None,
                    city=_clean_text(_safe_get(row, i_city))
                    if i_city is not None
                    else None,
                    state=_clean_text(_safe_get(row, i_state))
                    if i_state is not None
                    else None,
                    status=_clean_text(_safe_get(row, i_status))
                    if i_status is not None
                    else None,
                    last_filing_date=_parse_date(_safe_get(row, i_last_filing))
                    if i_last_filing is not None
                    else None,
                    website=_clean_text(_safe_get(row, i_website))
                    if i_website is not None
                    else None,
                    discretionary_aum=_parse_float(_safe_get(row, i_disc_aum))
                    if i_disc_aum is not None
                    else None,
                    non_discretionary_aum=_parse_float(_safe_get(row, i_non_disc_aum))
                    if i_non_disc_aum is not None
                    else None,
                    regulatory_aum=_parse_float(_safe_get(row, i_reg_aum))
                    if i_reg_aum is not None
                    else None,
                    total_clients=_parse_int(_safe_get(row, i_total_clients))
                    if i_total_clients is not None
                    else None,
                    advisory_activities=advisory_activities,
                    client_types=client_types,
                    client_counts=client_counts,
                )
            )

        logger.info("Parsed %d IAPD records from %s.", len(records), zip_path.name)
        return records


# ── Cell-value helpers ──
# Local helpers (not exported) — defensive parsing that turns SEC's
# inconsistent empty-cell representations into Python None.


def _safe_get(row: list[str], index: int | None) -> str | None:
    if index is None:
        return None
    if index < 0 or index >= len(row):
        return None
    return row[index]


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    # SEC sometimes renders empty cells as the literal string "0" for
    # date/text fields; leave numeric "0" alone here since this helper
    # only runs against text columns.
    return cleaned


def _is_truthy_flag(value: str | None) -> bool:
    """Return True iff the cell is a Form ADV "yes" indicator.

    The Compilation Report uses "Y", "Yes", or sometimes the indicator
    text itself; "N", "No", and empty all read as False.
    """

    if value is None:
        return False
    cleaned = value.strip().upper()
    return cleaned in {"Y", "YES", "TRUE", "1"}


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", "").replace("$", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    """Parse SEC date cells. The Compilation Report uses MM/DD/YYYY."""

    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    # Try the SEC-standard formats in order of frequency.
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None
