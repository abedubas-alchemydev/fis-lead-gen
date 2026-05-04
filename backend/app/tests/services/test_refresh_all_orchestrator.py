"""Unit tests for ``decide_pipelines`` — the gate-and-scope logic that
backs the per-firm refresh-all orchestrator.

Coverage:

  - ``scope="all"`` (default): all four legacy gates plus the new
    filings gate evaluated normally.
  - ``scope="list_only"``: website + contacts force-skipped regardless
    of their gate; the three list-view gates (financials, clearing,
    filings) evaluated normally.
  - Filings gate: open only when the BD has a cik AND
    ``last_filing_date is None``. Closed otherwise.
  - Already-complete firm under scope="all" returns an empty
    ``to_run``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.models.broker_dealer import BrokerDealer
from app.services.refresh_all_orchestrator import (
    SUB_ENRICH,
    SUB_HEALTH_CHECK,
    SUB_REFRESH_FILINGS,
    SUB_REFRESH_FINANCIALS,
    SUB_RESOLVE_WEBSITE,
    decide_pipelines,
)


def _bd(**fields: Any) -> BrokerDealer:
    """Default fixture: every gate-input field NULL → all gates open
    when callers don't override. Tests pass field overrides to flip
    individual gates closed."""
    defaults: dict[str, Any] = {
        "id": 1,
        "name": "Acme Securities LLC",
        "crd_number": "1234",
        "cik": None,
        "website": None,
        "website_source": None,
        "latest_net_capital": None,
        "yoy_growth": None,
        "health_status": None,
        "current_clearing_type": None,
        "current_clearing_partner": None,
        "last_filing_date": None,
    }
    defaults.update(fields)
    return BrokerDealer(**defaults)


# ─────────────────────────── scope="all" (default) ───────────────────────────


def test_default_scope_all_gates_open_no_cik_filings_skipped() -> None:
    """Fully-empty firm with no CIK → 4 originals open, filings skipped
    (no CIK = nothing to query EDGAR with)."""
    bd = _bd()
    decision = decide_pipelines(bd, has_contacts=False)

    assert set(decision.to_run) == {
        SUB_REFRESH_FINANCIALS,
        SUB_RESOLVE_WEBSITE,
        SUB_HEALTH_CHECK,
        SUB_ENRICH,
    }
    assert set(decision.to_skip) == {SUB_REFRESH_FILINGS}


def test_default_scope_all_gates_open_with_cik_filings_included() -> None:
    """Same firm but with a CIK → filings gate also opens."""
    bd = _bd(cik="0000320193")
    decision = decide_pipelines(bd, has_contacts=False)

    assert set(decision.to_run) == {
        SUB_REFRESH_FINANCIALS,
        SUB_RESOLVE_WEBSITE,
        SUB_HEALTH_CHECK,
        SUB_ENRICH,
        SUB_REFRESH_FILINGS,
    }
    assert decision.to_skip == ()


def test_default_scope_already_complete_returns_empty_to_run() -> None:
    """Every field set → empty to_run, the endpoint returns 'skipped'."""
    bd = _bd(
        cik="0000320193",
        website="https://acme.example",
        latest_net_capital=1_000_000.0,
        yoy_growth=5.0,
        health_status="healthy",
        current_clearing_type="self_clearing",
        current_clearing_partner="Acme Self Clearing",
        last_filing_date=date(2025, 8, 15),
    )
    decision = decide_pipelines(bd, has_contacts=True)
    assert decision.to_run == ()
    assert set(decision.to_skip) == {
        SUB_REFRESH_FINANCIALS,
        SUB_RESOLVE_WEBSITE,
        SUB_HEALTH_CHECK,
        SUB_ENRICH,
        SUB_REFRESH_FILINGS,
    }


# ─────────────────────────── scope="list_only" ───────────────────────────


def test_list_only_force_skips_website_and_contacts() -> None:
    """Even when website + contacts gates are open, list_only force-skips
    them — they don't drive a master-list grid column."""
    bd = _bd(cik="0000320193")  # all NULL except cik → all gates open
    decision = decide_pipelines(bd, has_contacts=False, scope="list_only")

    assert SUB_RESOLVE_WEBSITE not in decision.to_run
    assert SUB_RESOLVE_WEBSITE in decision.to_skip
    assert SUB_ENRICH not in decision.to_run
    assert SUB_ENRICH in decision.to_skip
    # Three list-view gates should still be open.
    assert set(decision.to_run) == {
        SUB_REFRESH_FINANCIALS,
        SUB_HEALTH_CHECK,
        SUB_REFRESH_FILINGS,
    }


def test_list_only_respects_closed_list_gates() -> None:
    """list_only doesn't force list-view gates open — closed ones stay
    closed (we never overwrite present data)."""
    bd = _bd(
        cik="0000320193",
        latest_net_capital=2_000_000.0,
        yoy_growth=3.5,
        health_status="ok",
        current_clearing_type="introducing",
        current_clearing_partner="Pershing",
        last_filing_date=date(2025, 6, 1),
    )
    decision = decide_pipelines(bd, has_contacts=False, scope="list_only")

    # No list-view work to do.
    assert decision.to_run == ()
    # Website + contacts force-skipped; the three list gates closed naturally.
    assert set(decision.to_skip) == {
        SUB_REFRESH_FINANCIALS,
        SUB_RESOLVE_WEBSITE,
        SUB_HEALTH_CHECK,
        SUB_ENRICH,
        SUB_REFRESH_FILINGS,
    }


def test_list_only_includes_filings_when_only_filing_date_is_missing() -> None:
    """The row-button case: financials + clearing already populated,
    but last_filing_date is null. Only filings should run."""
    bd = _bd(
        cik="0000320193",
        latest_net_capital=2_000_000.0,
        yoy_growth=3.5,
        health_status="ok",
        current_clearing_type="introducing",
        current_clearing_partner="Pershing",
        # last_filing_date stays None
    )
    decision = decide_pipelines(bd, has_contacts=True, scope="list_only")

    assert decision.to_run == (SUB_REFRESH_FILINGS,)


# ─────────────────────────── filings gate edge cases ───────────────────────────


def test_filings_skipped_without_cik() -> None:
    """No cik → cannot ask EDGAR → filings closed even with date NULL."""
    bd = _bd(cik=None)
    decision = decide_pipelines(bd, has_contacts=False)
    assert SUB_REFRESH_FILINGS in decision.to_skip
    assert SUB_REFRESH_FILINGS not in decision.to_run


def test_filings_skipped_with_existing_date() -> None:
    """Date already populated → don't re-fetch (daily cron handles
    drift); gate is "missing", not "stale"."""
    bd = _bd(cik="0000320193", last_filing_date=date(2025, 6, 1))
    decision = decide_pipelines(bd, has_contacts=False)
    assert SUB_REFRESH_FILINGS in decision.to_skip
    assert SUB_REFRESH_FILINGS not in decision.to_run


def test_filings_open_when_cik_present_and_date_missing() -> None:
    """The catch-up case for firms initial_load missed."""
    bd = _bd(cik="0000320193", last_filing_date=None)
    decision = decide_pipelines(bd, has_contacts=False)
    assert SUB_REFRESH_FILINGS in decision.to_run
