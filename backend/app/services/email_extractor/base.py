"""Provider Protocol + drafts the aggregator merges into DB rows.

Each provider (site crawler, theHarvester, Hunter, Apollo, Snov) implements
``EmailSource`` and yields a ``DiscoveryResult``. The aggregator owns DB
persistence, dedupe, and verification — providers stay pure (no DB writes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class DiscoveredEmailDraft:
    """In-memory representation of one email a provider found.

    The aggregator turns drafts into ``DiscoveredEmail`` rows; provider code
    never imports ORM models.
    """

    email: str
    source: str
    confidence: float | None = None
    attribution: str | None = None


@dataclass
class DiscoveryResult:
    """Bundle of one provider run's emails plus any soft errors.

    Soft errors (timeouts, partial failures) go in ``errors`` and are written
    to ``ExtractionRun.error_message`` by the aggregator. A provider that
    *raises* is treated separately (the aggregator catches via task-group
    exception handling).
    """

    emails: list[DiscoveredEmailDraft] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@runtime_checkable
class EmailSource(Protocol):
    """Contract for any discovery provider."""

    name: str

    async def run(self, domain: str) -> DiscoveryResult: ...
