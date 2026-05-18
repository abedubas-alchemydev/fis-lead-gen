"""Feature-permission keys for per-user list-feature gating.

Single source of truth for which feature keys exist. The frontend mirror
in ``frontend/lib/feature-permissions.ts`` must stay name-for-name in sync
so a grep matches across stacks.

Admins implicitly bypass every gate (see ``services.auth.ensure_feature``).
Only viewer accounts are filtered by this set.
"""

from __future__ import annotations

MASTER_LIST = "master_list"
INVESTMENT_ADVISORS = "investment_advisors"
INVESTORS = "investors"
INSTITUTIONAL_INVESTORS = "institutional_investors"

ALL_FEATURE_KEYS: frozenset[str] = frozenset(
    {
        MASTER_LIST,
        INVESTMENT_ADVISORS,
        INVESTORS,
        INSTITUTIONAL_INVESTORS,
    }
)
