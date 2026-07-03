"""Unit tests for the FDIC BankFind client (``services/fdic_bankfind.py``).

Parser coverage uses payload shapes captured from the LIVE API on
2026-07-02 (institutions envelope with per-row ``data`` nesting, absent
fields on week-old banks, the two different date formats between
/institutions and /history). HTTP behaviour (pagination, envelope
tolerance) is exercised through ``httpx.MockTransport`` — no network in
the default suite.
"""

from __future__ import annotations

from datetime import date

import httpx

from app.services.fdic_bankfind import (
    FDIC_CHANGECODE_NEW_INSTITUTION,
    FdicBankFindService,
    _parse_fdic_date,
    bankfind_public_url,
    parse_history_row,
    parse_institution_row,
)

# Captured live 2026-07-02 (Portrait Bank — note ints for CERT/ACTIVE and
# the MM/DD/YYYY institution date format).
LIVE_INSTITUTION_ROW = {
    "ZIP": "32789",
    "REGAGNT": "FDIC",
    "ACTIVE": 1,
    "BKCLASS": "NM",
    "FED_RSSD": "6075729",
    "CHRTAGNT": "STATE",
    "OFFICES": 1,
    "INSDATE": "06/22/2026",
    "NAME": "Portrait Bank",
    "CITY": "Winter Park",
    "ADDRESS": "941 W Morse Blvd",
    "CERT": 59381,
    "STALP": "FL",
    "ESTYMD": "06/22/2026",
    "ID": "59381",
}


class TestParseInstitutionRow:
    def test_live_shape_parses(self) -> None:
        record = parse_institution_row(LIVE_INSTITUTION_ROW)
        assert record is not None
        assert record.cert == "59381"
        assert record.name == "Portrait Bank"
        assert record.established_date == date(2026, 6, 22)
        assert record.insured_date == date(2026, 6, 22)
        assert record.charter_authority == "STATE"
        assert record.regulator == "FDIC"
        assert record.state == "FL"
        assert record.active is True
        assert record.offices == 1
        # Not requested / absent upstream on week-old banks:
        assert record.website is None
        assert record.asset is None
        assert record.raw["ID"] == "59381"

    def test_absent_fields_are_none_not_errors(self) -> None:
        # The live API simply OMITS null fields from the row; every accessor
        # must be absence-tolerant.
        record = parse_institution_row({"CERT": 1, "NAME": "Tiny Bank"})
        assert record is not None
        assert record.established_date is None
        assert record.active is None  # tri-state: unknown, not False
        assert record.deposits is None

    def test_row_without_cert_or_name_is_skipped(self) -> None:
        assert parse_institution_row({"NAME": "No Cert Bank"}) is None
        assert parse_institution_row({"CERT": 123}) is None
        assert parse_institution_row({"CERT": "  ", "NAME": "Blank Cert"}) is None

    def test_junk_numerics_do_not_raise(self) -> None:
        record = parse_institution_row(
            {"CERT": 5, "NAME": "Junk Bank", "ASSET": "n/a", "OFFICES": "??", "ACTIVE": 0}
        )
        assert record is not None
        assert record.asset is None
        assert record.offices is None
        assert record.active is False


class TestParseHistoryRow:
    def test_iso_datetime_format(self) -> None:
        # /history dates are ISO-with-time — different from /institutions.
        event = parse_history_row(
            {
                "CERT": 59381,
                "INSTNAME": "Portrait Bank",
                "CHANGECODE": 110,
                "PROCDATE": "2026-06-23T00:00:00",
                "EFFDATE": "2026-06-22T00:00:00",
            }
        )
        assert event is not None
        assert event.cert == "59381"
        assert event.change_code == 110
        assert event.process_date == date(2026, 6, 23)
        assert event.effective_date == date(2026, 6, 22)

    def test_row_without_cert_is_skipped(self) -> None:
        assert parse_history_row({"INSTNAME": "Ghost"}) is None


def test_new_institution_changecode_is_110_not_510() -> None:
    """Regression pin: CHANGECODE 110 is "New Institution" (verified live);
    510 is "Change in Legal Name" and would corroborate ~50 unrelated old
    certs per half-year into the vertical."""
    assert FDIC_CHANGECODE_NEW_INSTITUTION == 110


def test_parse_fdic_date_accepts_both_formats_and_junk() -> None:
    assert _parse_fdic_date("06/22/2026") == date(2026, 6, 22)
    assert _parse_fdic_date("2026-06-23T00:00:00") == date(2026, 6, 23)
    assert _parse_fdic_date(None) is None
    assert _parse_fdic_date("") is None
    assert _parse_fdic_date("not-a-date") is None


