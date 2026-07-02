"""Feature-permission keys for per-user list-feature gating.

Single source of truth for which feature keys exist. The frontend mirror
in ``frontend/lib/feature-permissions.ts`` must stay name-for-name in sync
so a grep matches across stacks.

Admins implicitly bypass every gate (see ``services.auth.ensure_feature``).
Only viewer accounts are filtered by this set.
"""

from __future__ import annotations

MASTER_LIST = "master_list"
BANKS = "banks"
INVESTMENT_ADVISORS = "investment_advisors"
INVESTORS = "investors"
INSTITUTIONAL_INVESTORS = "institutional_investors"
ALERTS = "alerts"
EMAIL_EXTRACTOR = "email_extractor"
SENT_OUTREACH = "sent_outreach"
OUTREACH_CONTACTS = "outreach_contacts"
MY_FAVORITES = "my_favorites"
VISITED_FIRMS = "visited_firms"
DASHBOARD = "dashboard"
SETTINGS = "settings"
USERS = "users"
VAULT = "vault"

ALL_FEATURE_KEYS: frozenset[str] = frozenset(
    {
        MASTER_LIST,
        BANKS,
        INVESTMENT_ADVISORS,
        INVESTORS,
        INSTITUTIONAL_INVESTORS,
        ALERTS,
        EMAIL_EXTRACTOR,
        SENT_OUTREACH,
        OUTREACH_CONTACTS,
        MY_FAVORITES,
        VISITED_FIRMS,
        DASHBOARD,
        SETTINGS,
        USERS,
        VAULT,
    }
)
