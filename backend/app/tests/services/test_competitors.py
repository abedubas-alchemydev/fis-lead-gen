"""Unit tests for the competitor matching predicate.

Covers the word-boundary regex matcher in
``BrokerDealerRepository.match_competitor`` and the tightened
``DEFAULT_COMPETITORS`` seed list. Both changed in tandem to fix
sister-entity false positives such as "RBC Capital Markets" being
flagged as the actual competitor "RBC Correspondent Services".

These are pure unit tests — no DB, no async, no fixtures. The matcher
only reads ``competitor.name`` and ``competitor.aliases``, so a small
dataclass stand-in for ``CompetitorProvider`` is enough.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.services.broker_dealers import BrokerDealerRepository
from app.services.competitors import DEFAULT_COMPETITORS


@dataclass
class _FakeCompetitor:
    """Lightweight stand-in for a CompetitorProvider ORM row."""

    name: str
    aliases: list[str] = field(default_factory=list)


def _seeded_competitors() -> list[_FakeCompetitor]:
    """Return the in-memory equivalent of what ``seed_defaults`` would
    push into the ``competitor_providers`` table. The matcher under test
    consumes the same shape at runtime via ``list_competitor_providers``.
    """
    return [_FakeCompetitor(name=c["name"], aliases=list(c["aliases"])) for c in DEFAULT_COMPETITORS]


@pytest.fixture
def repository() -> BrokerDealerRepository:
    return BrokerDealerRepository()


@pytest.fixture
def competitors() -> list[_FakeCompetitor]:
    return _seeded_competitors()


# ---------------------------------------------------------------------------
# Positive cases — competitor presence must be detected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "partner",
    [
        "Pershing LLC",
        "Pershing, LLC",
        "BNY Pershing",
        "RBC Correspondent Services",
        "Apex Clearing Corporation",
        "Apex Clearing Corp.",
        "Hilltop Securities Inc.",
        "Axos Clearing LLC",
        "Vision Financial Markets LLC",
        "Goldman, Sachs & Co., Pershing LLC, Mirae Asset Securities (USA), Inc.",
    ],
)
def test_known_competitor_partners_match(repository, competitors, partner):
    assert repository.match_competitor(partner, competitors) is True


def test_match_is_case_insensitive(repository, competitors):
    assert repository.match_competitor("pershing llc", competitors) is True
    assert repository.match_competitor("APEX CLEARING CORPORATION", competitors) is True


def test_match_tolerates_extra_whitespace_inside_alias(repository, competitors):
    # "BNY Pershing" alias still fires when the partner string uses
    # multiple spaces between the words (e.g. extraction artifacts).
    assert repository.match_competitor("BNY  Pershing", competitors) is True


# ---------------------------------------------------------------------------
# Negative cases — sibling entities sharing a brand prefix must NOT match.
# This is the bug the PR is fixing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "partner",
    [
        # The DRIVEWEALTH / ELEVATION false positive from Phase 1 audit.
        "RBC Capital Markets, LLC, Wedbush Securities, Inc., ABN AMRO Clearing USA, LLC",
        "RBC Capital Markets, LLC",
        # Hypothetical sibling firms — must be safe under the new rule.
        "Apex Securities Inc",
        "Apex Capital Group",
        "Axos Bank",
        "Axos Securities",
        "Hilltop Holdings",
        "Vision One Securities",
        "Vision Brokerage",
        # Wholly unrelated firms.
        "Goldman, Sachs & Co.",
        "Wedbush Securities, Inc.",
    ],
)
def test_sibling_and_unrelated_partners_do_not_match(repository, competitors, partner):
    assert repository.match_competitor(partner, competitors) is False


# ---------------------------------------------------------------------------
# Edge cases — empty / null inputs preserve the prior False return.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("partner", [None, "", "   "])
def test_empty_or_null_partner_returns_false(repository, competitors, partner):
    assert repository.match_competitor(partner, competitors) is False


def test_empty_competitor_list_returns_false(repository):
    assert repository.match_competitor("Pershing LLC", []) is False


def test_competitor_with_empty_alias_does_not_crash(repository):
    competitor = _FakeCompetitor(name="Pershing LLC", aliases=["", "Pershing"])
    assert repository.match_competitor("Pershing LLC", [competitor]) is True
    assert repository.match_competitor("Goldman Sachs", [competitor]) is False


# ---------------------------------------------------------------------------
# Seed-list contract — the bare-prefix aliases known to collide with
# sibling brands have been removed. Pershing remains because no sibling
# brand exists in our universe.
# ---------------------------------------------------------------------------


def test_default_competitors_drop_sibling_collision_aliases():
    aliases_by_canonical = {entry["name"]: entry["aliases"] for entry in DEFAULT_COMPETITORS}

    # Pershing keeps its bare alias — no sibling brand to collide with.
    assert "Pershing" in aliases_by_canonical["Pershing LLC"]

    # All other bare-prefix aliases are gone.
    assert "RBC" not in aliases_by_canonical["RBC Correspondent Services"]
    assert "Apex" not in aliases_by_canonical["Apex Clearing Corporation"]
    assert "Hilltop" not in aliases_by_canonical["Hilltop Securities Inc."]
    assert "Axos" not in aliases_by_canonical["Axos Clearing LLC"]
    assert "Vision" not in aliases_by_canonical["Vision Financial Markets LLC"]
