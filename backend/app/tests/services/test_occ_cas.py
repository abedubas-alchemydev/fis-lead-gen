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
import logging
from datetime import date

import httpx
import openpyxl

from app.services.occ_cas import (
    CHARTER_STATUS_RANK,
    OCC_API_KEY_ENV,
    OccCasService,
    OccDigitalAssetApplication,
    charter_status_for_action,
    normalize_bank_name,
    normalize_wayback_snapshot_href,
    parse_active_institution,
    parse_cas_item,
    parse_cdx_timestamps,
    parse_digital_assets_page,
    strip_wayback_rewrites,
    union_digital_asset_history,
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


# ── Wayback history of the digital-assets page (one-off backfill) ────────


class TestParseCdxTimestamps:
    def test_header_row_is_skipped_and_captures_sort_oldest_first(self) -> None:
        # Verified CDX output=json shape: header row first, then captures.
        body = [
            ["timestamp", "statuscode"],
            ["20260105120000", "200"],
            ["20250601083000", "200"],
        ]
        assert parse_cdx_timestamps(body) == ["20250601083000", "20260105120000"]

    def test_duplicates_junk_and_non_200_strays_are_dropped(self) -> None:
        body = [
            ["timestamp", "statuscode"],
            ["20250601083000", "200"],
            ["20250601083000", "200"],  # duplicate capture
            ["20250701083000", "302"],  # filter=statuscode:200 should bar this
            ["not-a-timestamp", "200"],
            [],
            "garbage",
            ["20250801083000"],  # missing status column -> tolerated as 200
        ]
        assert parse_cdx_timestamps(body) == ["20250601083000", "20250801083000"]

    def test_non_list_body_returns_empty_not_error(self) -> None:
        assert parse_cdx_timestamps({"error": "boom"}) == []
        assert parse_cdx_timestamps(None) == []
        assert parse_cdx_timestamps([]) == []


class TestWaybackHrefNormalization:
    def test_absolute_archive_rewrite_normalizes_to_original(self) -> None:
        assert normalize_wayback_snapshot_href(
            "https://web.archive.org/web/20250601083000/https://www.occ.gov/x.pdf"
        ) == "https://www.occ.gov/x.pdf"

    def test_timestamp_modifiers_like_id_and_im_are_handled(self) -> None:
        for modifier in ("id_", "im_", "if_"):
            assert normalize_wayback_snapshot_href(
                f"https://web.archive.org/web/20250601083000{modifier}/https://www.occ.gov/x.pdf"
            ) == "https://www.occ.gov/x.pdf"

    def test_root_relative_rewrite_normalizes_to_original(self) -> None:
        # Without this, the parser would absolutize '/web/…' onto
        # www.occ.gov as a broken but allowlist-PASSING URL.
        assert normalize_wayback_snapshot_href(
            "/web/20250601083000/https://www.occ.gov/x.pdf"
        ) == "https://www.occ.gov/x.pdf"

    def test_scheme_relative_original_gains_https(self) -> None:
        assert normalize_wayback_snapshot_href(
            "/web/20250601083000///www.occ.gov/x.pdf"
        ) == "https://www.occ.gov/x.pdf"

    def test_non_archive_hrefs_pass_through_unchanged(self) -> None:
        for href in (
            "/topics/charters-and-licensing/x.pdf",  # normal id_ snapshot shape
            "https://www.occ.gov/topics/x.pdf",
        ):
            assert normalize_wayback_snapshot_href(href) == href

    def test_archive_shaped_href_without_recoverable_original_is_none(self) -> None:
        assert normalize_wayback_snapshot_href("/web/20250601083000/") is None
        assert normalize_wayback_snapshot_href(
            "/web/20250601083000/javascript:void(0)"
        ) is None

    def test_snapshot_html_feeds_the_unchanged_parser_with_original_urls(self) -> None:
        # End-to-end: strip_wayback_rewrites + the UNCHANGED live parser.
        # Normalization happens BEFORE the allowlist, so the allowlist
        # judges original hosts: occ.gov originals pass even though the
        # bytes came from web.archive.org; off-domain originals and
        # unresolvable rewrites drop (row survives without a link).
        html = """
        <table>
        <tr><td>04/12/2021</td><td>Paxos National Trust</td>
            <td><a href="https://web.archive.org/web/20250601083000/https://www.occ.gov/paxos.pdf">PDF</a></td></tr>
        <tr><td>05/01/2021</td><td>Off Domain Original Bank</td>
            <td><a href="/web/20250601083000/https://evil.example.com/x.pdf">PDF</a></td></tr>
        <tr><td>06/01/2021</td><td>Unresolvable Rewrite Bank</td>
            <td><a href="/web/20250601083000/javascript:void(0)">PDF</a></td></tr>
        <tr><td>07/01/2021</td><td>Raw Relative Bank</td>
            <td><a href="/topics/raw-relative.pdf">PDF</a></td></tr>
        </table>
        """
        applications = parse_digital_assets_page(strip_wayback_rewrites(html))
        assert [a.applicant for a in applications] == [
            "Paxos National Trust", "Off Domain Original Bank",
            "Unresolvable Rewrite Bank", "Raw Relative Bank",
        ]
        assert [a.pdf_url for a in applications] == [
            "https://www.occ.gov/paxos.pdf",  # archive rewrite undone
            None,  # original host fails the occ.gov allowlist
            None,  # no clean original -> sentinel -> allowlist drop
            "https://www.occ.gov/topics/raw-relative.pdf",  # untouched id_ shape
        ]


def _da_app(
    applicant: str, received: date, pdf_url: str | None = None
) -> OccDigitalAssetApplication:
    return OccDigitalAssetApplication(
        applicant=applicant, received_date=received, pdf_url=pdf_url
    )


class TestUnionDigitalAssetHistory:
    def test_union_dedupes_across_snapshots_and_newest_pdf_set_wins(self) -> None:
        older = [
            _da_app("Paxos National Trust", date(2021, 4, 12),
                    "https://www.occ.gov/2021/paxos.pdf"),
            _da_app("Rolled Off Trust Company, N.A.", date(2025, 3, 1),
                    "https://www.occ.gov/2025/rolled-off.pdf"),
        ]
        newer = [
            # Same applicant, re-spelled and with a MOVED pdf link.
            _da_app("Rolled Off TR CO, NA", date(2025, 3, 1),
                    "https://www.occ.gov/2026/rolled-off-moved.pdf"),
            _da_app("Still Current Bank, N.A.", date(2026, 2, 10), None),
        ]
        # Deliberately passed newest-first: the union must sort by timestamp.
        entries = union_digital_asset_history(
            [("20260401000000", newer), ("20250601083000", older)]
        )
        assert [entry.applicant for entry in entries] == [
            "Paxos National Trust",       # rolled off entirely -> retained
            "Rolled Off TR CO, NA",       # one row; newest casing wins
            "Still Current Bank, N.A.",
        ]
        paxos, rolled_off, current = entries
        assert paxos.pdf_urls == ("https://www.occ.gov/2021/paxos.pdf",)
        assert rolled_off.pdf_urls == ("https://www.occ.gov/2026/rolled-off-moved.pdf",)
        assert rolled_off.received_date == date(2025, 3, 1)
        assert current.pdf_urls == ()

    def test_newer_snapshot_without_pdf_does_not_erase_older_set(self) -> None:
        older = [_da_app("X Trust", date(2025, 1, 2), "https://www.occ.gov/x.pdf")]
        newer = [_da_app("X Trust", date(2025, 1, 2), None)]
        entries = union_digital_asset_history(
            [("20250101000000", older), ("20260101000000", newer)]
        )
        assert len(entries) == 1
        assert entries[0].pdf_urls == ("https://www.occ.gov/x.pdf",)

    def test_distinct_received_dates_stay_distinct_rows(self) -> None:
        # A re-filed application (new received date) is a NEW row, not an
        # update of the old one.
        entries = union_digital_asset_history(
            [
                ("20240201000000", [_da_app("X Trust", date(2024, 1, 2))]),
                ("20250701000000", [_da_app("X Trust", date(2025, 6, 2))]),
            ]
        )
        assert [(e.applicant, e.received_date) for e in entries] == [
            ("X Trust", date(2024, 1, 2)),
            ("X Trust", date(2025, 6, 2)),
        ]


# Two fake monthly captures for the fetch tests. Snapshot A carries an
# archive-REWRITTEN absolute href (belt-and-braces: id_ should serve raw
# bytes, but rewritten content must still normalize); snapshot B carries
# the raw relative shape plus a brand-new applicant.
WAYBACK_SNAPSHOT_A_HTML = """
<table>
<tr><td>04/12/2021</td><td>Paxos National Trust</td>
    <td><a href="https://web.archive.org/web/20250601083000/https://www.occ.gov/2021/paxos.pdf">PDF</a></td></tr>
<tr><td>03/01/2025</td><td>Rolled Off Trust Company, N.A.</td>
    <td><a href="/2025/rolled-off.pdf">PDF</a></td></tr>
</table>
"""

WAYBACK_SNAPSHOT_B_HTML = """
<table>
<tr><td>03/01/2025</td><td>Rolled Off TR CO, NA</td>
    <td><a href="/2026/rolled-off-moved.pdf">PDF</a></td></tr>
<tr><td>02/10/2026</td><td>Still Current Bank, N.A.</td><td>N/A</td></tr>
</table>
"""

WAYBACK_CDX_JSON = [
    ["timestamp", "statuscode"],
    ["20250601083000", "200"],
    ["20260401000000", "200"],
]


# ── OCC Institutions API (api.occ.gov — the PRIMARY reconcile directory) ─

# Field set verified live 2026-07-02 (990 records). Identifiers arrive as
# JSON numbers; an uninsured trust company carries FDICCertificateNumber 0.
LIVE_INSTITUTIONS_API_ITEM = {
    "CharterNumber": 25357,
    "BankName": "Erebor Bank, National Association",
    "BankCity": "Columbus",
    "BankStateCode": "OH",
    "BankAddress": "500 Neil Avenue, Suite 140",
    "ZipCode": "43215",
    "InstNationalTrustCompanyInd": 0,
    "CharterType": "National",
    "CharterDate": "2026-02-06",
    "FDICCertificateNumber": 59378,
    "FDICInsuranceStatus": "Insured",
    "FRBRSSDNumber": 1234567,
    "LegalEntityIdentifier": "549300EREBOR0000LE00",
    "RCON": "A1B2",
}

UNINSURED_TRUSTCO_API_ITEM = {
    "CharterNumber": 25301,
    "BankName": "Catena Trust Bank, National Association",
    "BankCity": "New York",
    "BankStateCode": "NY",
    "InstNationalTrustCompanyInd": 1,
    "CharterType": "TrustCo-National",
    "FDICCertificateNumber": 0,  # uninsured — 0 means "no cert"
    "FRBRSSDNumber": 0,
    "LegalEntityIdentifier": "",
}


class TestParseActiveInstitution:
    def test_live_shape_parses_with_numeric_identifiers_stringified(self) -> None:
        row = parse_active_institution(LIVE_INSTITUTIONS_API_ITEM)
        assert row is not None
        assert (row.charter_number, row.name) == (
            "25357", "Erebor Bank, National Association",
        )
        assert (row.city, row.state) == ("Columbus", "OH")
        assert (row.fdic_cert, row.fed_rssd) == ("59378", "1234567")
        assert row.lei == "549300EREBOR0000LE00"
        assert row.charter_type == "National"

    def test_zero_cert_and_rssd_mean_absent_not_the_string_zero(self) -> None:
        # Stamping a literal '0' cert onto two uninsured trust companies
        # would trip the banks.fdic_cert unique index — 0 MUST parse as None.
        row = parse_active_institution(UNINSURED_TRUSTCO_API_ITEM)
        assert row is not None
        assert row.fdic_cert is None
        assert row.fed_rssd is None
        assert row.lei is None  # empty string -> absent
        assert row.charter_type == "TrustCo-National"

    def test_item_missing_required_keys_is_skipped_not_raised(self) -> None:
        assert parse_active_institution({}) is None
        assert parse_active_institution({"CharterNumber": 1}) is None  # no name
        assert parse_active_institution({"BankName": "X Bank"}) is None  # no charter

    def test_api_name_meets_the_cas_spelling_after_normalization(self) -> None:
        # The reconcile join key: the API's spelled-out legal name must
        # collapse to the same token as CAS's abbreviated one.
        row = parse_active_institution(UNINSURED_TRUSTCO_API_ITEM)
        assert normalize_bank_name(row.name) == normalize_bank_name(
            "Catena TR Bank, NA"
        )


async def test_fetch_active_institutions_sends_api_key_header(monkeypatch) -> None:
    monkeypatch.setenv(OCC_API_KEY_ENV, "test-api-key")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json=[LIVE_INSTITUTIONS_API_ITEM, UNINSURED_TRUSTCO_API_ITEM, {"junk": 1}]
        )

    _patch_async_client(monkeypatch, handler)
    service = OccCasService()
    rows = await service.fetch_active_institutions()

    assert requests[0].url == "https://api.occ.gov/institutions/active"
    assert requests[0].headers["X-Api-Key"] == "test-api-key"
    # The key must ride in the header only — never in the URL.
    assert "api_key" not in str(requests[0].url)
    # Two real records parse; the junk item is skipped, not raised.
    assert rows is not None
    assert [row.charter_number for row in rows] == ["25357", "25301"]


