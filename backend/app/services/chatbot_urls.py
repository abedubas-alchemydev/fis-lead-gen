"""FE URL builders for Doxie's clickable deep-links.

When Doxie cites a firm or a filtered list in chat, the user should be able
to click straight through to the matching page in the app. The FE owns the
URL <-> filter contract; this module mirrors that contract on the BE so
each tool can stamp a ``link`` (or ``list_link``) onto its result dict.

**Param-key contract** is mirrored from:
- ``frontend/lib/master-list-state.ts``  (BD master list — ``/master-list``)
- ``frontend/lib/advisor-list-state.ts`` (IA list — ``/advisor-list``)
- ``frontend/lib/investors-state.ts``    (Form 4 investors — ``/investors``)

Two contract subtleties worth flagging:

1. **Multi-value params use repeat keys**, not comma-split values:
   ``?clearing_partner=Pershing&clearing_partner=Apex`` — never
   ``?clearing_partner=Pershing,Apex``. Form ADV / FINRA labels can
   legitimately contain commas (e.g. types_of_business
   "...kiosk, with a: bank, savings bank..."), so comma-splitting
   shatters the value (see the parseMultiParam comments in the FE).

2. **Defaults are stripped from the URL** so a filter-less link is
   ``/master-list``, not ``/master-list?list=primary&page=1&limit=25``.
   That keeps share-able links clean and matches what the FE emits when
   the workspace state is at defaults.

3. **lead_priority vs prospect_priority**: the BE filter is still named
   ``lead_priority`` (matching the tool argument), but the FE URL key
   is ``prospect_priority`` (renamed in the FE; old URL still
   silently falls back to default). ``bd_list_url`` accepts the BE name
   and emits the FE key.
"""

from __future__ import annotations

from typing import Iterable
from urllib.parse import quote, quote_plus


# ── Page-level URL constants ───────────────────────────────────────────────
# Pages without filterable URL state. Useful when Doxie wants to nudge the
# user to a top-level destination ("check your alerts", "see vault").
ALERTS_URL = "/alerts"
MY_FAVORITES_URL = "/my-favorites"
VISITED_FIRMS_URL = "/visited-firms"
VAULT_URL = "/vault"
SETTINGS_USERS_URL = "/settings/users"


# Defaults must match the FE state modules so we strip the same values.
_BD_DEFAULTS = {
    "health": "All",
    "prospect_priority": "All",
    "clearing_type": "All",
    "list": "primary",
    "sort_by": "latest_net_capital",
    "sort_dir": "desc",
    "page": 1,
    "limit": 25,
    "source": "master-list",
}

_IA_DEFAULTS = {
    "status": "All",
    "files_13f": "13F",
    "sort_by": "regulatory_aum",
    "sort_dir": "desc",
    "page": 1,
    "limit": 25,
    "source": "advisor-list",
}

_INVESTORS_DEFAULTS = {
    "tab": "buyers",
    "days": 90,
    "sort_by": "transaction_date",
    "sort_dir": "desc",
    "page": 1,
    "limit": 25,
}


def _encode_pair(key: str, value: object) -> str:
    """``key=value`` with the value URL-encoded.

    Bools render as ``true`` / ``false``. ``quote_plus`` (not ``quote``)
    matches the FE's ``URLSearchParams.toString()`` behavior of encoding
    spaces as ``+`` rather than ``%20`` — both are valid in URL query
    strings, but matching keeps share-links emitted by the chat byte-for-
    byte identical to ones the FE produces from the same filter state.
    """
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    else:
        rendered = str(value)
    return f"{quote(key, safe='')}={quote_plus(rendered)}"


def _build_qs(parts: Iterable[tuple[str, object]]) -> str:
    """Join already-screened (key, value) pairs into a query string.

    Repeat-key multi params are produced by passing the same key multiple
    times in ``parts`` — each (key, value) becomes one ``key=value`` entry,
    so ``[("clearing_partner", "Pershing"), ("clearing_partner", "Apex")]``
    yields ``clearing_partner=Pershing&clearing_partner=Apex``.
    """
    rendered = [_encode_pair(k, v) for k, v in parts]
    return "&".join(rendered)


def _add(parts: list[tuple[str, object]], key: str, value: object | None, default: object) -> None:
    """Append ``(key, value)`` to ``parts`` if value is set and not at default.

    ``None`` and empty string are always treated as "not set"; the FE strips
    them in ``toSearchParams``. Non-default scalar values get included.
    """
    if value is None:
        return
    if isinstance(value, str) and value == "":
        return
    if value == default:
        return
    parts.append((key, value))


