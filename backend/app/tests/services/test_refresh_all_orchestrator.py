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
    SUB_REFRESH_CLEARING,
    SUB_REFRESH_FILINGS,
    SUB_REFRESH_FINANCIALS,
    SUB_RESOLVE_WEBSITE,
    decide_pipelines,
    gap_report_for,
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
    """Fully-empty firm with no CIK → all open gates run, filings skipped
    (no CIK = nothing to query EDGAR with)."""
    bd = _bd()
    decision = decide_pipelines(bd, has_contacts=False)

    assert set(decision.to_run) == {
        SUB_REFRESH_FINANCIALS,
        SUB_RESOLVE_WEBSITE,
        SUB_HEALTH_CHECK,
        SUB_REFRESH_CLEARING,
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
        SUB_REFRESH_CLEARING,
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
        registration_date=date(1990, 1, 1),
        formation_date=date(1989, 12, 1),
    )
    decision = decide_pipelines(bd, has_contacts=True)
    assert decision.to_run == ()
    assert set(decision.to_skip) == {
        SUB_REFRESH_FINANCIALS,
        SUB_RESOLVE_WEBSITE,
        SUB_HEALTH_CHECK,
        SUB_REFRESH_CLEARING,
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
    # All list-view gates should still be open.
    assert set(decision.to_run) == {
        SUB_REFRESH_FINANCIALS,
        SUB_HEALTH_CHECK,
        SUB_REFRESH_CLEARING,
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
        registration_date=date(1990, 1, 1),
        formation_date=date(1989, 12, 1),
    )
    decision = decide_pipelines(bd, has_contacts=False, scope="list_only")

    # No list-view work to do.
    assert decision.to_run == ()
    # Website + contacts force-skipped; the list gates closed naturally.
    assert set(decision.to_skip) == {
        SUB_REFRESH_FINANCIALS,
        SUB_RESOLVE_WEBSITE,
        SUB_HEALTH_CHECK,
        SUB_REFRESH_CLEARING,
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
        registration_date=date(1990, 1, 1),
        formation_date=date(1989, 12, 1),
        # last_filing_date stays None
    )
    decision = decide_pipelines(bd, has_contacts=True, scope="list_only")

    assert decision.to_run == (SUB_REFRESH_FILINGS,)


# ─────────────────────────── health-check gate edge cases ───────────────────────────


def test_health_check_fires_when_registration_date_missing_even_with_clearing_filled() -> None:
    """The gate widening: health-check is the catch-all for any FINRA
    Form BD-derived field that's still NULL. A firm whose clearing
    partner / type are already extracted but whose registration_date is
    null must still trigger health-check, otherwise the FINRA enrichment
    that would fill registration_date never runs. Regression for the
    pre-fix behavior where 2,793 of 2,798 broker_dealers ended up with
    NULL registration_date because their clearing fields were filled
    first and the gate closed."""
    bd = _bd(
        cik="0000320193",
        latest_net_capital=2_000_000.0,
        yoy_growth=3.5,
        health_status="ok",
        current_clearing_type="fully_disclosed",
        current_clearing_partner="Pershing",
        last_filing_date=date(2025, 6, 1),
        # registration_date stays None — the trigger
    )
    decision = decide_pipelines(bd, has_contacts=True)
    assert SUB_HEALTH_CHECK in decision.to_run


def test_health_check_fires_when_formation_date_missing_even_with_everything_else_filled() -> None:
    """Symmetric regression for ``formation_date`` — both fields come off
    the same FINRA Form BD parse and are paired in the gate predicate."""
    bd = _bd(
        cik="0000320193",
        latest_net_capital=2_000_000.0,
        yoy_growth=3.5,
        health_status="ok",
        current_clearing_type="fully_disclosed",
        current_clearing_partner="Pershing",
        last_filing_date=date(2025, 6, 1),
        registration_date=date(1990, 1, 1),
        # formation_date stays None — the trigger
    )
    decision = decide_pipelines(bd, has_contacts=True)
    assert SUB_HEALTH_CHECK in decision.to_run


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


# ─────────────────────────── aggressive=True (bulk gap-fill) ───────────────────────────


def _fully_filled_bd(**overrides: Any) -> BrokerDealer:
    """Every legacy-strict gate-input field populated → with
    ``aggressive=False`` all gates close. Tests in this section pass
    overrides to flip a single aggressive-only field into a gap shape
    so we can confirm aggressive=True opens that specific gate while
    aggressive=False leaves everything closed."""
    defaults: dict[str, Any] = {
        "cik": "0000320193",
        "website": "https://acme.example",
        "latest_net_capital": 2_000_000.0,
        "yoy_growth": 3.5,
        "health_status": "ok",
        "current_clearing_type": "fully_disclosed",
        "current_clearing_partner": "Pershing",
        "last_filing_date": date(2025, 6, 1),
        "registration_date": date(1990, 1, 1),
        "formation_date": date(1989, 12, 1),
    }
    defaults.update(overrides)
    return _bd(**defaults)


def test_aggressive_treats_unknown_clearing_type_as_gap() -> None:
    """The 409 staging rows whose ``current_clearing_type='unknown'``
    were skipped by the strict gate (it only checks IS NULL). Aggressive
    mode must catch this sentinel and re-fire the clearing pipeline so
    the PR #409 resolver fix can retry these firms."""
    bd = _fully_filled_bd(
        current_clearing_type="unknown",
        current_clearing_partner=None,  # paired with unknown
    )
    # Default (strict) — gate is open because partner is None.
    legacy = decide_pipelines(bd, has_contacts=True)
    assert SUB_REFRESH_CLEARING in legacy.to_run

    # Aggressive — same row would also trip if both partner and type
    # were filled but clearing_classification was 'needs_review'. See
    # next test. Here we just confirm the unknown sentinel matters.
    bd2 = _fully_filled_bd(current_clearing_type="unknown")
    aggressive = decide_pipelines(bd2, has_contacts=True, aggressive=True)
    assert SUB_REFRESH_CLEARING in aggressive.to_run


def test_aggressive_treats_needs_review_classification_as_gap() -> None:
    """The 1,189 staging rows whose ``clearing_classification='needs_review'``
    are the bulk of the "Not on file" tooltip population. Strict gate
    misses them entirely; aggressive must re-fire clearing."""
    bd = _fully_filled_bd(clearing_classification="needs_review")

    legacy = decide_pipelines(bd, has_contacts=True)
    assert SUB_REFRESH_CLEARING in legacy.to_skip  # strict ignores this field

    aggressive = decide_pipelines(bd, has_contacts=True, aggressive=True)
    assert SUB_REFRESH_CLEARING in aggressive.to_run


def test_aggressive_widens_health_check_to_detail_fields() -> None:
    """``registration_date`` + ``formation_date`` are filled but a
    detail-page FINRA field (``dba_names``) is NULL. Strict gate closes
    health-check; aggressive must open it so the FINRA parse re-runs
    and fills the missing detail-page field."""
    bd = _fully_filled_bd(dba_names=None)

    legacy = decide_pipelines(bd, has_contacts=True)
    assert SUB_HEALTH_CHECK in legacy.to_skip

    aggressive = decide_pipelines(bd, has_contacts=True, aggressive=True)
    assert SUB_HEALTH_CHECK in aggressive.to_run


def test_aggressive_off_preserves_legacy_behavior() -> None:
    """Backward-compat anchor: with aggressive=False, every existing
    sentinel/unfilled-detail field must remain a non-gap. Locks the
    contract for ``POST /broker-dealers/{id}/refresh-all`` which uses
    the default."""
    bd = _fully_filled_bd(
        current_clearing_type="unknown",  # sentinel — aggressive-only
        clearing_classification="needs_review",  # sentinel — aggressive-only
        dba_names=None,  # detail-page — aggressive-only
        types_of_business=None,  # detail-page — aggressive-only
        three_year_cagr=None,  # detail-page — aggressive-only
        latest_excess_net_capital=None,  # detail-page — aggressive-only
        current_clearing_partner="Pershing",  # filled so strict closes clearing
    )
    decision = decide_pipelines(bd, has_contacts=True)
    # Strict mode: every aggressive-only signal is invisible. With partner
    # filled and type set (even to 'unknown'), the strict clearing gate
    # closes. All other strict gates already closed in _fully_filled_bd.
    assert decision.to_run == ()


def test_gap_report_returns_column_names() -> None:
    """The new ``gap_report_for`` helper returns per-pipeline column
    name lists. The bulk script's scan-only mode uses this to print a
    per-column summary without firing any pipelines."""
    bd = _bd(
        cik="0000320193",
        latest_net_capital=None,
        yoy_growth=None,
        current_clearing_type="unknown",
        clearing_classification="needs_review",
        dba_names=None,
        registration_date=date(1990, 1, 1),
        formation_date=date(1989, 12, 1),
    )
    report = gap_report_for(bd, has_contacts=False, aggressive=True)

    # Financials gate: net_capital + yoy_growth + health_status all NULL.
    assert "latest_net_capital" in report[SUB_REFRESH_FINANCIALS]
    assert "yoy_growth" in report[SUB_REFRESH_FINANCIALS]

    # Clearing gate fires on partner=NULL (strict) plus unknown sentinel
    # and needs_review (aggressive only).
    assert "current_clearing_partner" in report[SUB_REFRESH_CLEARING]
    assert "current_clearing_type=unknown" in report[SUB_REFRESH_CLEARING]
    assert "clearing_classification" in report[SUB_REFRESH_CLEARING]

    # Health gate: registration_date + formation_date are set, so strict
    # closed — but dba_names=None opens it under aggressive.
    assert "registration_date" not in report[SUB_HEALTH_CHECK]
    assert "dba_names" in report[SUB_HEALTH_CHECK]


def test_aggressive_treats_total_assets_and_required_min_as_gap() -> None:
    """``latest_total_assets`` + ``required_min_capital`` are detail-page
    fields filled as a side-effect of refresh-financials. They were
    initially omitted from the aggressive predicate list, which left
    BDs with NULL-here-and-everywhere-else-filled invisible to the
    bulk gap-fill. This test pins the fix."""
    bd = _fully_filled_bd(latest_total_assets=None)
    decision = decide_pipelines(bd, has_contacts=True, aggressive=True)
    assert SUB_REFRESH_FINANCIALS in decision.to_run

    bd2 = _fully_filled_bd(required_min_capital=None)
    decision2 = decide_pipelines(bd2, has_contacts=True, aggressive=True)
    assert SUB_REFRESH_FINANCIALS in decision2.to_run

    # Strict mode: both fields are aggressive-only; the legacy gate
    # closes when net_capital / yoy_growth / health_status are filled.
    decision_strict = decide_pipelines(bd, has_contacts=True)
    assert SUB_REFRESH_FINANCIALS in decision_strict.to_skip


def test_gap_report_strict_mode_omits_aggressive_only_fields() -> None:
    """The legacy ``gap_report_for(..., aggressive=False)`` must not
    surface sentinel/detail-page fields. Anchors the strict mode for
    any future callers besides ``decide_pipelines``."""
    bd = _fully_filled_bd(
        current_clearing_type="unknown",
        clearing_classification="needs_review",
        dba_names=None,
    )
    report = gap_report_for(bd, has_contacts=True, aggressive=False)

    assert report[SUB_REFRESH_CLEARING] == []  # sentinels invisible
    assert report[SUB_HEALTH_CHECK] == []      # detail-page invisible
    assert report[SUB_REFRESH_FINANCIALS] == []
    assert report[SUB_RESOLVE_WEBSITE] == []
    assert report[SUB_ENRICH] == []
    assert report[SUB_REFRESH_FILINGS] == []
