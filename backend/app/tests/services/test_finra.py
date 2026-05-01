"""Tests for the FINRA BrokerCheck request fingerprint + Form BD enrichment.

Two surfaces under test:

* ``BROKERCHECK_HEADERS`` / ``BROKERCHECK_BASE_PARAMS`` / ``_FINRA_DETAIL_BASE_URL``
  — constants that lock the Cloudflare-passing browser fingerprint and the
  Solr params for the still-live JSON paths (the enumeration ``_search``
  call and the single-firm ``fetch_website_by_crd`` helper). Cloudflare
  rejects requests that don't carry the full set, and a passing unit test
  won't catch that because respx happily mocks anything — we therefore
  also assert the wire shape of the actual outgoing request.

* ``FinraService.enrich_with_detail`` — the per-firm enrichment pass. As of
  this change it delegates to ``fetch_form_bd_detail`` (PDF pipeline) rather
  than calling the FINRA JSON detail endpoint, because the JSON endpoint
  no longer carries Form BD fields. Tests cover happy path, 404 → record
  untouched, transient fetch error → record untouched + warning, parse
  exception → record untouched + warning. We deliberately verify that a
  failure does NOT clobber existing values on the record.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import respx

from app.services.brokercheck_pdf import (
    FinraPdfFetchError,
    FormBdDetail,
)
from app.services.finra import (
    BROKERCHECK_BASE_PARAMS,
    BROKERCHECK_HEADERS,
    FinraService,
    _FINRA_DETAIL_BASE_URL,
)
from app.services.service_models import FinraBrokerDealerRecord


REQUIRED_BROWSER_HEADERS = {
    "Accept",
    "Accept-Language",
    "Origin",
    "Priority",
    "Referer",
    "Sec-Ch-Ua",
    "Sec-Ch-Ua-Mobile",
    "Sec-Ch-Ua-Platform",
    "Sec-Fetch-Dest",
    "Sec-Fetch-Mode",
    "Sec-Fetch-Site",
    "User-Agent",
}

REQUIRED_SOLR_PARAMS = {"hl", "nrows", "query", "start", "wt"}


def _record(crd: str = "111111") -> FinraBrokerDealerRecord:
    return FinraBrokerDealerRecord(
        crd_number=crd,
        name="Test Securities LLC",
        sec_file_number="8-99999",
        registration_status="Active",
        branch_count=1,
        address_city="New York",
        address_state="NY",
        business_type=None,
    )


# ----- Constants lock down the full Cloudflare-passing fingerprint -----

def test_constants_define_full_browser_header_set() -> None:
    """If a future edit drops any of the 12 fingerprint headers, this
    test fails before the change can ship."""
    assert REQUIRED_BROWSER_HEADERS.issubset(set(BROKERCHECK_HEADERS.keys()))


def test_constants_define_full_solr_param_set() -> None:
    assert REQUIRED_SOLR_PARAMS == set(BROKERCHECK_BASE_PARAMS.keys())


def test_user_agent_advertises_modern_chrome() -> None:
    """Cloudflare also fingerprints stale UAs — keep this on a current
    Chrome major. Bump in lockstep with Sec-Ch-Ua version when needed."""
    ua = BROKERCHECK_HEADERS["User-Agent"]
    assert "Chrome/" in ua
    assert "Mozilla/5.0" in ua
    assert "Windows NT" in ua


def test_origin_and_referer_point_at_brokercheck() -> None:
    assert BROKERCHECK_HEADERS["Origin"] == "https://brokercheck.finra.org"
    assert BROKERCHECK_HEADERS["Referer"] == "https://brokercheck.finra.org/"


def test_detail_base_url_uses_search_firm_path() -> None:
    """``/firm/{crd}`` 403s at Cloudflare; ``/search/firm/{crd}`` is the
    path the browser hits and it returns a 200 (used today by the
    on-demand single-firm resolver — ``fetch_website_by_crd``)."""
    assert _FINRA_DETAIL_BASE_URL == "https://api.brokercheck.finra.org/search/firm"


# ----- Wire-level assertions: enumeration still sends the fingerprint -----

@respx.mock
async def test_search_request_includes_wt_json_param() -> None:
    """The enumeration path must send ``wt=json``. Without it the
    payload comes back as XML (or Cloudflare 403s the malformed call)."""
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        if "query" not in captured:
            captured["query"] = request.url.query.decode("ascii")
        return httpx.Response(200, json={"hits": {"hits": [], "total": 0}})

    respx.get(url__startswith="https://api.brokercheck.finra.org/search/firm").mock(
        side_effect=_capture,
    )

    await FinraService().fetch_broker_dealers(limit=1)

    query = captured.get("query", "")
    assert "wt=json" in query, f"search query missing wt=json: {query!r}"
    assert "hl=true" in query
    assert "filter=active" in query  # active-only filter must be preserved


@respx.mock
async def test_search_request_sends_full_browser_fingerprint() -> None:
    """The enumeration ``_search`` call uses ``BROKERCHECK_HEADERS`` via
    the shared client — assert the wire request carries every required
    header so a future edit that drops one fails fast (locally, in CI)
    rather than at Cloudflare."""
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        if "headers" not in captured:
            captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"hits": {"hits": [], "total": 0}})

    respx.get(url__startswith="https://api.brokercheck.finra.org/search/firm").mock(
        side_effect=_capture,
    )

    await FinraService().fetch_broker_dealers(limit=1)

    headers = captured["headers"]
    missing = [h for h in REQUIRED_BROWSER_HEADERS if h.lower() not in headers]
    assert not missing, f"search request missing required headers: {missing}"


# ----- enrich_with_detail delegates to the PDF adapter -----

async def test_enrich_with_detail_applies_pdf_fields_to_record() -> None:
    """Happy path: ``fetch_form_bd_detail`` returns a populated detail and
    each field is stamped onto the record."""
    record = _record(crd="123456")

    detail = FormBdDetail(
        crd="123456",
        types_of_business=["Mutual fund retailer", "Investment advisory services"],
        executive_officers=[{"name": "DOE, JANE", "title": "DIRECTOR"}],
        firm_operations_text=(
            "This firm does not hold or maintain funds or securities."
        ),
        web_address=None,
    )

    with patch(
        "app.services.finra.fetch_form_bd_detail",
        return_value=detail,
    ):
        await FinraService().enrich_with_detail([record])

    assert record.types_of_business == [
        "Mutual fund retailer",
        "Investment advisory services",
    ]
    assert record.executive_officers == [{"name": "DOE, JANE", "title": "DIRECTOR"}]
    assert record.firm_operations_text == (
        "This firm does not hold or maintain funds or securities."
    )


async def test_enrich_with_detail_stamps_website_when_pdf_carries_one() -> None:
    """The Form BD PDF rarely carries a web address, but when it does the
    record gets it stamped with ``website_source='finra'`` so the merge
    layer can disclose source. The Apollo fallback knows to skip
    finra-stamped rows."""
    record = _record(crd="123456")

    detail = FormBdDetail(
        crd="123456",
        types_of_business=[],
        executive_officers=[],
        firm_operations_text=None,
        web_address="https://acme.example.com",
    )

    with patch(
        "app.services.finra.fetch_form_bd_detail",
        return_value=detail,
    ):
        await FinraService().enrich_with_detail([record])

    assert record.website == "https://acme.example.com"
    assert record.website_source == "finra"


async def test_enrich_with_detail_does_not_overwrite_existing_website() -> None:
    """If the record already has a website (e.g. from an earlier Apollo
    pass), don't clobber it with the FINRA value."""
    record = _record(crd="123456")
    record.website = "https://existing.example.com"
    record.website_source = "apollo"

    detail = FormBdDetail(
        crd="123456",
        types_of_business=[],
        executive_officers=[],
        firm_operations_text=None,
        web_address="https://different.example.com",
    )

    with patch(
        "app.services.finra.fetch_form_bd_detail",
        return_value=detail,
    ):
        await FinraService().enrich_with_detail([record])

    assert record.website == "https://existing.example.com"
    assert record.website_source == "apollo"