def _add_multi(parts: list[tuple[str, object]], key: str, values: Iterable[str] | None) -> None:
    """Repeat-key encode a multi-value filter. Empty/None entries are dropped."""
    if not values:
        return
    for entry in values:
        if entry is None:
            continue
        rendered = str(entry).strip()
        if not rendered:
            continue
        parts.append((key, rendered))


# ── Broker-dealer URLs ─────────────────────────────────────────────────────


def bd_list_url(
    *,
    q: str | None = None,
    state: str | None = None,
    health: str | None = None,
    # BE name; emitted under FE key 'prospect_priority'
    lead_priority: str | None = None,
    clearing_partner: Iterable[str] | None = None,
    clearing_type: str | None = None,
    types_of_business: Iterable[str] | None = None,
    min_net_capital: float | None = None,
    max_net_capital: float | None = None,
    registered_after: str | None = None,
    registered_before: str | None = None,
    list_mode: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> str:
    """Build a ``/master-list`` URL with filters pre-applied.

    Any argument left ``None`` is omitted from the URL; arguments equal to
    the FE defaults are also stripped so the link reads cleanly.
    """
    parts: list[tuple[str, object]] = []
    _add(parts, "q", q, "")
    _add(parts, "state", state, "")
    _add(parts, "health", health, _BD_DEFAULTS["health"])
    _add(parts, "prospect_priority", lead_priority, _BD_DEFAULTS["prospect_priority"])
    _add_multi(parts, "clearing_partner", clearing_partner)
    _add(parts, "clearing_type", clearing_type, _BD_DEFAULTS["clearing_type"])
    _add_multi(parts, "types_of_business", types_of_business)
    if min_net_capital is not None and min_net_capital > 0:
        parts.append(("min_net_capital", min_net_capital))
    if max_net_capital is not None and max_net_capital >= 0:
        parts.append(("max_net_capital", max_net_capital))
    _add(parts, "registered_after", registered_after, None)
    _add(parts, "registered_before", registered_before, None)
    _add(parts, "list", list_mode, _BD_DEFAULTS["list"])
    _add(parts, "sort_by", sort_by, _BD_DEFAULTS["sort_by"])
    _add(parts, "sort_dir", sort_dir, _BD_DEFAULTS["sort_dir"])
    _add(parts, "page", page, _BD_DEFAULTS["page"])
    _add(parts, "limit", limit, _BD_DEFAULTS["limit"])
    qs = _build_qs(parts)
    return f"/master-list?{qs}" if qs else "/master-list"


def bd_detail_url(broker_dealer_id: int | str) -> str:
    """Deep-link to a single broker-dealer's detail page."""
    return f"/master-list/{quote(str(broker_dealer_id), safe='')}"


# ── Investment advisor URLs ────────────────────────────────────────────────


def ia_list_url(
    *,
    q: str | None = None,
    state: str | None = None,
    status: str | None = None,
    # The FE encodes this as the literal '13F' or 'all' string, not a bool.
    files_13f: str | bool | None = None,
    advisory_activities: Iterable[str] | None = None,
    client_types: Iterable[str] | None = None,
    min_regulatory_aum: float | None = None,
    max_regulatory_aum: float | None = None,
    registered_after: str | None = None,
    registered_before: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> str:
    """Build an ``/advisor-list`` URL with filters pre-applied.

    ``files_13f`` accepts:
    - ``"13F"`` (default — restrict to 13F filers; stripped from URL)
    - ``"all"`` (broaden universe to all advisors)
    - ``True`` → maps to ``"13F"``
    - ``False`` → maps to ``"all"``
    - ``None`` → omitted
    """
    if isinstance(files_13f, bool):
        files_13f = "13F" if files_13f else "all"

    parts: list[tuple[str, object]] = []
    _add(parts, "q", q, "")
    _add(parts, "state", state, "")
    _add(parts, "status", status, _IA_DEFAULTS["status"])
    _add(parts, "files_13f", files_13f, _IA_DEFAULTS["files_13f"])
    _add_multi(parts, "advisory_activities", advisory_activities)
    _add_multi(parts, "client_types", client_types)
    if min_regulatory_aum is not None and min_regulatory_aum >= 0:
        parts.append(("min_regulatory_aum", min_regulatory_aum))
    if max_regulatory_aum is not None and max_regulatory_aum >= 0:
        parts.append(("max_regulatory_aum", max_regulatory_aum))
    _add(parts, "registered_after", registered_after, None)
    _add(parts, "registered_before", registered_before, None)
    _add(parts, "sort_by", sort_by, _IA_DEFAULTS["sort_by"])
    _add(parts, "sort_dir", sort_dir, _IA_DEFAULTS["sort_dir"])
    _add(parts, "page", page, _IA_DEFAULTS["page"])
    _add(parts, "limit", limit, _IA_DEFAULTS["limit"])
    qs = _build_qs(parts)
    return f"/advisor-list?{qs}" if qs else "/advisor-list"


def ia_detail_url(advisor_id: int | str) -> str:
    """Deep-link to a single investment advisor's detail page."""
    return f"/advisor-list/{quote(str(advisor_id), safe='')}"


# ── Institutional investor URLs ────────────────────────────────────────────
# IIs had no list page after #438 dropped the sidebar tab; #552 restored
# it on 2026-05-27 with a minimal contract (search + page + limit only —
# sort is hard-coded server-side, no AUM/state filters yet).


_II_DEFAULTS = {
    "page": 1,
    "limit": 25,
}


def ii_list_url(
    *,
    q: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> str:
    """Build an ``/institutional-investors`` URL with the query pre-applied.

    The new (post-#552) workspace client at
    ``frontend/components/institutional-investors/institutional-investors-
    workspace-client.tsx`` only URL-syncs three params — search (``q``),
    page, and limit. Sort is hard-coded to ``total_aum desc`` and not
    user-facing. If a future iteration adds state / AUM-band filters,
    extend this signature in lockstep with that workspace client.
    """
    parts: list[tuple[str, object]] = []
    _add(parts, "q", q, "")
    _add(parts, "page", page, _II_DEFAULTS["page"])
    _add(parts, "limit", limit, _II_DEFAULTS["limit"])
    qs = _build_qs(parts)
    return f"/institutional-investors?{qs}" if qs else "/institutional-investors"


def ii_detail_url(investor_id: int | str) -> str:
    """Deep-link to a single institutional investor's detail page."""
    return f"/institutional-investors/{quote(str(investor_id), safe='')}"


# ── Form 4 investors URLs ──────────────────────────────────────────────────


def investors_url(
    *,
    tab: str | None = None,
    q: str | None = None,
    ticker: str | None = None,
    state: str | None = None,
    days: int | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> str:
    """Build an ``/investors`` URL with filters pre-applied.

    ``days`` is constrained on the FE to one of {30, 90, 180, 365}; values
    outside that set are not rejected here but the FE will silently fall
    back to its default (90).
    """
    parts: list[tuple[str, object]] = []
    _add(parts, "tab", tab, _INVESTORS_DEFAULTS["tab"])
    _add(parts, "q", q, "")
    _add(parts, "ticker", ticker, "")
    _add(parts, "state", state, "")
    _add(parts, "days", days, _INVESTORS_DEFAULTS["days"])
    if min_value is not None and min_value >= 0:
        parts.append(("min_value", min_value))
    if max_value is not None and max_value >= 0:
        parts.append(("max_value", max_value))
    _add(parts, "sort_by", sort_by, _INVESTORS_DEFAULTS["sort_by"])
    _add(parts, "sort_dir", sort_dir, _INVESTORS_DEFAULTS["sort_dir"])
    _add(parts, "page", page, _INVESTORS_DEFAULTS["page"])
    _add(parts, "limit", limit, _INVESTORS_DEFAULTS["limit"])
    qs = _build_qs(parts)
    return f"/investors?{qs}" if qs else "/investors"


# ── Internal allowlist (FE-mirrored) ───────────────────────────────────────
# The FE markdown renderer uses this list to decide whether a URL is an
# in-app navigation (next/link router.push) or external (anchor target=_blank).
# Exported as a sorted tuple so the FE can keep its allowlist in sync via a
# simple grep across stacks.
INTERNAL_ROUTE_PREFIXES: tuple[str, ...] = (
    "/advisor-list",
    "/alerts",
    "/institutional-investors",
    "/investors",
    "/master-list",
    "/my-favorites",
    "/settings",
    "/vault",
    "/visited-firms",
)
