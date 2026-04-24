"""Abstract base for contact discovery providers.

Each provider (Apollo match, Hunter, Snov) owns its own HTTP shape but
speaks a common language in and out of the orchestrator:

* ``DiscoveryEntity`` -- the thing we're trying to find. Either a specific
  person (``type="person"``, ``first_name`` + ``last_name`` set) or a
  whole organisation (``type="organization"``, ``org_name`` set). A
  ``domain`` is always passed when known; providers that support it use
  it as the primary anchor for higher-quality matches.

* ``DiscoveryResult`` -- what a provider returns on a hit. ``confidence``
  is 0..100 (matching Hunter / Apollo's own semantics). ``raw`` preserves
  the provider's native payload for downstream logging / debugging.

A provider returning ``None`` means "no confident match at all" and the
orchestrator moves on to the next provider in the chain. A provider
raising is caught by the orchestrator and treated as a miss so one bad
provider can't block the rest of the fan-out.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal


EntityType = Literal["person", "organization"]


@dataclass(frozen=True)
class DiscoveryEntity:
    """The thing we're asking a provider to find."""

    type: EntityType
    org_name: str
    domain: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None


@dataclass
class DiscoveryResult:
    """One provider's successful hit, normalised for the orchestrator."""

    email: str | None
    phone: str | None
    linkedin_url: str | None
    confidence: float
    provider: str
    raw: dict[str, Any]


class ContactDiscoveryProvider(ABC):
    """Abstract provider. Implementations are stateless and HTTP-backed."""

    #: Short identifier persisted to ``executive_contacts.discovery_source``
    #: when this provider's result wins the chain.
    name: str

    @abstractmethod
    async def find_person(
        self,
        first_name: str,
        last_name: str,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        """Return a single-person match or ``None`` if no confident hit."""

    @abstractmethod
    async def find_org(
        self,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        """Return an org-level contact (public inbox or org profile) or ``None``."""
