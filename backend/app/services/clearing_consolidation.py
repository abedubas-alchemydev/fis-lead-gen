"""Map raw `current_clearing_partner` strings to canonical display labels.

The master-list filter dropdown reads distinct
``broker_dealers.current_clearing_partner`` values, which arrive as raw
extracted text — "PERSHING LLC", "PERSHING NFS", "BNY PERSHING" all
appear as separate rows for what is effectively one firm. This module
consolidates those variants by matching against the curated
``CompetitorProvider`` registry (canonical name + aliases), returning a
short ``display_name`` label per group.

Matching mirrors ``BrokerDealerRepository.match_competitor`` (whole-word
regex, case-insensitive, multi-word aliases compile to ``\\s+`` between
tokens) so the dropdown grouping behaves the same as the competitor flag
already exposed in detail responses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import false as sql_false, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.competitor_provider import CompetitorProvider


@dataclass(frozen=True)
class ProviderEntry:
    """Snapshot of one ``CompetitorProvider`` row used by the consolidator."""

    canonical_name: str
    display_label: str
    aliases: tuple[str, ...]


async def load_providers(db: AsyncSession) -> list[ProviderEntry]:
    """Fetch active providers ordered by priority, packaged for matching."""

    stmt = (
        select(CompetitorProvider)
        .where(CompetitorProvider.is_active.is_(True))
        .order_by(CompetitorProvider.priority.asc(), CompetitorProvider.name.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        ProviderEntry(
            canonical_name=row.name,
            display_label=row.display_name or row.name,
            aliases=tuple(row.aliases or ()),
        )
        for row in rows
    ]


@lru_cache(maxsize=512)
def _whole_word_pattern(candidate: str) -> re.Pattern[str]:
    """Compile a case-insensitive whole-word regex for `candidate`.

    Whitespace inside the candidate becomes ``\\s+`` so " BNY  Pershing "
    and " BNY Pershing " both match.
    """

    tokens = candidate.split()
    body = r"\s+".join(re.escape(token) for token in tokens)
    return re.compile(rf"\b{body}\b", re.IGNORECASE)


def _match_provider(raw: str, providers: list[ProviderEntry]) -> ProviderEntry | None:
    for provider in providers:
        for candidate in (provider.canonical_name, *provider.aliases):
            if candidate and _whole_word_pattern(candidate).search(raw):
                return provider
    return None


def is_unmatched(raw: str | None, providers: list[ProviderEntry]) -> bool:
    """True iff `raw` is non-empty and matches no provider in `providers`.

    Companion to ``consolidate_partner`` for callers that need to know
    whether a value would fall into the "long tail" branch (returned as
    its own raw text). The clearing-partner clustering pass uses this to
    skip values that the registry already merges so re-runs don't keep
    re-suggesting clusters that admin already covered with explicit
    aliases.
    """

    if not raw:
        return False
    cleaned = raw.strip()
    if not cleaned:
        return False
    return _match_provider(cleaned, providers) is None


def consolidate_partner(
    raw: str | None,
    providers: list[ProviderEntry],
) -> str | None:
    """Return the canonical display label for `raw`, or `raw` (trimmed) if
    no provider matches. Empty / whitespace-only inputs yield ``None``.
    """

    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    matched = _match_provider(cleaned, providers)
    if matched is not None:
        return matched.display_label
    return cleaned


def consolidated_label_set(
    raw_values: list[str | None],
    providers: list[ProviderEntry],
) -> list[str]:
    """Apply `consolidate_partner` across `raw_values`, dedup, sort.

    Used by the dropdown endpoint to render one entry per canonical
    firm. Long-tail raw values that don't match a provider are kept as
    their (trimmed) text.
    """

    seen: set[str] = set()
    for raw in raw_values:
        label = consolidate_partner(raw, providers)
        if label:
            seen.add(label)
    return sorted(seen, key=lambda s: s.lower())


def expand_filter_predicate(
    selected_labels: list[str],
    providers: list[ProviderEntry],
    raw_column: ColumnElement[str | None],
    distinct_raw_values: list[str | None],
) -> ColumnElement[bool] | None:
    """Build the WHERE clause that matches every BD whose raw clearing
    partner consolidates to one of `selected_labels`.

    Returns ``None`` when `selected_labels` is empty so callers can skip
    the filter entirely. The semantic equivalence with the dropdown is
    enforced by reusing ``consolidate_partner`` against the database's
    actual distinct raw values, then handing the resulting raw set to
    SQLAlchemy's ``IN`` clause. This avoids the false-positive risk of
    naive ``ILIKE %alias%`` SQL (which would match e.g. "Pershington").
    """

    if not selected_labels:
        return None

    wanted = {label.strip() for label in selected_labels if label and label.strip()}
    if not wanted:
        return None

    matching_raw: set[str] = set()
    for raw in distinct_raw_values:
        if not raw:
            continue
        # Primary path: select-from-dropdown sends a canonical label like
        # "Pershing"; the raw "PERSHING LLC" / "BNY PERSHING" rows that
        # consolidate to it all match.
        label = consolidate_partner(raw, providers)
        if label and label in wanted:
            matching_raw.add(raw)
        # Compatibility path: legacy URLs (e.g. dashboard
        # provider-distribution chart rows that push the un-consolidated
        # provider string) carry a raw value as the filter param. Keep
        # them working by accepting an exact raw-vs-label match too.
        if raw in wanted:
            matching_raw.add(raw)

    if not matching_raw:
        # No raw value consolidates to (or exactly matches) any of the
        # selected labels. Return a literal false so the caller sees an
        # empty result rather than mistakenly skipping the filter.
        return sql_false()

    return raw_column.in_(matching_raw)
