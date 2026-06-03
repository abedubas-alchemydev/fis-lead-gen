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
class DiscoveredPhoneDraft:
    """In-memory representation of one phone number a provider found.

    Mirrors :class:`DiscoveredEmailDraft`. ``value`` is normalized to a canonical
    US format by the crawler so different spacings dedupe; ``attribution`` is the
    page URL it appeared on (used by the web-fallback composite to attach a phone
    to a person named on the same page).
    """

    value: str
    source: str
    confidence: float | None = None
    attribution: str | None = None


@dataclass
class PageText:
    """One fetched page's URL + normalized visible text.

    Populated only when a crawler runs with ``collect_page_text=True`` so a
    downstream consumer can do co-location (e.g. attach a phone to a person whose
    name appears on the same page). Off by default to avoid retaining page text.
    """

    url: str
    text: str


@dataclass
class DiscoveryResult:
    """Bundle of one provider run's emails plus any soft errors.

    Soft errors (timeouts, partial failures) go in ``errors`` and are written
    to ``ExtractionRun.error_message`` by the aggregator. A provider that
    *raises* is treated separately (the aggregator catches via task-group
    exception handling).

    ``phones`` / ``pages`` are appended after ``errors`` (existing callers
    construct by keyword, so position is irrelevant) and default empty, so the
    email-aggregator path is unaffected. They carry the web-fallback's optional
    phone capture + co-location text.
    """

    emails: list[DiscoveredEmailDraft] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    phones: list[DiscoveredPhoneDraft] = field(default_factory=list)
    pages: list[PageText] = field(default_factory=list)


@runtime_checkable
class EmailSource(Protocol):
    """Contract for any discovery provider."""

    name: str

    async def run(self, domain: str) -> DiscoveryResult: ...
