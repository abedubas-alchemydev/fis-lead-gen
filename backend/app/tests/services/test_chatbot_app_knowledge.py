"""Unit tests for ``app.services.chatbot_app_knowledge``.

Two responsibilities here:

1. **Schema-drift guard** — every constant in ``feature_permissions.py``
   must have a registry entry, and every registry entry must reference a
   real feature key. Adding a new feature without a help entry (or removing
   a feature without cleaning up here) fails this test.

2. **Search behaviour** — ``find_topics`` is a small scorer; we exercise
   the cases the tool layer relies on (exact label, exact synonym,
   substring synonym, blank input, deterministic ordering).
"""

from __future__ import annotations

from app.core import feature_permissions
from app.core.feature_permissions import (
    ALL_FEATURE_KEYS,
    DASHBOARD,
    INSTITUTIONAL_INVESTORS,
    INVESTORS,
    MASTER_LIST,
    USERS,
    VAULT,
)
from app.services.chatbot_app_knowledge import (
    APP_KNOWLEDGE,
    FEATURE_LABELS_FOR_PROMPT,
    AppFeatureHelp,
    find_topics,
    list_all_features,
)


# ── Drift guard ─────────────────────────────────────────────────────────


def test_registry_covers_every_feature_key() -> None:
    """Every feature constant gets exactly one help entry and vice versa."""
    assert set(APP_KNOWLEDGE.keys()) == set(ALL_FEATURE_KEYS)


def test_every_entry_has_required_non_empty_fields() -> None:
    """Catch the case where a new entry was added with empty prose."""
    for key, entry in APP_KNOWLEDGE.items():
        assert isinstance(entry, AppFeatureHelp), key
        assert entry.label.strip(), f"{key}: empty label"
        assert entry.route.startswith("/"), f"{key}: route must start with /"
        assert entry.summary.strip(), f"{key}: empty summary"
        assert entry.what_to_do_here.strip(), f"{key}: empty what_to_do_here"
        assert entry.permission_key == key, (
            f"{key}: permission_key on entry ({entry.permission_key!r}) "
            f"doesn't match its registry key"
        )
        assert isinstance(entry.synonyms, tuple)
        # At least one synonym so the substring matcher has something to
        # work with for the common domain-jargon case. Empty synonyms is
        # a sign of an under-populated entry.
        assert entry.synonyms, f"{key}: needs at least one synonym"


def test_users_entry_is_admin_only() -> None:
    """USERS is the only admin_only=True entry in the current registry."""
    assert APP_KNOWLEDGE[USERS].admin_only is True
    admin_only_keys = {k for k, e in APP_KNOWLEDGE.items() if e.admin_only}
    assert admin_only_keys == {USERS}


def test_feature_constants_module_only_exports_strings_and_set() -> None:
    """Spot-check the source of truth so a future tuple/dict refactor
    of feature_permissions.py doesn't silently break this drift test."""
    for key in ALL_FEATURE_KEYS:
        # Every entry in ALL_FEATURE_KEYS must be a module-level string
        # constant on feature_permissions.
        assert isinstance(key, str)
        const_name = key.upper()
        assert getattr(feature_permissions, const_name) == key


# ── Prompt-injection catalog ────────────────────────────────────────────


def test_feature_labels_for_prompt_is_alphabetical_and_complete() -> None:
    labels = list(FEATURE_LABELS_FOR_PROMPT)
    assert labels == sorted(labels)
    assert len(labels) == len(APP_KNOWLEDGE)
    # Every label maps back to exactly one entry.
    registry_labels = {entry.label for entry in APP_KNOWLEDGE.values()}
    assert set(labels) == registry_labels


# ── find_topics ─────────────────────────────────────────────────────────


def test_find_topics_exact_label_match_wins() -> None:
    matches = find_topics("Vault")
    assert matches
    key, entry = matches[0]
    assert key == VAULT
    assert entry.label == "Vault"


def test_find_topics_case_insensitive() -> None:
    matches = find_topics("MASTER LIST")
    assert matches
    assert matches[0][0] == MASTER_LIST


def test_find_topics_exact_synonym_routes_correctly() -> None:
    # "13F" is a synonym on INSTITUTIONAL_INVESTORS.
    matches = find_topics("13F")
    assert matches
    assert matches[0][0] == INSTITUTIONAL_INVESTORS


def test_find_topics_form_4_jargon_routes_to_investors() -> None:
    matches = find_topics("Form 4")
    assert matches
    assert matches[0][0] == INVESTORS


def test_find_topics_returns_empty_on_blank() -> None:
    assert find_topics("") == []
    assert find_topics("   ") == []


def test_find_topics_returns_empty_on_total_miss() -> None:
    # No feature mentions "quantum entanglement" in label, synonym,
    # route, or prose.
    assert find_topics("quantum entanglement") == []


def test_find_topics_respects_max_results_cap() -> None:
    # "form" is a broad-ish substring that hits multiple entries via
    # their synonyms (Form 4, Form ADV, Form BD, Form 13F, etc.).
    matches = find_topics("form", max_results=2)
    assert len(matches) <= 2


def test_find_topics_is_deterministic() -> None:
    """Same input → same output, ordering and all (the chat-layer cache
    key relies on this)."""
    first = find_topics("filings")
    second = find_topics("filings")
    assert [k for k, _ in first] == [k for k, _ in second]


def test_find_topics_dashboard_key_match() -> None:
    # The permission key itself ("dashboard") should match directly so
    # the chat layer can pass either label or key.
    matches = find_topics(DASHBOARD)
    assert matches
    assert matches[0][0] == DASHBOARD


# ── list_all_features ────────────────────────────────────────────────────


def test_list_all_features_is_complete_and_sorted_by_key() -> None:
    catalog = list_all_features()
    assert len(catalog) == len(APP_KNOWLEDGE)
    keys = [k for k, _ in catalog]
    assert keys == sorted(keys)
    assert set(keys) == set(APP_KNOWLEDGE.keys())


# ── Route allowlist sanity ──────────────────────────────────────────────


def test_every_route_is_in_internal_route_allowlist() -> None:
    """If a registry entry's route isn't in INTERNAL_ROUTE_PREFIXES, the
    FE will render the deep-link as an external (target=_blank) anchor —
    which would be wrong for an in-app help reply. Catch the mismatch at
    test time so adding a new feature without updating the allowlist
    fails CI rather than shipping a broken link."""
    from app.services.chatbot_urls import INTERNAL_ROUTE_PREFIXES

    for key, entry in APP_KNOWLEDGE.items():
        prefix = "/" + entry.route.lstrip("/").split("/", 1)[0]
        assert prefix in INTERNAL_ROUTE_PREFIXES, (
            f"{key}: route {entry.route!r} has prefix {prefix!r} which "
            f"is not in chatbot_urls.INTERNAL_ROUTE_PREFIXES — the FE "
            f"will render the link as external. Update both BE and FE "
            f"allowlists in lockstep."
        )
