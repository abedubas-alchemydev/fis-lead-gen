"""Unit tests for the OCC clients (``services/occ_cas.py``).

CAS is official but UNDOCUMENTED, so these tests pin the payload shapes
captured from the live API on 2026-07-02 (including the placeholder junk
CAS emits — 'To be confirmed' addresses, 'XXXXX' zips — and the
'Consummated/Effective' action spelling) plus the schema-tolerance
contract: unknown actions map to no status change, malformed items are
skipped, never raised. Also covers the national-banks XLSX directory
parser (in-memory workbook) and the digital-assets page HTML parser
(markup snapshot from the live page).
"""

from __future__ import annotations

import io
from datetime import date

import httpx
import openpyxl

from app.services.occ_cas import (
    CHARTER_STATUS_RANK,
    OccCasService,
    charter_status_for_action,
    normalize_bank_name,
    parse_cas_item,
    parse_digital_assets_page,
)

# Captured live 2026-07-02.
LIVE_CAS_ITEM = {
    "ftId": "2",
    "fId": "344521",
    "fstId": "1104",
    "act": "Receipt",
    "actDte": "2026-01-06",
    "ft": "New Bank Charter",
    "cn": "2026-Charter-344521",
    "bn": "World Liberty TR CO, NA",
    "addr": "1177 Kane Concourse",
    "cty": "Bay Harbor Islands",
    "st": "FL",
    "zip": "33154",
    "cnty": "",
    "cped": "2026-02-09",
}


class TestParseCasItem:
    def test_live_shape_parses(self) -> None:
        filing = parse_cas_item(LIVE_CAS_ITEM)
        assert filing is not None
        assert filing.control_number == "2026-Charter-344521"
        assert filing.bank_name == "World Liberty TR CO, NA"
        assert filing.action == "Receipt"
        assert filing.action_date == date(2026, 1, 6)
        assert filing.comment_period_end_date == date(2026, 2, 9)
        assert filing.charter_status == "pending"
        assert filing.details_url == (
            "https://apps.occ.gov/CAS/home/details"
            "?FilingTypeID=2&FilingID=344521&FilingSubtypeID=1104"
        )

    def test_placeholder_address_junk_is_nulled(self) -> None:
        # Live example: Nubank, NA arrived with addr='To be confirmed',
        # zip='XXXXX'. Placeholders must never land in the DB.
        item = {
            **LIVE_CAS_ITEM,
            "cn": "2025-Charter-343355",
            "bn": "Nubank, NA",
            "addr": "To be confirmed",
            "zip": "XXXXX",
            "cty": "McLean",
        }
        filing = parse_cas_item(item)
        assert filing is not None
        assert filing.address is None
        assert filing.zip is None
        assert filing.city == "McLean"

    def test_item_missing_keys_is_skipped_not_raised(self) -> None:
        assert parse_cas_item({}) is None
        assert parse_cas_item({"cn": "x", "bn": "y"}) is None  # no action
        assert parse_cas_item({"cn": "x", "act": "Receipt"}) is None  # no name

    def test_details_url_absent_when_ids_missing(self) -> None:
        item = {k: v for k, v in LIVE_CAS_ITEM.items() if k not in ("ftId", "fId", "fstId")}
        filing = parse_cas_item(item)
        assert filing is not None
        assert filing.details_url is None


class TestActionStatusMapping:
    def test_live_vocabulary(self) -> None:
        # All three action strings observed live 2026-07-02, including the
        # slash spelling of Consummated/Effective.
        assert charter_status_for_action("Receipt") == "pending"
        assert charter_status_for_action("Approved") == "approved"
        assert charter_status_for_action("Consummated/Effective") == "opened"
        assert charter_status_for_action("Withdrawn") == "withdrawn"
        assert charter_status_for_action("Approval Rescinded") == "rescinded"

    def test_unknown_action_maps_to_none_not_error(self) -> None:
        # CAS is undocumented — new vocabulary must land as an event with
        # NO status change, never crash or corrupt the lifecycle.
        assert charter_status_for_action("Some Novel Action") is None
        assert charter_status_for_action(None) is None
        assert charter_status_for_action("") is None

    def test_rank_orders_lifecycle_forward(self) -> None:
        assert CHARTER_STATUS_RANK["pending"] < CHARTER_STATUS_RANK["approved"]
        assert CHARTER_STATUS_RANK["approved"] < CHARTER_STATUS_RANK["opened"]
        # withdrawn/rescinded outrank approved so a terminal outcome lands.
        assert CHARTER_STATUS_RANK["withdrawn"] > CHARTER_STATUS_RANK["approved"]