async def test_fetch_active_institutions_tolerates_a_wrapped_envelope(monkeypatch) -> None:
    monkeypatch.setenv(OCC_API_KEY_ENV, "test-api-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"institutions": [LIVE_INSTITUTIONS_API_ITEM]})

    _patch_async_client(monkeypatch, handler)
    rows = await OccCasService().fetch_active_institutions()
    assert rows is not None and len(rows) == 1


async def test_fetch_active_institutions_missing_key_returns_none_without_calling(
    monkeypatch, caplog
) -> None:
    monkeypatch.delenv(OCC_API_KEY_ENV, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP call may happen without a key")

    _patch_async_client(monkeypatch, handler)
    with caplog.at_level(logging.WARNING, logger="app.services.occ_cas"):
        rows = await OccCasService().fetch_active_institutions()
    assert rows is None
    assert "OCC_API_KEY unset" in caplog.text


async def test_fetch_active_institutions_http_error_returns_none(monkeypatch, caplog) -> None:
    # The exact failure observed live with unentitled creds: a 403.
    monkeypatch.setenv(OCC_API_KEY_ENV, "test-api-key")
    _patch_async_client(monkeypatch, lambda request: httpx.Response(403))
    with caplog.at_level(logging.WARNING, logger="app.services.occ_cas"):
        rows = await OccCasService().fetch_active_institutions()
    assert rows is None
    assert "Institutions API failed" in caplog.text


async def test_fetch_active_institutions_schema_surprise_returns_none(monkeypatch) -> None:
    monkeypatch.setenv(OCC_API_KEY_ENV, "test-api-key")

    # An unrecognizable envelope and an all-junk list both mean "no usable
    # directory" — the caller must fall back to XLSX, never reconcile
    # against an empty map labeled reconcile_source=api.
    for body in ({"weird": "shape"}, "not-json-collection", [{"junk": 1}], []):
        _patch_async_client(monkeypatch, lambda request, b=body: httpx.Response(200, json=b))
        assert await OccCasService().fetch_active_institutions() is None


async def test_fetch_active_institutions_non_json_body_returns_none(monkeypatch) -> None:
    monkeypatch.setenv(OCC_API_KEY_ENV, "test-api-key")
    _patch_async_client(
        monkeypatch, lambda request: httpx.Response(200, text="<html>maintenance</html>")
    )
    assert await OccCasService().fetch_active_institutions() is None


async def test_fetch_digital_asset_application_history_cdx_failure_degrades(
    monkeypatch,
) -> None:
    """A CDX index failure (archive flake) degrades to an empty history —
    warning + (0, []) — instead of raising and killing a composed run
    (observed in production 2026-07-02: httpx.ConnectError mid-backfill)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cdx/search/cdx":
            raise httpx.ConnectError("All connection attempts failed", request=request)
        raise AssertionError(f"unexpected URL {request.url}")

    _patch_async_client(monkeypatch, handler)
    snapshots, entries = await OccCasService().fetch_digital_asset_application_history(
        delay_seconds=0
    )
    assert snapshots == 0
    assert entries == []


async def test_fetch_digital_asset_application_history_cdx_503_degrades(
    monkeypatch,
) -> None:
    _patch_async_client(
        monkeypatch, lambda request: httpx.Response(503, text="unavailable")
    )
    snapshots, entries = await OccCasService().fetch_digital_asset_application_history(
        delay_seconds=0
    )
    assert snapshots == 0
    assert entries == []


async def test_fetch_digital_asset_application_history_unions_monthly_snapshots(
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/cdx/search/cdx":
            return httpx.Response(200, json=WAYBACK_CDX_JSON)
        if request.url.path.startswith("/web/20250601083000id_/"):
            return httpx.Response(200, text=WAYBACK_SNAPSHOT_A_HTML)
        if request.url.path.startswith("/web/20260401000000id_/"):
            return httpx.Response(200, text=WAYBACK_SNAPSHOT_B_HTML)
        raise AssertionError(f"unexpected URL {request.url}")

    _patch_async_client(monkeypatch, handler)
    service = OccCasService()
    snapshots, entries = await service.fetch_digital_asset_application_history(
        delay_seconds=0
    )

    assert snapshots == 2
    # CDX query: the verified scheme-/www-less capture key + monthly collapse.
    assert requests[0].url.host == "web.archive.org"
    assert dict(requests[0].url.params) == {
        "url": (
            "occ.gov/topics/charters-and-licensing/"
            "digital-assets-licensing-applications/"
            "index-digital-assets-licensing-applications.html"
        ),
        "output": "json",
        "fl": "timestamp,statuscode",
        "filter": "statuscode:200",
        "collapse": "timestamp:6",
    }
    # Snapshots fetched via the raw id_ URLs, oldest first.
    snapshot_paths = [request.url.path for request in requests[1:]]
    assert [path.split("/")[2] for path in snapshot_paths] == [
        "20250601083000id_", "20260401000000id_",
    ]
    assert all("https://www.occ.gov/topics/" in path for path in snapshot_paths)

    assert [entry.applicant for entry in entries] == [
        "Paxos National Trust", "Rolled Off TR CO, NA", "Still Current Bank, N.A.",
    ]
    paxos, rolled_off, current = entries
    # Rewritten href normalized back to the original occ.gov URL...
    assert paxos.pdf_urls == ("https://www.occ.gov/2021/paxos.pdf",)
    # ...relative hrefs absolutized as on the live page; newest set wins.
    assert rolled_off.pdf_urls == ("https://www.occ.gov/2026/rolled-off-moved.pdf",)
    assert current.pdf_urls == ()


async def test_fetch_history_tolerates_a_failed_snapshot(monkeypatch, caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cdx/search/cdx":
            return httpx.Response(200, json=WAYBACK_CDX_JSON)
        if request.url.path.startswith("/web/20250601083000id_/"):
            return httpx.Response(503)  # archive hiccup on one capture
        return httpx.Response(200, text=WAYBACK_SNAPSHOT_B_HTML)

    _patch_async_client(monkeypatch, handler)
    service = OccCasService()
    with caplog.at_level(logging.WARNING, logger="app.services.occ_cas"):
        snapshots, entries = await service.fetch_digital_asset_application_history(
            delay_seconds=0
        )
    # The bad capture is warned about and skipped; the run continues.
    assert snapshots == 1
    assert "wayback snapshot 20250601083000 failed" in caplog.text
    assert [entry.applicant for entry in entries] == [
        "Rolled Off TR CO, NA", "Still Current Bank, N.A.",
    ]
