"""Tests for ``EdgarService.lookup_cik_for_bd`` — the CIK-backfill entry
point used by ``scripts/backfill_cik.py``.

Four scenarios cover the brief's contract:

* ``sec_file_number`` hits ``browse-edgar`` directly → ``found``
* ``sec_file_number`` misses, name search hits → ``found`` (fallback path)
* Both paths miss → ``not_found``
* Name search returns multiple rows and the ``state`` tiebreaker does
  not narrow it to one → ``ambiguous`` (we deliberately do not guess)

A fifth test pins the state-disambiguation success path so a future
"simplify the parser" PR cannot silently regress that branch.

All HTTP is mocked via ``respx`` — no real SEC calls in CI.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx

from app.services.edgar import EdgarService


def _company_detail_page(name: str, cik: str) -> str:
    """Render the canonical browse-edgar single-company page that
    ``EdgarService.BROWSE_HEADER_PATTERN`` matches against."""
    return (
        '<html><body>'
        f'<span class="companyName">{name} '
        '<acronym title="Central Index Key">CIK</acronym>#: '
        f'<a href="/cgi-bin/browse-edgar?action=getcompany&amp;CIK={cik}'
        '&amp;type=&amp;dateb=&amp;owner=include&amp;count=40">View</a>'
        '</span>'
        '<table><tr><td>2025-01-01</td></tr></table>'
        '</body></html>'
    )


def _no_match_page() -> str:
    return '<html><body><p>No matching companies.</p></body></html>'


def _multi_match_results_page(rows: list[tuple[str, str, str]]) -> str:
    """Render a multi-row browse-edgar results table.

    ``rows`` is ``[(cik, name, state), ...]``. The HTML shape mirrors
    the live SEC layout that ``EdgarService._COMPANY_TABLE_ROW_PATTERN``
    is calibrated against.
    """
    rendered_rows = "\n".join(
        '<tr>'
        f'<td><a href="/cgi-bin/browse-edgar?action=getcompany&amp;CIK={cik}'
        f'&amp;type=BD&amp;dateb=&amp;owner=include&amp;count=40">{cik}</a></td>'
        f'<td><a href="#">{name}</a></td>'
        f'<td>{state}</td>'
        '</tr>'
        for cik, name, state in rows
    )
    return (
        '<html><body>'
        '<table class="tableFile2" summary="Results">'
        '<tr><th>CIK</th><th>Company</th><th>State</th></tr>'
        f'{rendered_rows}'
        '</table>'
        '</body></html>'
    )


def _bd(
    *,
    name: str = "ACME SECURITIES LLC",
    state: str | None = "NY",
    sec_file_number: str | None = None,
) -> SimpleNamespace:
    """Build a ``CikLookupTarget``-shaped object. ``SimpleNamespace``
    is enough — the service only reads three attributes."""
    return SimpleNamespace(
        name=name, state=state, sec_file_number=sec_file_number,
    )


@respx.mock
async def test_lookup_returns_found_when_file_number_resolves() -> None:
    """Path 1: ``bd.sec_file_number`` is present and ``browse-edgar``
    returns a single-company detail page. We extract the CIK directly
    and skip the name-search fallback entirely."""
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        return httpx.Response(
            200,
            text=_company_detail_page("ACME SECURITIES LLC", "0001234567"),
        )

    respx.get("https://www.sec.gov/cgi-bin/browse-edgar").mock(
        side_effect=handler,
    )

    async with httpx.AsyncClient() as client:
        status, cik = await EdgarService().lookup_cik_for_bd(
            client,
            _bd(sec_file_number="8-12345"),
        )

    assert status == "found"
    assert cik == "0001234567"
    # Exactly one HTTP call — the file-number path resolved on the first
    # try and we did NOT fall through to a name search.
    assert len(seen_params) == 1
    assert seen_params[0].get("filenum") == "8-12345"


@respx.mock
async def test_lookup_falls_back_to_name_search_when_file_number_misses() -> None:
    """Path 2: ``bd.sec_file_number`` is set but ``browse-edgar`` says
    "No matching" for it (legacy file numbers sometimes drift). The
    name-search fallback takes over and resolves to a single company."""
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if "filenum" in params:
            return httpx.Response(200, text=_no_match_page())
        if "company" in params:
            return httpx.Response(
                200,
                text=_company_detail_page("ACME SECURITIES LLC", "0007654321"),
            )
        return httpx.Response(404)

    respx.get("https://www.sec.gov/cgi-bin/browse-edgar").mock(
        side_effect=handler,
    )

    async with httpx.AsyncClient() as client:
        status, cik = await EdgarService().lookup_cik_for_bd(
            client,
            _bd(sec_file_number="8-99999", name="ACME SECURITIES LLC"),
        )

    assert status == "found"
    assert cik == "0007654321"
    # Two HTTP calls: filenum miss, then company-name hit — confirms the
    # fallback fires only after the file-number path returns nothing.
    assert len(calls) == 2
    assert "filenum" in calls[0]
    assert calls[1].get("company") == "ACME SECURITIES LLC"
    # The name search must scope to type=BD so unrelated mutual-fund
    # filers under the same name don't pollute the result set.
    assert calls[1].get("type") == "BD"


@respx.mock
async def test_lookup_returns_not_found_when_name_search_misses() -> None:
    """Both paths miss (no ``sec_file_number`` on the row, name search
    returns no matches). Legitimate state for FINRA-only filers — caller
    leaves ``cik`` NULL and moves on."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_no_match_page())

    respx.get("https://www.sec.gov/cgi-bin/browse-edgar").mock(
        side_effect=handler,
    )

    async with httpx.AsyncClient() as client:
        status, cik = await EdgarService().lookup_cik_for_bd(
            client,
            _bd(name="UNKNOWN HOLDINGS LLC", sec_file_number=None),
        )

    assert status == "not_found"
    assert cik is None


@respx.mock
async def test_lookup_returns_ambiguous_on_multi_match_no_state_tiebreaker() -> None:
    """Name search returns multiple rows and ``bd.state`` matches none
    of them. We refuse to guess — return ``ambiguous`` so the script
    can log and skip rather than write a wrong CIK."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_multi_match_results_page([
                ("0001111111", "ACME SECURITIES LLC", "CA"),
                ("0002222222", "ACME SECURITIES INC", "TX"),
            ]),
        )

    respx.get("https://www.sec.gov/cgi-bin/browse-edgar").mock(
        side_effect=handler,
    )

    async with httpx.AsyncClient() as client:
        status, cik = await EdgarService().lookup_cik_for_bd(
            client,
            _bd(name="ACME SECURITIES", state="NY", sec_file_number=None),
        )

    assert status == "ambiguous"
    assert cik is None


@respx.mock
async def test_lookup_disambiguates_multi_match_by_state() -> None:
    """Multi-match results page + ``bd.state`` matches exactly one row.
    We return that row's CIK as ``found`` (deliberate state-tiebreaker
    branch — pinned so a future parser refactor cannot silently lose
    it)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_multi_match_results_page([
                ("0001111111", "ACME SECURITIES LLC", "CA"),
                ("0002222222", "ACME SECURITIES INC", "NY"),
                ("0003333333", "ACME SECURITIES CORP", "TX"),
            ]),
        )

    respx.get("https://www.sec.gov/cgi-bin/browse-edgar").mock(
        side_effect=handler,
    )

    async with httpx.AsyncClient() as client:
        status, cik = await EdgarService().lookup_cik_for_bd(
            client,
            _bd(name="ACME SECURITIES", state="NY", sec_file_number=None),
        )

    assert status == "found"
    assert cik == "0002222222"