class TestNormalizeBankName:
    def test_occ_abbreviations_meet_directory_spellings(self) -> None:
        # OCC CAS says 'TR CO, NA'; the directory / digital-assets page
        # spell it out. Both sides must collapse to the same token.
        assert normalize_bank_name("World Liberty TR CO, NA") == normalize_bank_name(
            "World Liberty Trust Company, N.A."
        )
        assert normalize_bank_name("Augustus NB, NA") == normalize_bank_name(
            "Augustus National Bank, National Association"
        )

    def test_distinct_names_stay_distinct(self) -> None:
        assert normalize_bank_name("First National Bank of Alpha") != normalize_bank_name(
            "First National Bank of Beta"
        )


def _patch_async_client(monkeypatch, handler) -> None:
    """Route the service's httpx.AsyncClient through a MockTransport.

    Captures the real class BEFORE patching (the module attribute is shared,
    so a lambda that re-reads ``httpx.AsyncClient`` would recurse)."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.occ_cas.httpx.AsyncClient", factory)


async def test_fetch_new_charter_filings_sends_params_and_tolerates_error_field(
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "items": [LIVE_CAS_ITEM, {"garbage": True}],
                "error": "",
            },
        )

    _patch_async_client(monkeypatch, handler)
    service = OccCasService()
    filings = await service.fetch_new_charter_filings(date(2026, 1, 1), date(2026, 7, 2))
    # The valid item parses; the garbage item is skipped, not raised.
    assert [f.control_number for f in filings] == ["2026-Charter-344521"]
    params = dict(requests[0].url.params)
    # CAS wants US-format dates and the New Bank Charter filing type.
    assert params == {"fromDte": "01/01/2026", "toDte": "07/02/2026", "filingTypes": "2"}


async def test_fetch_national_bank_directory_parses_workbook(monkeypatch) -> None:
    """Header row located by content (not position); numeric cells like
    RSSD arriving as floats are trimmed of the '.0' suffix."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["OFFICE OF THE COMPTROLLER OF THE CURRENCY"])  # banner
    sheet.append([])  # spacer
    sheet.append(["CHARTER NO", "NAME", "ADDRESS (LOC)", "CITY", "STATE", "CERT", "RSSD"])
    sheet.append(["8709", "1st National Bank", "1 Main St", "Lebanon", "OH", 6646, 480723.0])
    sheet.append([None, "Row without charter is skipped", "", "", "", "", ""])
    buffer = io.BytesIO()
    workbook.save(buffer)
    xlsx_bytes = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=xlsx_bytes)

    _patch_async_client(monkeypatch, handler)
    service = OccCasService()
    rows = await service.fetch_national_bank_directory()
    assert len(rows) == 1
    row = rows[0]
    assert (row.charter_number, row.name, row.fdic_cert, row.fed_rssd) == (
        "8709", "1st National Bank", "6646", "480723",
    )
    assert row.state == "OH"


# Markup snapshot of the live digital-assets page table (2026-07-02),
# trimmed to three representative rows: a PDF row, an N/A row (conversion),
# and the header.
DIGITAL_ASSETS_HTML = """
<html><body>
<table id="example_simple" class="row-border stripe">
<thead><tr><th>Date received</th><th>Applicant</th><th>Application</th></tr></thead>
<tbody>
<tr><td>06/26/2026</td><td>CBW Bank</td><td>N/A</td></tr>
<tr>
<td>05/18/2026</td>
<td>Catena Trust Bank, N.A.</td>
<td><a href="/topics/charters-and-licensing/digital-assets-licensing-applications/catena-trust-bank-na.pdf" target="_blank" rel="noopener noreferrer" title="Catena Trust Bank, N.A.">PDF</a></td>
</tr>
<tr>
<td>01/06/2026</td>
<td>World Liberty Trust Company, N.A.</td>
<td><a href="https://www.occ.gov/topics/charters-and-licensing/digital-assets-licensing-applications/world-liberty-trust-company.pdf">PDF</a></td>
</tr>
</tbody>
</table>
</body></html>
"""


