"""Doxie app-knowledge registry — what each DOX feature does and where to find it.

When a user asks Doxie a navigation or feature question ("where do I see
filing alerts?", "what does the email extractor do?"), the ``get_app_help``
tool looks up entries here instead of having every feature's prose
permanently inlined into the system prompt. The always-on prompt only
needs the short list of feature labels (``FEATURE_LABELS_FOR_PROMPT``);
the detailed help is round-tripped lazily on demand.

The registry is keyed on the feature-permission constants in
``app.core.feature_permissions``. The schema-drift test in
``tests/services/test_chatbot_app_knowledge.py`` enforces that every
feature key has exactly one entry — adding a new feature key without
adding a help entry (or removing one without cleaning up here) fails
the test.

Route values mirror ``frontend/components/layout/app-shell.tsx`` (the
sidebar nav). When a sidebar href changes, update the matching
``AppFeatureHelp.route`` in lockstep so Doxie's deep-link lands on the
right page. Routes are returned to the FE as the result ``link`` field;
make sure each route prefix is in ``INTERNAL_ROUTE_PREFIXES`` (both
``app.services.chatbot_urls`` and ``frontend/components/chatbot/chatbot-message.tsx``)
so the FE renders it as an in-app navigation rather than an external link.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.feature_permissions import (
    ALERTS,
    ALL_FEATURE_KEYS,
    DASHBOARD,
    EMAIL_EXTRACTOR,
    INSTITUTIONAL_INVESTORS,
    INVESTMENT_ADVISORS,
    INVESTORS,
    MASTER_LIST,
    MY_FAVORITES,
    OUTREACH_CONTACTS,
    SENT_OUTREACH,
    SETTINGS,
    USERS,
    VAULT,
    VISITED_FIRMS,
)


@dataclass(frozen=True)
class AppFeatureHelp:
    """One feature's worth of Doxie-facing app knowledge.

    Held flat (no nested children for sub-routes) so the projection shape
    is uniform across the registry and the search helper doesn't need a
    tree walk. Sub-routes that map to a distinct permission key (e.g.
    ``/settings/users`` → ``USERS``) get their own top-level entry; the
    rest (``/settings/account``, ``/settings/email-accounts``,
    ``/settings/clearing-memberships``) are folded into the parent entry's
    ``what_to_do_here`` prose.
    """

    label: str
    route: str
    summary: str
    what_to_do_here: str
    permission_key: str
    synonyms: tuple[str, ...]
    admin_only: bool = False


APP_KNOWLEDGE: dict[str, AppFeatureHelp] = {
    DASHBOARD: AppFeatureHelp(
        label="Dashboard",
        route="/dashboard",
        summary=(
            "The landing page after sign-in. Shows top-level KPIs for the "
            "broker-dealer pipeline (totals, lead-priority breakdown, "
            "recent filings, pipeline refresh status)."
        ),
        what_to_do_here=(
            "Use it as a daily snapshot: check pipeline health, see how "
            "many leads are hot vs. warm vs. cold, jump into Alerts when "
            "the refresh banner indicates new filings have landed."
        ),
        permission_key=DASHBOARD,
        synonyms=("home", "overview", "kpis", "stats", "main page", "landing"),
    ),
    ALERTS: AppFeatureHelp(
        label="Alerts",
        route="/alerts",
        summary=(
            "Recent filing-alerts feed. New Form BD, Form X-17A-5 (FOCUS), "
            "Form 17a-11 (deficiency notices), and related broker-dealer "
            "filings as they arrive on SEC EDGAR, each with an AI-generated "
            "summary and a priority band."
        ),
        what_to_do_here=(
            "Browse new filings, filter by form type or priority, mark "
            "items as read, click through to the firm's detail page for "
            "context."
        ),
        permission_key=ALERTS,
        synonyms=(
            "filings feed",
            "new filings",
            "notifications",
            "what's new",
            "form filings",
            "form bd alerts",
            "x-17a-5 alerts",
            "deficiency alerts",
        ),
    ),
    MASTER_LIST: AppFeatureHelp(
        label="Master List",
        route="/master-list",
        summary=(
            "The full broker-dealer universe. Every SEC-registered "
            "broker-dealer with Form BD, Form X-17A-5 (FOCUS), clearing "
            "arrangements, financial metrics, lead scoring, and contact "
            "data joined into one searchable list."
        ),
        what_to_do_here=(
            "Search by name / CRD / CIK. Filter by state, status, "
            "clearing partner, lead priority, or net-capital band. "
            "Click a row to open the firm's detail page with financials, "
            "clearing history, recent filings, and contacts."
        ),
        permission_key=MASTER_LIST,
        synonyms=(
            "broker-dealers",
            "broker dealers",
            "bd list",
            "bds",
            "bd",
            "broker dealer",
            "focus filers",
            "x-17a-5",
            "x17a5",
            "form bd",
            "form x-17a-5",
            "clearing arrangements",
            "net capital",
        ),
    ),
    INVESTMENT_ADVISORS: AppFeatureHelp(
        label="Investment Advisors",
        route="/advisor-list",
        summary=(
            "SEC-registered investment advisors (Form ADV filers). Carries "
            "AUM breakdown (regulatory / discretionary / non-discretionary), "
            "advisory activities, client mix, and 13F-filer flag."
        ),
        what_to_do_here=(
            "Search by name / CRD / CIK. Filter by state, status, AUM "
            "band, or 13F-filer flag (defaults to 13F filers only). "
            "Click a row for the advisor profile and filing history."
        ),
        permission_key=INVESTMENT_ADVISORS,
        synonyms=(
            "investment advisors",
            "advisor list",
            "advisors",
            "ia",
            "ria",
            "rias",
            "form adv",
            "adv filers",
            "registered investment advisors",
            "aum",
        ),
    ),
    INSTITUTIONAL_INVESTORS: AppFeatureHelp(
        label="Institutional Investors",
        route="/institutional-investors",
        summary=(
            "SEC Form 13F filers — institutional asset managers reporting "
            "quarterly equity holdings. Linked to investment-advisor "
            "profiles where the entity is dual-registered."
        ),
        what_to_do_here=(
            "Search by name / legal name / CIK. Open a profile to see "
            "total AUM, holdings count, latest 13F filing date, and the "
            "linked advisor record where one exists."
        ),
        permission_key=INSTITUTIONAL_INVESTORS,
        synonyms=(
            "institutional investors",
            "13f filers",
            "13f",
            "form 13f",
            "13f-hr",
            "asset managers",
            "ii",
            "holdings",
            "13d",
            "13g",
        ),
    ),
    INVESTORS: AppFeatureHelp(
        label="Investors",
        route="/investors",
        summary=(
            "Form 4 insider-transaction feed. Directors, officers, and "
            "10%+ owners reporting buys and sells of their own company's "
            "stock — filed within ~2 business days of each transaction."
        ),
        what_to_do_here=(
            "Switch between Buyers / Sellers / All. Filter by ticker, "
            "lookback window (30 / 90 / 180 / 365 days), or minimum "
            "transaction value. Click a row to see the issuer, the "
            "insider's role, and a deep-link to the SEC filing."
        ),
        permission_key=INVESTORS,
        synonyms=(
            "form 4",
            "form4",
            "insiders",
            "insider transactions",
            "insider buys",
            "insider sells",
            "directors",
            "officers",
            "ten percent owners",
        ),
    ),
    EMAIL_EXTRACTOR: AppFeatureHelp(
        label="Email Extractor",
        route="/email-extractor",
        summary=(
            "Bulk contact-discovery workflow. Takes a list of firms (or "
            "selects from a favorites list) and runs the multi-provider "
            "discovery chain — site crawl, Hunter, Snov, Apollo — to find "
            "executive contacts (name, role, email, phone where available)."
        ),
        what_to_do_here=(
            "Pick firms by favorites list or pasted CRDs. Kick off "
            "enrichment, watch live progress, then export the discovered "
            "contacts. Apollo phone reveal runs asynchronously via webhook."
        ),
        permission_key=EMAIL_EXTRACTOR,
        synonyms=(
            "email extractor",
            "email finder",
            "find emails",
            "harvest emails",
            "bulk email",
            "contact discovery",
            "contact enrichment",
            "find contacts",
            "phone numbers",
            "apollo",
            "hunter",
        ),
    ),
    SENT_OUTREACH: AppFeatureHelp(
        label="Outreach",
        route="/outreach/sent",
        summary=(
            "History of sent outreach emails. Each row shows the recipient, "
            "subject, sender account, sent timestamp, and engagement state "
            "(opened / replied / bounced)."
        ),
        what_to_do_here=(
            "Audit what's been sent, find replies, re-open threads from "
            "the linked email accounts."
        ),
        permission_key=SENT_OUTREACH,
        synonyms=(
            "outreach",
            "sent outreach",
            "outbound emails",
            "sent emails",
            "outreach history",
            "sent mail",
            "outbound",
        ),
    ),
    OUTREACH_CONTACTS: AppFeatureHelp(
        label="Contacts",
        route="/outreach/contacts",
        summary=(
            "Cross-entity contacts directory. Browses every broker-dealer, "
            "investment advisor, and institutional investor with at least "
            "one contact on file, with a one-click 'Enrich all' button per "
            "firm to refresh phones / emails / LinkedIn via the PDL → "
            "Apollo → Hunter → Snov discovery chain (PR #587)."
        ),
        what_to_do_here=(
            "Pick a firm, click Enrich all to gap-fill its contacts (30-day "
            "cooldown applies), then jump to the firm's detail page to act "
            "on the refreshed list."
        ),
        permission_key=OUTREACH_CONTACTS,
        synonyms=(
            "contacts",
            "contacts page",
            "outreach contacts",
            "enrich contacts",
            "gap fill",
            "gap-fill",
            "refresh contacts",
            "people",
            "executives",
        ),
    ),
    MY_FAVORITES: AppFeatureHelp(
        label="My Favorites",
        route="/my-favorites",
        summary=(
            "Per-user saved firm lists. Items can mix broker-dealers, "
            "investment advisors, institutional investors, and Form 4 "
            "reporting owners on the same list."
        ),
        what_to_do_here=(
            "Create named lists, drop firms into them from any detail "
            "page or list view, and feed those lists into Email Extractor "
            "or Outreach for bulk actions."
        ),
        permission_key=MY_FAVORITES,
        synonyms=(
            "favorites",
            "my favorites",
            "my lists",
            "saved firms",
            "favorited",
            "starred",
            "personal lists",
            "bookmarks",
        ),
    ),
    VISITED_FIRMS: AppFeatureHelp(
        label="Visited Firms",
        route="/visited-firms",
        summary=(
            "Browsing history of firms the user has opened, ordered by "
            "most-recent visit. Includes visit count so frequently "
            "revisited firms surface to the top."
        ),
        what_to_do_here=(
            "Jump back into a firm you opened earlier without searching "
            "for it again."
        ),
        permission_key=VISITED_FIRMS,
        synonyms=(
            "visited firms",
            "history",
            "browsing history",
            "recently viewed",
            "visit history",
            "recent firms",
            "recently opened",
        ),
    ),
    SETTINGS: AppFeatureHelp(
        label="Settings",
        route="/settings",
        summary=(
            "App configuration hub. Houses sub-pages for the user's own "
            "account (/settings/account), connected email-sending accounts "
            "(/settings/email-accounts), team/user management (admin), "
            "and clearing-firm memberships (admin)."
        ),
        what_to_do_here=(
            "Update your profile, connect or rotate email-sending accounts "
            "for outreach, manage admin-only configuration. Admins also "
            "manage users from here."
        ),
        permission_key=SETTINGS,
        synonyms=(
            "settings",
            "configuration",
            "preferences",
            "config",
            "my account",
            "account settings",
            "email accounts",
            "smtp",
            "profile",
            "memberships",
            "clearing memberships",
        ),
    ),
    USERS: AppFeatureHelp(
        label="Users",
        route="/settings/users",
        summary=(
            "Admin-only user management. Add / remove team members, set "
            "their role (admin or viewer), and pick which features each "
            "viewer can see."
        ),
        what_to_do_here=(
            "Invite teammates, change their role, toggle per-feature "
            "access (admins always bypass feature gates)."
        ),
        permission_key=USERS,
        synonyms=(
            "users",
            "user management",
            "team",
            "members",
            "permissions",
            "roles",
            "invite",
            "admin",
        ),
        admin_only=True,
    ),
    VAULT: AppFeatureHelp(
        label="Vault",
        route="/vault",
        summary=(
            "Per-user document store with semantic search. Upload your "
            "own compliance playbooks, internal memos, or reference docs; "
            "the Vault chunks and embeds them so Doxie can answer "
            "questions grounded in your own materials via the ask_vault "
            "tool."
        ),
        what_to_do_here=(
            "Create a folder, upload PDFs or text, wait for embedding to "
            "finish, then ask Doxie a question — Doxie pulls the relevant "
            "chunks via RAG and cites them in its reply."
        ),
        permission_key=VAULT,
        synonyms=(
            "vault",
            "documents",
            "uploaded docs",
            "internal docs",
            "doc rag",
            "files",
            "compliance docs",
            "knowledge base",
            "kb",
            "doc upload",
        ),
    ),
}


# Sanity guard at import time — keeps drift from silently shipping. The
# proper schema-drift assertion lives in test_chatbot_app_knowledge.py so
# CI catches it as a test failure, but a runtime guard means a misnamed
# entry in this module also fails fast during local development.
assert set(APP_KNOWLEDGE.keys()) == set(ALL_FEATURE_KEYS), (
    f"APP_KNOWLEDGE keys must match ALL_FEATURE_KEYS exactly; "
    f"missing={set(ALL_FEATURE_KEYS) - set(APP_KNOWLEDGE.keys())} "
    f"extra={set(APP_KNOWLEDGE.keys()) - set(ALL_FEATURE_KEYS)}"
)


# Stable label list for injection into ``DOXIE_SYSTEM_PROMPT`` — the
# always-on prompt needs the catalog but not the full prose. Sorted so
# regenerating the prompt is byte-stable.
FEATURE_LABELS_FOR_PROMPT: tuple[str, ...] = tuple(
    sorted(entry.label for entry in APP_KNOWLEDGE.values())
)


def _score(needle: str, entry: AppFeatureHelp) -> int:
    """Score how well one registry entry matches a user-supplied topic.

    Intentionally simple — substring + synonym checks. The tool returns
    the top-N entries by score, so absolute scale doesn't matter; the
    relative ordering does. Reasoning:

    - Exact label / permission-key matches dominate (score 100) — a user
      typing "Vault" wants the Vault, not the Settings page that mentions
      it in passing.
    - Exact synonym matches come next (25) so domain jargon like "Form 4"
      or "13F" routes cleanly.
    - Substring matches on label / synonyms get medium weight (12-30).
    - Summary / what_to_do_here substring is a tiebreaker only (2-3) so
      noisy multi-word phrases don't flood every entry with a stray match.
    """
    if not needle:
        return 0
    label_l = entry.label.lower()
    if needle == label_l or needle == entry.permission_key:
        return 100
    score = 0
    if needle in label_l:
        score += 30
    for syn in entry.synonyms:
        syn_l = syn.lower()
        if needle == syn_l:
            score += 25
        elif needle in syn_l or syn_l in needle:
            score += 12
    if needle in entry.route.lower():
        score += 8
    if needle in entry.summary.lower():
        score += 3
    if needle in entry.what_to_do_here.lower():
        score += 2
    return score


def find_topics(
    topic: str, *, max_results: int = 3
) -> list[tuple[str, AppFeatureHelp]]:
    """Return the top ``max_results`` registry entries matching ``topic``.

    Empty / whitespace-only topics return an empty list. Ties are broken
    alphabetically by feature key so the output is deterministic across
    runs (important for the cache key on the chat side).
    """
    needle = (topic or "").strip().lower()
    if not needle:
        return []
    scored: list[tuple[int, str, AppFeatureHelp]] = []
    for key, entry in APP_KNOWLEDGE.items():
        score = _score(needle, entry)
        if score > 0:
            scored.append((score, key, entry))
    if not scored:
        return []
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [(key, entry) for _, key, entry in scored[:max_results]]


def list_all_features() -> list[tuple[str, AppFeatureHelp]]:
    """Return every (key, entry) pair in alphabetical order of key.

    The tool calls this as a fallback when ``find_topics`` returns nothing
    so Doxie can still offer a useful "here's everything you can do"
    catalog instead of failing the lookup.
    """
    return [(key, APP_KNOWLEDGE[key]) for key in sorted(APP_KNOWLEDGE)]
