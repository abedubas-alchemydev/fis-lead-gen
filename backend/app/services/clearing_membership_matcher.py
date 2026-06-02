"""Name-matching for clearing-agency / SRO membership ingestion.

Pure (no DB, no file I/O) so the importer script
(``scripts/import_clearing_agency_memberships.py``) and the test suite
share one matching implementation. Directory member names are matched to
firm records by *normalized* name (reusing ``normalize_entity_name``,
which lowercases, strips punctuation, and drops corporate noise tokens
like "inc"/"llc"), considering a firm's legal name plus — for
broker-dealers — its DBA names and resolver aliases.

The OCC/DTCC directories expose firm names + their own member numbers but
no CRD, so name is the only join key. To bound false positives: a
normalized key that maps to exactly one firm auto-applies (``active``); a
key shared by multiple distinct firms is ambiguous and every candidate is
routed to ``needs_review`` rather than silently applied.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.normalization import normalize_entity_name


# Auto-apply confidence per match method (the firm name a key matched on).
CONFIDENCE_BY_METHOD: dict[str, float] = {
    "exact_normalized": 100.0,
    "dba": 90.0,
    "alias": 85.0,
}
# Confidence stamped on ambiguous (>1 firm) matches routed to review.
AMBIGUOUS_CONFIDENCE = 60.0
# When one firm matches a key via several of its names, the strongest
# (most authoritative) method wins.
_METHOD_RANK: dict[str, int] = {"exact_normalized": 3, "dba": 2, "alias": 1}

# normalized-name key -> {firm_id: best_method_for_that_firm}
FirmIndex = dict[str, dict[int, str]]


@dataclass(frozen=True)
class MatchResult:
    firm_id: int
    method: str  # exact_normalized | dba | alias
    status: str  # active | needs_review
    confidence: float


def index_firm(index: FirmIndex, firm_id: int, named: list[tuple[str | None, str]]) -> None:
    """Add one firm's candidate names to ``index`` (mutates in place).

    ``named`` is a list of ``(raw_name, method)`` pairs — e.g. the firm's
    legal name as ``exact_normalized`` plus each DBA as ``dba`` and each
    resolver alias as ``alias``. Empty / noise-only names are skipped. If
    several of a firm's names normalize to the same key, the strongest
    method is kept for that firm under that key.
    """
    for raw, method in named:
        key = normalize_entity_name(raw)
        if not key:
            continue
        bucket = index.setdefault(key, {})
        existing = bucket.get(firm_id)
        if existing is None or _METHOD_RANK[method] > _METHOD_RANK[existing]:
            bucket[firm_id] = method


def match_name(index: FirmIndex, member_name: str | None) -> list[MatchResult]:
    """Match a directory member name against a firm index.

    Resolution prefers the *strongest matching method*: a directory listing
    "PERSHING LLC" should attach to the firm literally named Pershing LLC
    (``exact_normalized``), not to a firm that merely carries "Pershing" as a
    DBA or resolver alias. So:

    * 0 firms for the key -> ``[]`` (the firm isn't in this directory).
    * a single firm with the strongest method -> one ``active`` result.
    * a TIE at the strongest method (>=2 firms whose *same-strength* names
      collide) -> a ``needs_review`` result per tied firm; we genuinely can't
      tell which the directory meant. Firms that matched the key only via a
      weaker method are dropped — they're alias coincidences, not candidates.
    """
    key = normalize_entity_name(member_name)
    if not key:
        return []
    bucket = index.get(key)
    if not bucket:
        return []
    top_rank = max(_METHOD_RANK[method] for method in bucket.values())
    top = [(fid, method) for fid, method in bucket.items() if _METHOD_RANK[method] == top_rank]
    if len(top) == 1:
        firm_id, method = top[0]
        return [MatchResult(firm_id, method, "active", CONFIDENCE_BY_METHOD[method])]
    return [
        MatchResult(firm_id, method, "needs_review", AMBIGUOUS_CONFIDENCE)
        for firm_id, method in top
    ]