class TestParseDigitalAssetsPage:
    def test_parses_rows_and_absolutizes_pdf_urls(self) -> None:
        applications = parse_digital_assets_page(DIGITAL_ASSETS_HTML)
        assert len(applications) == 3

        cbw, catena, world_liberty = applications
        assert cbw.applicant == "CBW Bank"
        assert cbw.received_date == date(2026, 6, 26)
        assert cbw.pdf_url is None  # N/A — conversions publish no PDF

        assert catena.applicant == "Catena Trust Bank, N.A."
        assert catena.pdf_url == (
            "https://www.occ.gov/topics/charters-and-licensing/"
            "digital-assets-licensing-applications/catena-trust-bank-na.pdf"
        )

        # Already-absolute hrefs pass through untouched.
        assert world_liberty.pdf_url.startswith("https://www.occ.gov/")
        assert world_liberty.received_date == date(2026, 1, 6)

    def test_header_and_malformed_rows_are_skipped(self) -> None:
        html = """
        <table><tr><td>not a date</td><td>X</td><td>Y</td></tr>
        <tr><td>06/26/2026</td><td></td><td>N/A</td></tr></table>
        """
        assert parse_digital_assets_page(html) == []

    def test_empty_page_returns_empty_list_not_error(self) -> None:
        assert parse_digital_assets_page("<html></html>") == []

    def test_off_domain_and_plain_http_pdf_links_are_dropped(self) -> None:
        # Host allowlist (security review): only https occ.gov / *.occ.gov
        # links may persist. The ROW survives (the applicant/date still feed
        # the digital-assets tag) — only the link is dropped.
        html = """
        <table>
        <tr><td>06/01/2026</td><td>Off Domain Bank</td>
            <td><a href="https://evil.example.com/app.pdf">PDF</a></td></tr>
        <tr><td>06/02/2026</td><td>Lookalike Bank</td>
            <td><a href="https://notocc.gov/app.pdf">PDF</a></td></tr>
        <tr><td>06/03/2026</td><td>Hyphen Lookalike Bank</td>
            <td><a href="https://evil-occ.gov/app.pdf">PDF</a></td></tr>
        <tr><td>06/04/2026</td><td>Plain HTTP Bank</td>
            <td><a href="http://www.occ.gov/app.pdf">PDF</a></td></tr>
        <tr><td>06/05/2026</td><td>Subdomain Bank</td>
            <td><a href="https://apps.occ.gov/app.pdf">PDF</a></td></tr>
        </table>
        """
        applications = parse_digital_assets_page(html)
        assert [a.applicant for a in applications] == [
            "Off Domain Bank", "Lookalike Bank", "Hyphen Lookalike Bank",
            "Plain HTTP Bank", "Subdomain Bank",
        ]
        assert [a.pdf_url for a in applications] == [
            None,  # off-domain host
            None,  # notocc.gov is not occ.gov
            None,  # evil-occ.gov does not end with '.occ.gov'
            None,  # https only
            "https://apps.occ.gov/app.pdf",  # *.occ.gov is fine
        ]

    def test_relative_hrefs_absolutize_inside_the_allowlist(self) -> None:
        # The normal shape — a relative href — resolves onto www.occ.gov and
        # must keep passing the allowlist.
        applications = parse_digital_assets_page(DIGITAL_ASSETS_HTML)
        assert applications[1].pdf_url.startswith("https://www.occ.gov/")

    def test_da_page_name_matches_cas_name_after_normalization(self) -> None:
        # The tagger's join key: the DA page's spelled-out name must meet
        # CAS's abbreviated one (real pairing observed live).
        applications = parse_digital_assets_page(DIGITAL_ASSETS_HTML)
        assert normalize_bank_name(applications[2].applicant) == normalize_bank_name(
            "World Liberty TR CO, NA"
        )
