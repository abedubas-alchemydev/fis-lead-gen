"""URL contract tests for ``app.services.chatbot_urls``.

These tests pin down the param-key contract that Doxie tool responses
stamp onto their ``link`` / ``list_link`` fields. The same contract is
defined on the FE in ``frontend/lib/master-list-state.ts`` and siblings —
when a FE param key is renamed, this suite plus the FE one must move in
lockstep, or Doxie-emitted URLs silently lose filters.

The intent is to catch:
- defaults being emitted where the FE would strip them (would inflate
  share-links and surface a stale filter chip on landing),
- key renames (lead_priority/prospect_priority is the historical example),
- multi-value filters being comma-split instead of repeated.
"""

from __future__ import annotations

from app.services.chatbot_urls import (
    ALERTS_URL,
    INTERNAL_ROUTE_PREFIXES,
    MY_FAVORITES_URL,
    SETTINGS_USERS_URL,
    VAULT_URL,
    VISITED_FIRMS_URL,
    bd_detail_url,
    bd_list_url,
    ia_detail_url,
    ia_list_url,
    ii_detail_url,
    ii_list_url,
    investors_url,
)


# ── BD list URL ─────────────────────────────────────────────────────────


class TestBdListUrl:
    def test_no_args_yields_bare_route(self) -> None:
        # All filters at defaults must NOT appear in the URL — matches
        # the FE's toSearchParams behavior of stripping defaults.
        assert bd_list_url() == "/master-list"

    def test_query_threads_through_as_q(self) -> None:
        assert bd_list_url(q="Apex") == "/master-list?q=Apex"

    def test_spaces_become_plus_not_percent_20(self) -> None:
        # URLSearchParams.toString() on the FE emits ``+``; match that
        # so share-links are byte-identical to FE-generated ones.
        assert (
            bd_list_url(q="Apex Clearing Corp")
            == "/master-list?q=Apex+Clearing+Corp"
        )

    def test_state_filter_emitted(self) -> None:
        assert bd_list_url(state="CA") == "/master-list?state=CA"

    def test_lead_priority_renames_to_prospect_priority(self) -> None:
        # The BE filter is ``lead_priority`` (and the by-filter tool's
        # argument matches), but the FE URL key was renamed to
        # ``prospect_priority`` in a prior PR. The builder must translate
        # so links land on the right FE chip.
        assert (
            bd_list_url(lead_priority="hot")
            == "/master-list?prospect_priority=hot"
        )

    def test_default_prospect_priority_stripped(self) -> None:
        # ``All`` is the FE default — never emit it.
        assert bd_list_url(lead_priority="All") == "/master-list"

    def test_multi_value_clearing_partner_uses_repeat_keys(self) -> None:
        out = bd_list_url(clearing_partner=["Pershing", "Apex"])
        # Order preserved; repeat-key encoding (not comma-split). FOCUS
        # types_of_business strings can contain commas legitimately, so
        # comma-splitting would shatter them.
        assert out == "/master-list?clearing_partner=Pershing&clearing_partner=Apex"

    def test_empty_multi_value_omitted(self) -> None:
        assert bd_list_url(clearing_partner=[]) == "/master-list"

    def test_multi_value_drops_empty_entries(self) -> None:
        out = bd_list_url(clearing_partner=["Pershing", "", None])  # type: ignore[list-item]
        assert out == "/master-list?clearing_partner=Pershing"

    def test_net_capital_band(self) -> None:
        out = bd_list_url(min_net_capital=5_000_000, max_net_capital=100_000_000)
        assert "min_net_capital=5000000" in out
        assert "max_net_capital=100000000" in out

    def test_min_net_capital_zero_stripped(self) -> None:
        # A 0 floor is a no-op for non-negative net-capital values; the
        # FE collapses it to "no filter applied" at parse time, so emit
        # it accordingly to avoid surfacing a "Net capital ≥ $0" chip
        # when the user lands.
        assert bd_list_url(min_net_capital=0) == "/master-list"

    def test_combined_filters(self) -> None:
        out = bd_list_url(
            state="NY",
            clearing_partner=["Pershing"],
            lead_priority="hot",
            min_net_capital=1_000_000,
        )
        # Order doesn't matter, but every key must be present.
        for fragment in (
            "state=NY",
            "clearing_partner=Pershing",
            "prospect_priority=hot",
            "min_net_capital=1000000",
        ):
            assert fragment in out

    def test_single_id_emits_ids_param(self) -> None:
        # semantic_firm_search deep-links to the exact firms it cited.
        assert bd_list_url(ids=[42]) == "/master-list?ids=42"

    def test_multi_id_uses_repeat_keys(self) -> None:
        # Repeat-key (not comma-joined) so it round-trips cleanly through
        # URLSearchParams, which would otherwise %2C-encode a comma. Order
        # is preserved so the link mirrors Doxie's listed order.
        out = bd_list_url(ids=[24051, 23969, 22635])
        assert out == "/master-list?ids=24051&ids=23969&ids=22635"

    def test_empty_ids_omitted(self) -> None:
        # No ids → bare route, never a stray ``?ids=``.
        assert bd_list_url(ids=[]) == "/master-list"

    def test_ids_combine_with_other_filters(self) -> None:
        out = bd_list_url(ids=[7], state="CA")
        assert "ids=7" in out
        assert "state=CA" in out


class TestBdDetailUrl:
    def test_numeric_id(self) -> None:
        assert bd_detail_url(42) == "/master-list/42"

    def test_string_id(self) -> None:
        assert bd_detail_url("42") == "/master-list/42"