def test_bankfind_public_url() -> None:
    assert (
        bankfind_public_url("59381")
        == "https://banks.data.fdic.gov/bankfind-suite/bankfind/institution/59381"
    )


def _patch_async_client(monkeypatch, handler) -> None:
    """Route the service's httpx.AsyncClient through a MockTransport.

    Captures the real class BEFORE patching (the module attribute is shared,
    so a lambda that re-reads ``httpx.AsyncClient`` would recurse)."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.fdic_bankfind.httpx.AsyncClient", factory)


async def test_fetch_institutions_sends_window_filter_and_unwraps_envelope(monkeypatch) -> None:
    """The ESTYMD window filter + fields params are sent, and the envelope's
    ``data[].data`` nesting is unwrapped into parsed records."""
    def page(certs: list[int], total: int) -> dict:
        return {
            "meta": {"total": total},
            "data": [
                {"data": {**LIVE_INSTITUTION_ROW, "CERT": cert, "NAME": f"Bank {cert}"}}
                for cert in certs
            ],
            "totals": {"count": total},
        }

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=page([59381, 59379], total=2))

    _patch_async_client(monkeypatch, handler)

    service = FdicBankFindService()
    records = await service.fetch_institutions_established_between(
        date(2026, 1, 1), date(2026, 7, 2)
    )
    assert [r.cert for r in records] == ["59381", "59379"]
    assert len(requests) == 1
    params = dict(requests[0].url.params)
    assert params["filters"] == "ESTYMD:[2026-01-01 TO 2026-07-02]"
    assert "NAME" in params["fields"] and "CHRTAGNT" in params["fields"]


async def test_fetch_history_uses_new_institution_changecode(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"data": {"CERT": 59381, "CHANGECODE": 110, "PROCDATE": "2026-06-23T00:00:00"}}
                ],
                "totals": {"count": 1},
            },
        )

    _patch_async_client(monkeypatch, handler)
    service = FdicBankFindService()
    events = await service.fetch_new_institution_history(date(2026, 6, 1), date(2026, 7, 2))
    assert [e.cert for e in events] == ["59381"]
    filters = dict(requests[0].url.params)["filters"]
    assert "CHANGECODE:110" in filters
    assert "PROCDATE:[2026-06-01 TO 2026-07-02]" in filters


async def test_fetch_by_certs_empty_input_short_circuits(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP call expected for empty cert list")

    _patch_async_client(monkeypatch, handler)
    service = FdicBankFindService()
    assert await service.fetch_institutions_by_certs([]) == []
    assert await service.fetch_institutions_by_certs(["  ", ""]) == []


async def test_fetch_all_pages_stops_at_total(monkeypatch) -> None:
    """A server that always returns a full page must still terminate via
    ``totals.count`` (guards against an infinite drain loop)."""
    import app.services.fdic_bankfind as mod

    monkeypatch.setattr(mod, "_PAGE_SIZE", 2)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params)["offset"])
        calls.append(offset)
        rows = [
            {"data": {"CERT": offset + i, "NAME": f"B{offset + i}"}} for i in range(2)
        ]
        return httpx.Response(200, json={"data": rows, "totals": {"count": 4}})

    _patch_async_client(monkeypatch, handler)
    service = FdicBankFindService()
    records = await service.fetch_institutions_by_certs(["1", "2", "3", "4"])
    assert len(records) == 4
    assert calls == [0, 2]


async def test_fetch_all_active_institutions_filters_active_and_drains_pages(monkeypatch) -> None:
    """The full-directory fetch sends ``filters=ACTIVE:1`` with the stable
    CERT-ascending sort and drains every page (short-page termination)."""
    import app.services.fdic_bankfind as mod

    monkeypatch.setattr(mod, "_PAGE_SIZE", 2)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(dict(request.url.params)["offset"])
        # 3 active institutions across two pages of size 2: [2 rows][1 row].
        certs = [c for c in (offset + 1, offset + 2) if c <= 3]
        rows = [
            {"data": {**LIVE_INSTITUTION_ROW, "CERT": c, "NAME": f"Active Bank {c}"}}
            for c in certs
        ]
        return httpx.Response(200, json={"data": rows, "totals": {"count": 3}})

    _patch_async_client(monkeypatch, handler)
    service = FdicBankFindService()
    records = await service.fetch_all_active_institutions()

    # Drained both pages into the full set, parsed via parse_institution_row.
    assert [r.cert for r in records] == ["1", "2", "3"]
    assert len(requests) == 2  # offset 0 (full) then offset 2 (short -> stop)
    params = dict(requests[0].url.params)
    assert params["filters"] == "ACTIVE:1"
    assert params["sort_by"] == "CERT"
    assert params["sort_order"] == "ASC"
    assert "NAME" in params["fields"] and "CHRTAGNT" in params["fields"]
