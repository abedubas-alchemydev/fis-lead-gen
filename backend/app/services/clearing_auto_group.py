"""Automatically group long-tail ``current_clearing_partner`` variants for
the master-list Clearing Arrangement filter.

This sits *on top of* the curated registry consolidation in
``clearing_consolidation`` and the fuzzy clustering in
``clearing_partner_clustering``. The goal: a firm that appears under a base
name and a longer variant ("Acme Securities" and "Acme Securities Trust
Co") collapses into ONE dropdown entry — labeled with the *shorter base
name* — and selecting it filters to BD rows for every underlying variant,
with no manual admin step.

Layering (a raw value lands in exactly one bucket):

* **Layer 0 — curated registry (authoritative).** Any raw that matches a
  ``CompetitorProvider`` (``is_unmatched`` is False) is keyed under that
  provider's display label and is NEVER auto-clustered.
* **Layer 1 — automatic clustering.** The ``is_unmatched`` remainder is run
  through the existing ``cluster_partners`` pass. Multi-member clusters are
  keyed under the shortest base name; clusters whose ``cluster_signature``
  an admin has *rejected* are skipped (they fall back to singletons — this
  is the human override). Everything still unclustered keeps its own
  trimmed text, exactly as before.

The same ``auto_group_long_tail`` map drives BOTH the dropdown listing
(``.keys()``) and the filter expansion (``[label]``), so the label a user
sees always expands back to exactly the raw values that produced it. This
is the load-bearing invariant — never derive labels for the two surfaces
independently.

Trade-off — false merges: a token-subset scores 100 in ``token_set_ratio``,
so a genuinely distinct firm whose name is a prefix of another ("Morgan
Stanley" vs "Morgan Stanley Smith Barney") WILL collapse under the shorter
label. This is accepted because the grouping is filter-only and fully
reversible: an admin can reject the cluster at /settings (it splits back
immediately) or curate either firm as a ``CompetitorProvider`` to pull it
out of the unmatched set for precise control.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import false as sql_false, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.clearing_partner_merge_suggestion import (
    ClearingPartnerMergeSuggestion,
)
from app.services.clearing_consolidation import (
    ProviderEntry,
    consolidate_partner,
    is_unmatched,
)
from app.services.clearing_partner_clustering import (
    _content_tokens,
    cluster_partners,
    ord_sum,
)


def _shortest_base_label(variants: list[str]) -> str:
    """Pick the variant most likely to read as the shared base name.

    Key: fewest *content* tokens (suffix words like LLC/INC are stripped by
    ``_content_tokens``), then shortest raw length, then a stable alpha
    tiebreak via ``ord_sum``. The raw-length term matters because
    ``_content_tokens`` makes "Acme Securities" and "Acme Securities LLC"
    tie on token count — raw length then correctly favors the shorter
    "Acme Securities".
    """

    return min(
        variants,
        key=lambda v: (len(_content_tokens(v)), len(v.strip()), ord_sum(v)),
    )


def auto_group_long_tail(
    distinct_raw_values: list[str | None],
    providers: list[ProviderEntry],
    rejected_signatures: frozenset[str] = frozenset(),
) -> dict[str, set[str]]:
    """Group distinct raw clearing-partner strings into display buckets.

    Returns ``{display_label: {original_raw, ...}}``. Bucket values are the
    ORIGINAL (un-trimmed) raw strings so the filter's ``IN (...)`` matches
    the exact stored column value even when extraction left surrounding
    whitespace. Labels are trimmed/canonical.
    """

    # Index trimmed text -> the original raw string(s) that produced it.
    # Skips empty / whitespace-only inputs. Two stored values that differ
    # only by surrounding whitespace share one trimmed key but keep both
    # originals in the bucket.
    trimmed_to_originals: dict[str, set[str]] = {}
    for raw in distinct_raw_values:
        if not raw:
            continue
        cleaned = raw.strip()
        if not cleaned:
            continue
        trimmed_to_originals.setdefault(cleaned, set()).add(raw)

    groups: dict[str, set[str]] = {}

    def add_members(label: str, trimmed_members: Iterable[str]) -> None:
        bucket = groups.setdefault(label, set())
        for member in trimmed_members:
            # Fall back to the member itself if it isn't an indexed trimmed
            # key (defensive; cluster variants always come from the index).
            bucket.update(trimmed_to_originals.get(member, {member}))

    # Layer 0: registry-matched raws key under their provider display label;
    # the rest become the long tail to auto-cluster.
    unmatched_trimmed: list[str] = []
    for cleaned in trimmed_to_originals:
        if is_unmatched(cleaned, providers):
            unmatched_trimmed.append(cleaned)
            continue
        label = consolidate_partner(cleaned, providers)
        if label:  # always truthy here (cleaned is non-empty and matched)
            add_members(label, [cleaned])

    # Layer 1: fuzzy-cluster the long tail (default threshold 80, block cap
    # 50 — same pass the admin "Run scan" uses, so signatures align).
    clustered: set[str] = set()
    for candidate in cluster_partners(unmatched_trimmed):
        if candidate.signature in rejected_signatures:
            continue  # admin-suppressed -> handled as singletons below
        label = _shortest_base_label(list(candidate.variants))
        add_members(label, candidate.variants)
        clustered.update(candidate.variants)

    # Whatever the cluster pass didn't group (singletons, oversized-block
    # fallbacks, rejected clusters) keeps its own trimmed text.
    for cleaned in unmatched_trimmed:
        if cleaned in clustered:
            continue
        add_members(cleaned, [cleaned])

    return groups


def matching_raw_values(
    selected_labels: list[str],
    groups: dict[str, set[str]],
    distinct_raw_values: list[str | None],
) -> set[str]:
    """Raw column values the selected labels expand to, given a group map.

    Pure (no SQLAlchemy) so it can be asserted on directly. Each selected
    label contributes its whole group's members; a label that is a group
    *member* but not a *key* (a longer variant folded under a base label,
    typically only reachable via a legacy URL) still matches its own rows.
    """

    wanted = {label.strip() for label in selected_labels if label and label.strip()}
    matching: set[str] = set()
    for label in wanted:
        members = groups.get(label)
        if members:
            matching.update(members)
    present_raw = {raw for raw in distinct_raw_values if raw}
    matching.update(label for label in wanted if label in present_raw)
    return matching


def expand_auto_group_predicate(
    selected_labels: list[str],
    providers: list[ProviderEntry],
    raw_column: ColumnElement[str | None],
    distinct_raw_values: list[str | None],
    rejected_signatures: frozenset[str] = frozenset(),
) -> ColumnElement[bool] | None:
    """WHERE clause matching every BD whose raw partner falls in one of the
    selected groups.

    Reuses the SAME ``auto_group_long_tail`` map the dropdown renders, so
    the selection semantics can't drift from what the user saw. Returns
    ``None`` for an empty selection (caller skips the filter) and
    ``sql_false()`` when a selection matches nothing (empty result rather
    than a silently-skipped filter).
    """

    if not any(label and label.strip() for label in selected_labels):
        return None

    groups = auto_group_long_tail(distinct_raw_values, providers, rejected_signatures)
    matching = matching_raw_values(selected_labels, groups, distinct_raw_values)

    if not matching:
        return sql_false()
    return raw_column.in_(matching)


async def load_rejected_signatures(db: AsyncSession) -> frozenset[str]:
    """Cluster signatures an admin rejected — excluded from auto-grouping."""

    stmt = select(ClearingPartnerMergeSuggestion.cluster_signature).where(
        ClearingPartnerMergeSuggestion.status == "rejected"
    )
    rows = (await db.execute(stmt)).scalars().all()
    return frozenset(signature for signature in rows if signature)