# ── IA list URL ─────────────────────────────────────────────────────────


class TestIaListUrl:
    def test_no_args_yields_bare_route(self) -> None:
        assert ia_list_url() == "/advisor-list"

    def test_default_files_13f_stripped(self) -> None:
        # ``13F`` is the FE default — stripped.
        assert ia_list_url(files_13f="13F") == "/advisor-list"

    def test_files_13f_all_emitted(self) -> None:
        # Non-default scope MUST be emitted; otherwise the FE's hard 13F
        # scope silently re-applies and the user sees fewer rows than
        # the tool result.
        assert ia_list_url(files_13f="all") == "/advisor-list?files_13f=all"

    def test_files_13f_bool_true_maps_to_13f(self) -> None:
        assert ia_list_url(files_13f=True) == "/advisor-list"

    def test_files_13f_bool_false_maps_to_all(self) -> None:
        assert ia_list_url(files_13f=False) == "/advisor-list?files_13f=all"

    def test_status_default_stripped(self) -> None:
        assert ia_list_url(status="All") == "/advisor-list"

    def test_advisory_activities_repeat_keys(self) -> None:
        out = ia_list_url(advisory_activities=["portfolio_management", "pension_consulting"])
        assert "advisory_activities=portfolio_management" in out
        assert "advisory_activities=pension_consulting" in out

    def test_aum_band(self) -> None:
        out = ia_list_url(min_regulatory_aum=1_000_000_000)
        assert "min_regulatory_aum=1000000000" in out


class TestIaDetailUrl:
    def test_numeric_id(self) -> None:
        assert ia_detail_url(77) == "/advisor-list/77"


class TestIiDetailUrl:
    def test_numeric_id(self) -> None:
        assert ii_detail_url(88) == "/institutional-investors/88"


class TestIiListUrl:
    """The /institutional-investors list page was restored in #552 with a
    minimal URL contract: search + page + limit. Sort is hard-coded
    server-side and not URL-synced."""

    def test_no_args_yields_bare_route(self) -> None:
        assert ii_list_url() == "/institutional-investors"

    def test_query_threads_through_as_q(self) -> None:
        assert ii_list_url(q="Vanguard") == "/institutional-investors?q=Vanguard"

    def test_default_page_and_limit_stripped(self) -> None:
        # Match the FE's `updateUrl` behaviour: defaults are stripped so
        # a filter-less load is plain `/institutional-investors`.
        assert ii_list_url(page=1, limit=25) == "/institutional-investors"

    def test_non_default_page_emitted(self) -> None:
        assert ii_list_url(page=3) == "/institutional-investors?page=3"

    def test_spaces_become_plus(self) -> None:
        # Sanity: encoding matches the FE's URLSearchParams output (same
        # check the BD / IA list URLs already enforce).
        assert (
            ii_list_url(q="Gamma Capital")
            == "/institutional-investors?q=Gamma+Capital"
        )


# ── Investors URL ───────────────────────────────────────────────────────


class TestInvestorsUrl:
    def test_no_args_yields_bare_route(self) -> None:
        assert investors_url() == "/investors"

    def test_default_tab_stripped(self) -> None:
        assert investors_url(tab="buyers") == "/investors"

    def test_non_default_tab_emitted(self) -> None:
        assert investors_url(tab="sellers") == "/investors?tab=sellers"

    def test_days_default_stripped(self) -> None:
        assert investors_url(days=90) == "/investors"

    def test_value_band(self) -> None:
        out = investors_url(min_value=100_000, max_value=10_000_000)
        assert "min_value=100000" in out
        assert "max_value=10000000" in out


# ── Page constants + allowlist ──────────────────────────────────────────


def test_page_constants() -> None:
    """These read like noise but they're the public surface that
    chatbot_tools (and any future Doxie navigation helper) imports.
    Renaming one without updating callers would silently produce 404
    links."""
    assert ALERTS_URL == "/alerts"
    assert MY_FAVORITES_URL == "/my-favorites"
    assert VISITED_FIRMS_URL == "/visited-firms"
    assert VAULT_URL == "/vault"
    assert SETTINGS_USERS_URL == "/settings/users"


def test_internal_route_prefixes_sorted_and_unique() -> None:
    """The FE markdown renderer mirrors this allowlist to decide which
    URLs route through next/link vs. open in a new tab. Sorted +
    duplicate-free so a cross-stack grep against the FE constant is
    deterministic."""
    assert list(INTERNAL_ROUTE_PREFIXES) == sorted(set(INTERNAL_ROUTE_PREFIXES))
    # Every URL builder must produce a path that starts with one of the
    # allowlisted prefixes; otherwise a link would render as external.
    for url in (
        bd_list_url(state="NY"),
        bd_list_url(ids=[1, 2]),
        bd_detail_url(1),
        ia_list_url(state="NY"),
        ia_detail_url(1),
        ii_detail_url(1),
        ii_list_url(q="x"),
        investors_url(),
        ALERTS_URL,
        MY_FAVORITES_URL,
        VISITED_FIRMS_URL,
        VAULT_URL,
        SETTINGS_USERS_URL,
    ):
        assert any(url.startswith(prefix) for prefix in INTERNAL_ROUTE_PREFIXES), (
            f"URL {url!r} not covered by INTERNAL_ROUTE_PREFIXES — FE renderer "
            f"would treat it as external"
        )
