"""Tests for the form-code filter in ``FilingMonitorService._fetch_live_alerts``.

The monitor reads SEC EDGAR ``submissions/CIK*.json`` feeds. Broker-dealer
EDGAR submissions carry ``X-17A-5`` (annual audited financial report);
they do **not** carry ``BD`` (registration) or ``17A-11`` (deficiency
notice) — those live in FINRA Web CRD. The previous filter looked for
the FINRA values and consequently emitted zero alerts across 100+ runs.

This module locks in the corrected behaviour:

* X-17A-5 within the last 30 days produces a ``Form X-17A-5`` alert at
  ``priority="low"``.
* X-17A-5 older than 30 days is skipped (otherwise every BD's full audit
  history would dump into the alerts table on first run).
* ``BD`` and ``17A-11`` forms — if EDGAR ever did carry them — are
  ignored. The new monitor is a single-purpose X-17A-5 watcher.

Tests mock the EDGAR endpoint with ``respx`` so they never hit the SEC
network and run deterministically in CI.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import respx

from app.services.filing_monitor import FilingMonitorService


def _bd(bd_id: int = 1) -> SimpleNamespace:
    """Minimal duck-typed broker-dealer for ``_fetch_live_alerts`` input.

    The method only reads ``id`` and ``filings_index_url`` from each
    broker_dealer, so we don't need a real ORM instance here.
    """
    return SimpleNamespace(
        id=bd_id,
        filings_index_url="https://data.sec.gov/submissions/CIK0001234567.json",
    )


def _payload(forms: list[tuple[str, str, str]]) -> dict[str, object]:
    """Build a minimal SEC submissions JSON shaped like the live feed.

    ``forms`` is a list of ``(form, filingDate, accessionNumber)``
    triples. The live feed uses parallel arrays under
    ``filings.recent``.
    """
    return {
        "cik": "0001234567",
        "name": "TEST BD LLC",
        "filings": {
            "recent": {
                "form": [item[0] for item in forms],
                "filingDate": [item[1] for item in forms],
                "accessionNumber": [item[2] for item in forms],
            },
        },
    }


@respx.mock
async def test_x17a5_within_30_days_produces_low_priority_alert() -> None:
    today = date.today()
    recent = (today - timedelta(days=5)).isoformat()
    respx.get("https://data.sec.gov/submissions/CIK0001234567.json").mock(
        return_value=httpx.Response(
            200,
            json=_payload([("X-17A-5", recent, "0001234567-26-000001")]),
        )
    )

    alerts = await FilingMonitorService()._fetch_live_alerts([_bd()])

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.form_type == "Form X-17A-5"
    assert alert.priority == "low"
    assert "X-17A-5" in alert.summary
    assert alert.bd_id == 1
    assert alert.dedupe_key == "Form X-17A-5:1:0001234567-26-000001"


@respx.mock
async def test_x17a5_older_than_30_days_is_skipped() -> None:
    today = date.today()
    stale = (today - timedelta(days=400)).isoformat()
    respx.get("https://data.sec.gov/submissions/CIK0001234567.json").mock(
        return_value=httpx.Response(
            200,
            json=_payload([("X-17A-5", stale, "0001234567-25-000001")]),
        )
    )

    alerts = await FilingMonitorService()._fetch_live_alerts([_bd()])

    assert alerts == []


@respx.mock
async def test_form_bd_and_17a11_in_edgar_payload_are_ignored() -> None:
    """If EDGAR ever returns BD or 17A-11 (it doesn't today), they must
    still be ignored. The monitor is a single-purpose X-17A-5 watcher;
    Form BD registrations and 17a-11 deficiencies need a separate
    FINRA-driven watcher."""
    today = date.today()
    recent = (today - timedelta(days=5)).isoformat()
    respx.get("https://data.sec.gov/submissions/CIK0001234567.json").mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [
                    ("BD", recent, "0001234567-26-bd001"),
                    ("17A-11", recent, "0001234567-26-defcy"),
                    ("FOCUSN", recent, "0001234567-26-focus"),
                ]
            ),
        )
    )

    alerts = await FilingMonitorService()._fetch_live_alerts([_bd()])

    assert alerts == []


@respx.mock
async def test_mixed_forms_only_recent_x17a5_emitted() -> None:
    today = date.today()
    recent = (today - timedelta(days=10)).isoformat()
    stale = (today - timedelta(days=400)).isoformat()
    respx.get("https://data.sec.gov/submissions/CIK0001234567.json").mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [
                    ("X-17A-5", recent, "0001234567-26-cur"),
                    ("X-17A-5", stale, "0001234567-25-prev"),
                    ("FOCUSN", recent, "0001234567-26-focus"),
                    ("BD", recent, "0001234567-26-bd"),
                ]
            ),
        )
    )

    alerts = await FilingMonitorService()._fetch_live_alerts([_bd()])

    assert len(alerts) == 1
    assert alerts[0].dedupe_key == "Form X-17A-5:1:0001234567-26-cur"


@respx.mock
async def test_broker_dealer_without_filings_index_url_is_skipped() -> None:
    bd_no_url = SimpleNamespace(id=42, filings_index_url=None)

    # No respx mock — if the code tried to fetch we'd get a respx routing
    # error.
    alerts = await FilingMonitorService()._fetch_live_alerts([bd_no_url])

    assert alerts == []