async def test_enrich_with_detail_skips_record_when_pdf_returns_none() -> None:
    """``fetch_form_bd_detail`` returns None on a 404 (FINRA has no
    Detailed Report on file). The record is left untouched — the basic
    row from the search-page enumeration is still our best view of the
    firm and the Apollo fallback still gets a chance at the website."""
    record = _record(crd="999999")

    with patch(
        "app.services.finra.fetch_form_bd_detail",
        return_value=None,
    ):
        await FinraService().enrich_with_detail([record])

    assert record.types_of_business is None
    assert record.executive_officers is None
    assert record.firm_operations_text is None
    assert record.website is None


async def test_enrich_with_detail_skips_record_on_fetch_error() -> None:
    """Transient FINRA upstream failures (network / 5xx / non-PDF body)
    surface as ``FinraPdfFetchError``. The pass logs a warning and leaves
    the record untouched — we deliberately don't null fields the
    search-page gave us, that would silently throw away real data on a
    transient outage."""
    record = _record(crd="555555")
    record.business_type = "broker_dealer"  # pre-existing field stays put

    with patch(
        "app.services.finra.fetch_form_bd_detail",
        side_effect=FinraPdfFetchError("network: ConnectError"),
    ):
        await FinraService().enrich_with_detail([record])

    assert record.business_type == "broker_dealer"
    assert record.types_of_business is None
    assert record.executive_officers is None
    assert record.firm_operations_text is None


async def test_enrich_with_detail_skips_record_on_parse_exception() -> None:
    """If the PDF parser raises (corrupt PDF, unexpected layout), log
    and continue — never abort the loop or null the record."""
    record_a = _record(crd="111111")
    record_b = _record(crd="222222")

    detail_b = FormBdDetail(
        crd="222222",
        types_of_business=["Broker or dealer retailing"],
        executive_officers=[],
        firm_operations_text=None,
        web_address=None,
    )

    def _side_effect(crd: str):
        if crd == "111111":
            raise ValueError("corrupt pdf")
        return detail_b

    with patch(
        "app.services.finra.fetch_form_bd_detail",
        side_effect=_side_effect,
    ):
        await FinraService().enrich_with_detail([record_a, record_b])

    # Record A: parser blew up → untouched
    assert record_a.types_of_business is None
    # Record B: subsequent record still gets enriched (loop didn't abort)
    assert record_b.types_of_business == ["Broker or dealer retailing"]


async def test_enrich_with_detail_does_not_overwrite_with_empty_lists() -> None:
    """A PDF that parses cleanly but yielded empty types_of_business /
    officers (e.g. an "Information not available" section on a legacy
    firm) must NOT clobber whatever the search-page gave us."""
    record = _record(crd="123456")
    record.types_of_business = ["Pre-existing type"]
    record.executive_officers = [{"name": "PRE, EXISTING"}]

    detail = FormBdDetail(
        crd="123456",
        types_of_business=[],
        executive_officers=[],
        firm_operations_text=None,
        web_address=None,
    )

    with patch(
        "app.services.finra.fetch_form_bd_detail",
        return_value=detail,
    ):
        await FinraService().enrich_with_detail([record])

    assert record.types_of_business == ["Pre-existing type"]
    assert record.executive_officers == [{"name": "PRE, EXISTING"}]
