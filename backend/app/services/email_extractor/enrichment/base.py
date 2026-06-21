"""Provider abstraction for reverse-email enrichment (email -> person).

The email-extractor "Enrich" button enriches a discovered email address with
the person behind it -- name, title, company, LinkedIn, an alternate email,
and a phone. This used to be Apollo-only; it now walks a configurable provider
chain (``settings.email_enrichment_chain``, default
``apollo,hunter,snov,name_lookup,web_scraper``) and *gap-fills*: the first provider that
returns a field wins it, and later providers fill only the fields still empty.
See ``orchestrator.py`` for the walk + merge.

Providers are pure and stateless: ``enrich(email)`` does one provider's HTTP
work and returns a normalised :class:`EnrichmentData` (or ``None`` on a clean
"no match"). Network / credential / payload failures raise
:class:`EnrichmentError`, which the orchestrator catches and treats as a
provider miss so one bad provider never aborts the chain.

Error-message sentinels (:data:`NOT_FOUND_MESSAGE`,
:data:`NOT_CONFIGURED_MESSAGE`) are the two ``EnrichmentError`` strings the API
layer maps to specific HTTP status codes; everything else is a generic 502.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# Fields the chain merges + uses to decide "fully enriched" (early-stop). The
# Apollo-only ``apollo_person_id`` correlation key is intentionally excluded --
# it's plumbing for the async phone-reveal webhook, not user-facing data.
MERGE_FIELDS: tuple[str, ...] = (
    "name",
    "title",
    "company",
    "linkedin_url",
    "email",
    "phone",
)

# ``EnrichmentError`` messages the endpoint special-cases. Kept as constants so
# the raiser (orchestrator) and the matcher (endpoint) can't drift.
NOT_FOUND_MESSAGE = "discovered_email not found"
NOT_CONFIGURED_MESSAGE = "no enrichment provider configured"


class EnrichmentError(Exception):
    """Bare error message wrapping an HTTP / config / payload failure.

    Named the same as the legacy Apollo-only error so existing callers /
    imports (``apollo_enrichment``) keep working through the shim.
    """


@dataclass
class EnrichmentData:
    """One provider's normalised hit, or the merged result across the chain.

    Every field is optional: a provider populates what it can and leaves the
    rest ``None``. ``apollo_person_id`` is Apollo-only -- stamped onto the row
    so the async phone-reveal webhook can correlate a revealed number back to
    it (see ``endpoints/webhooks_apollo.py``).
    """

    name: str | None = None
    title: str | None = None
    company: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    phone: str | None = None
    apollo_person_id: str | None = None

    def has_any(self) -> bool:
        """True when at least one user-facing field is populated (i.e. this is a
        real match worth marking ``enriched``)."""
        return any(getattr(self, field) is not None for field in MERGE_FIELDS)

    def is_complete(self) -> bool:
        """True when every mergeable field is filled -- the orchestrator can
        stop walking the chain early (nothing left to gap-fill)."""
        return all(getattr(self, field) is not None for field in MERGE_FIELDS)


class EmailEnrichmentProvider(ABC):
    """A reverse-email enrichment provider. Stateless and HTTP-backed."""

    #: Short identifier used in ``settings.email_enrichment_chain`` and logs.
    name: str

    @abstractmethod
    def is_configured(self) -> bool:
        """True when this provider has the credentials / flags it needs to run.

        Unconfigured providers are *skipped* by the orchestrator (not counted
        as a miss), mirroring how the contact-discovery chain handles a
        missing key.
        """

    @abstractmethod
    async def enrich(self, email: str, needed: set[str] | None = None) -> EnrichmentData | None:
        """Reverse-lookup ``email`` -> person.

        ``needed`` is the set of :data:`MERGE_FIELDS` the chain still hasn't
        filled (``None`` = treat everything as needed, e.g. a direct call).
        One-shot API providers ignore it -- a single call returns everything
        and the orchestrator keeps only the gaps. Providers that do *expensive,
        separable* work (the web scraper's per-field crawl + LinkedIn search)
        use it to skip sub-steps whose output would just be discarded -- which
        also keeps them off scarce shared quota (SerpAPI).

        ``None`` means a clean "no match" (the provider ran fine, found
        nothing). Raise :class:`EnrichmentError` on a network / credential /
        payload failure so the orchestrator can tell a retryable error apart
        from an honest miss.
        """


def clean_str(value: Any) -> str | None:
    """Return a stripped non-empty string, else ``None``.

    Shared by the HTTP providers to coerce arbitrary JSON values (which may be
    ``None``, numbers, or empty strings) into a clean optional string.
    """
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def split_name_from_local(local_part: str) -> tuple[str | None, str | None]:
    """Split an email local-part into ``(first, last)`` when it cleanly encodes a
    two-token name (``jane.smith``, ``jane_smith``). Drops single-char initials
    so ``j.smith`` (ambiguous) is rejected rather than guessed. Shared by the
    web-scraper and name-lookup providers, which both reverse the address itself
    into a name."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", local_part.lower()) if t.isalpha() and len(t) >= 2]
    if len(tokens) < 2:
        return None, None
    return tokens[0], tokens[-1]


def brand_from_domain(domain: str) -> str:
    """The firm's brand label from its domain (``vanguard.com`` -> ``vanguard``),
    used as the org-affiliation signal for the LinkedIn / name+domain lookups."""
    label = domain.lstrip(".").split(".")[0]
    return label.replace("-", " ")
