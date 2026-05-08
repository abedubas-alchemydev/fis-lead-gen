"""Cluster long-tail unmatched ``current_clearing_partner`` variants.

The Clearing Arrangement filter on /master-list shows raw FOCUS-extracted
strings for any partner not in the curated ``competitor_providers``
registry — so one firm with multiple legal-suffix variants ("RBC Capital
Markets, LLC", "RBC Capital Markets LLC", "RBC Capital Markets Corp")
appears as several dropdown entries. This module groups those variants
by similarity and persists each cluster as a pending suggestion at
/settings, where admin can accept (creates a ``CompetitorProvider`` with
the cluster as its alias list) or reject.

Algorithm:

1. Pull distinct raw partners; drop values that already consolidate via
   the existing registry (those are already merged at the dropdown
   layer).
2. Block by first content token after stripping common suffixes
   (LLC, INC, CORP, LTD, LP, THE) — keeps comparison cost roughly
   ``Σ block_i²`` instead of ``n²``. Blocks larger than
   ``MAX_BLOCK_SIZE`` (50) are skipped with a warning so a degenerate
   first token can't pin the pass.
3. Pre-normalize each value by stripping ", a/an/the … of …"
   subordinate clauses before scoring. FOCUS-extracted strings often
   include corporate-disclosure subtitles ("RBC Clearing & Custody,
   a Division of RBC Capital") that are noise for clustering — they
   describe the parent, not the entity. Stripping them lets a plain
   token_set_ratio do the right thing without false positives from
   ``WRatio``'s partial-match component (which would otherwise bridge
   "RBC Capital Markets" to a Custody string just because the parent
   parenthetical mentions "RBC Capital").
4. Within each block, ``rapidfuzz.fuzz.token_set_ratio`` over every
   pair of normalized values. Threshold default 85 — at this level
   "RBC Capital Markets, LLC" / "RBC Capital Markets LLC" / "RBC
   Capital Markets Corp" cluster together, the two RBC Clearing &
   Custody variants cluster together, and the two families stay
   distinct (cross-family scores ≤44).
4. Union-find clusters at the threshold, drop singletons. Each cluster's
   ``cluster_signature`` is sha256 of its sorted variants joined by
   newline; this is the dedup key when persisting so a re-run never
   produces duplicate pending rows and a previously rejected cluster
   never resurfaces unchanged.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer
from app.models.clearing_partner_merge_suggestion import (
    ClearingPartnerMergeSuggestion,
)
from app.services.clearing_consolidation import (
    is_unmatched,
    load_providers as load_consolidation_providers,
)


logger = logging.getLogger(__name__)


DEFAULT_THRESHOLD = 85
MAX_BLOCK_SIZE = 50

# Strips corporate-subtitle clauses like ", a Division of …",
# ", an Affiliate of …", ", the wholly-owned subsidiary of …". The
# pattern requires an article (a/an/the) immediately after the comma so
# legitimate suffix conjunctions like "Acme Securities, Inc." or
# "Acme Securities, LLC" are NOT stripped.
_SUBTITLE_PATTERN: re.Pattern[str] = re.compile(
    r",\s+(?:a|an|the)\s+.+$", re.IGNORECASE
)

# Suffixes that should be ignored when picking a blocking token AND when
# scoring the "most informative" candidate name. Lowercased; matched as
# whole tokens, not substrings (so "INCORPORATED" doesn't get clipped to
# "INC").
_SUFFIX_TOKENS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "&",
        "co",
        "co.",
        "llc",
        "llc.",
        "l.l.c.",
        "l.l.c",
        "inc",
        "inc.",
        "incorporated",
        "corp",
        "corp.",
        "corporation",
        "ltd",
        "ltd.",
        "limited",
        "lp",
        "l.p.",
        "llp",
        "l.l.p.",
        "company",
        "plc",
    }
)


@dataclass(frozen=True, slots=True)
class ClusterCandidate:
    variants: tuple[str, ...]
    suggested_name: str
    min_score: int

    @property
    def signature(self) -> str:
        body = "\n".join(self.variants)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _strip_corporate_subtitle(value: str) -> str:
    """Remove a trailing ", a/an/the … of …" subordinate clause if any.

    See ``_SUBTITLE_PATTERN`` for the precise regex; the article gate
    keeps "Acme Securities, Inc." (no article) intact while stripping
    "Acme Securities, a Division of Big Bank".
    """

    return _SUBTITLE_PATTERN.sub("", value).strip()


def _score(a: str, b: str) -> int:
    """Pairwise similarity used for clustering — normalized + scored.

    Centralized so the normalization pre-pass is impossible to forget
    at any call site. Returns an int 0..100.
    """

    return int(
        fuzz.token_set_ratio(
            _strip_corporate_subtitle(a),
            _strip_corporate_subtitle(b),
        )
    )


def _normalize_token(token: str) -> str:
    return token.strip().lower()


def _content_tokens(value: str) -> list[str]:
    """Split on whitespace; lowercase; drop suffix words and empty tokens."""

    return [
        t
        for t in (_normalize_token(part) for part in value.split())
        if t and t not in _SUFFIX_TOKENS
    ]


def _blocking_key(value: str) -> str:
    """First content token (suffix-stripped), lowercased.

    Empty string when the entire value is suffixes — those values are
    impossible to cluster usefully and get their own block which is then
    skipped anyway since clusters need ≥2 members.
    """

    tokens = _content_tokens(value)
    return tokens[0] if tokens else ""


def _suggested_name(variants: list[str]) -> str:
    """Pick the variant with the most content tokens; ties → alphabetical.

    Picks the variant most likely to read as a clean canonical name —
    something like "RBC Capital Markets Corporation" beats "RBC Capital
    Markets, LLC" because Corp/LLC are stripped during scoring but
    "Capital" / "Markets" / "Corporation" / "Corp" all count toward the
    raw token total before suffix removal. The admin can rename on
    accept either way.
    """

    return max(
        variants,
        key=lambda v: (len(_content_tokens(v)), -ord_sum(v)),
    )


def ord_sum(text: str) -> int:
    """Stable tiebreaker: lower ord_sum → earlier alphabetically."""

    return sum(ord(c) for c in text.lower())


def _union_find_clusters(
    items: list[str],
    threshold: int,
) -> tuple[list[list[str]], dict[frozenset[str], int]]:
    """Cluster ``items`` by ``token_set_ratio >= threshold``.

    Returns (clusters, min_pair_score_by_member_set). Singletons are
    dropped from clusters but their indices are still consumed.
    """

    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    # Track min pairwise score within each (proto-)cluster so we can
    # surface a confidence indicator in the admin UI. Keyed by the
    # eventual cluster's frozenset of members — built up as we go.
    pair_scores: dict[tuple[int, int], int] = {}
    for i in range(n):
        for j in range(i + 1, n):
            score = _score(items[i], items[j])
            if score >= threshold:
                union(i, j)
                pair_scores[(i, j)] = score

    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(i)

    clusters: list[list[str]] = []
    min_scores: dict[frozenset[str], int] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        member_strs = [items[i] for i in members]
        clusters.append(member_strs)
        # Min pairwise score within the connected component. Pairs that
        # didn't directly meet the threshold (transitive merges) get
        # filled in by recomputing on demand.
        scores: list[int] = []
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                pair = (members[a], members[b]) if members[a] < members[b] else (members[b], members[a])
                if pair in pair_scores:
                    scores.append(pair_scores[pair])
                else:
                    scores.append(_score(items[members[a]], items[members[b]]))
        min_scores[frozenset(member_strs)] = min(scores) if scores else 0

    return clusters, min_scores


async def _distinct_unmatched_partners(db: AsyncSession) -> list[str]:
    """Distinct raw current_clearing_partner values that DON'T already
    consolidate to a registry display label."""

    stmt = (
        select(BrokerDealer.current_clearing_partner)
        .where(BrokerDealer.current_clearing_partner.is_not(None))
        .distinct()
    )
    rows = (await db.execute(stmt)).scalars().all()
    providers = await load_consolidation_providers(db)

    unmatched: list[str] = []
    for raw in rows:
        if is_unmatched(raw, providers):
            unmatched.append(raw.strip())  # type: ignore[union-attr]
    return unmatched


def cluster_partners(
    values: list[str],
    *,
    threshold: int = DEFAULT_THRESHOLD,
    max_block_size: int = MAX_BLOCK_SIZE,
) -> list[ClusterCandidate]:
    """Pure clustering — no DB. Public so tests can pin behavior on
    fixed inputs without spinning up a session.
    """

    blocks: dict[str, list[str]] = {}
    for value in values:
        key = _blocking_key(value)
        if not key:
            continue
        blocks.setdefault(key, []).append(value)

    candidates: list[ClusterCandidate] = []
    for key, block in blocks.items():
        if len(block) > max_block_size:
            logger.warning(
                "clearing_partner_clustering: block %r has %d members; "
                "skipping (cap=%d). Likely a degenerate first token.",
                key,
                len(block),
                max_block_size,
            )
            continue
        if len(block) < 2:
            continue
        clusters, min_scores = _union_find_clusters(block, threshold)
        for cluster in clusters:
            sorted_variants = tuple(sorted(cluster))
            candidates.append(
                ClusterCandidate(
                    variants=sorted_variants,
                    suggested_name=_suggested_name(list(sorted_variants)),
                    min_score=min_scores[frozenset(sorted_variants)],
                )
            )
    candidates.sort(key=lambda c: (-c.min_score, c.suggested_name.lower()))
    return candidates


async def cluster_unmatched_partners(
    db: AsyncSession,
    *,
    threshold: int = DEFAULT_THRESHOLD,
) -> list[ClusterCandidate]:
    values = await _distinct_unmatched_partners(db)
    return cluster_partners(values, threshold=threshold)


async def run_clustering_pass(
    db: AsyncSession,
    *,
    threshold: int = DEFAULT_THRESHOLD,
) -> int:
    """Run the clustering pass and persist NEW pending suggestions.

    Idempotent — clusters whose ``cluster_signature`` already exists in
    any status (pending, accepted, rejected) are skipped. Returns the
    count of newly-inserted pending rows. Caller is responsible for
    committing the session.
    """

    candidates = await cluster_unmatched_partners(db, threshold=threshold)
    if not candidates:
        return 0

    existing_sigs = set(
        (
            await db.execute(
                select(ClearingPartnerMergeSuggestion.cluster_signature)
            )
        )
        .scalars()
        .all()
    )

    new_count = 0
    for candidate in candidates:
        if candidate.signature in existing_sigs:
            continue
        suggestion = ClearingPartnerMergeSuggestion(
            cluster_signature=candidate.signature,
            variants=list(candidate.variants),
            suggested_name=candidate.suggested_name,
            min_score=candidate.min_score,
            status="pending",
        )
        db.add(suggestion)
        new_count += 1
    if new_count:
        await db.flush()
    return new_count
