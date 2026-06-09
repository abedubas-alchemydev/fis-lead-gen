"""Name+domain forward-lookup enrichment provider -- the chain's coverage net.

Every other link is a reverse-*email* lookup ("who owns this address?"). When
the brokers hold no record for that exact address the row comes back empty even
though the person sits in their index under a different address -- and partial
rows (name + LinkedIn but no phone) are the same story for the missing channel.
This link asks the other question: it derives the person's name from the email
local-part (``jonathan.lemco@vanguard.com`` -> "Jonathan Lemco"), then runs the
brokers' *name + domain* person-match. That's a different index path, so it hits
where the email path missed and fills the phone / alternate email left empty.

It reuses the tested ``contact_discovery`` person-match providers (Apollo, PDL,
Hunter) rather than re-implementing them -- the same way ``web_scraper`` reuses
``contact_discovery``'s web fallback. Snov is intentionally left out: its
name->email finder is an async poll (slow) and only yields a work email we
usually already have from the discovered address.

Placement + cost: it runs AFTER the reverse-email links, so the orchestrator's
``needed`` set means it only spends a (second, differently-keyed) broker call on
the gaps they left. It attaches the parsed name ONLY when a broker actually
corroborates the person -- never fabricated from the address alone -- and never
fires for a firm-wide inbox (``info@``, ``investor.relations@`` ...).
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.services.contact_discovery.apollo_match import ApolloMatchProvider
from app.services.contact_discovery.base import (
    ContactDiscoveryProvider,
    DiscoveryResult,
)
from app.services.contact_discovery.hunter import HunterProvider
from app.services.contact_discovery.pdl import PdlProvider
from app.services.contact_discovery.web_fallback import _GENERIC_LOCALS
from app.services.email_extractor.enrichment.base import (
    EmailEnrichmentProvider,
    EnrichmentData,
    EnrichmentError,
    brand_from_domain,
    split_name_from_local,
)

logger = logging.getLogger(__name__)

# Fields a name+domain person-match can contribute. Title/company live only in
# each broker's raw payload in provider-specific shapes; the reverse-email Apollo
# / PDL links already fill them on a match, so this link stays focused on the
# channels (plus the name parsed from the address).
_FILLABLE: frozenset[str] = frozenset({"name", "linkedin_url", "email", "phone"})


class NameLookupEnrichProvider(EmailEnrichmentProvider):
    name = "name_lookup"

    def is_configured(self) -> bool:
        # Configured when at least one underlying name+domain broker is keyed.
        return bool(
            settings.apollo_api_key or settings.pdl_api_key or settings.hunter_api_key
        )

    async def enrich(self, email: str, needed: set[str] | None = None) -> EnrichmentData | None:
        # Nothing this link can fill is still missing -> skip (no broker spend).
        if needed is not None and not (_FILLABLE & needed):
            return None

        local, _, domain = email.partition("@")
        domain = domain.strip().lower()
        if not domain or not local:
            return None

        first, last = split_name_from_local(local)
        if not first or not last:
            return None
        # A firm-wide inbox (info@, investor.relations@) belongs to no one person.
        if first in _GENERIC_LOCALS or last in _GENERIC_LOCALS:
            return None

        providers = _configured_providers()
        if not providers:
            return None

        org_name = brand_from_domain(domain)
        outcomes = await asyncio.gather(
            *(p.find_person(first, last, org_name, domain) for p in providers),
            return_exceptions=True,
        )

        hits: list[DiscoveryResult] = []
        errors = 0
        for outcome in outcomes:
            if isinstance(outcome, DiscoveryResult):
                hits.append(outcome)
            elif isinstance(outcome, BaseException):
                errors += 1
                logger.warning("name_lookup sub-provider failed for %s: %r", email, outcome)

        if not hits:
            # Every broker errored -> retryable (orchestrator marks the row error);
            # a clean all-miss -> None (never fabricate a name from the address).
            if errors and errors == len(providers):
                raise EnrichmentError("name_lookup: all sub-providers failed")
            return None

        # A broker corroborated the person, so the name parsed from their address
        # is safe to attach. Highest-confidence hit wins each contested channel.
        hits.sort(key=lambda hit: hit.confidence, reverse=True)
        data = EnrichmentData(name=f"{first.title()} {last.title()}")
        for hit in hits:
            if data.linkedin_url is None and hit.linkedin_url:
                data.linkedin_url = hit.linkedin_url
            if data.phone is None and hit.phone:
                data.phone = hit.phone
            if data.email is None:
                alt = _alt_email(hit, email)
                if alt:
                    data.email = alt
            if data.apollo_person_id is None and hit.apollo_person_id:
                data.apollo_person_id = hit.apollo_person_id
        return data


def _configured_providers() -> list[ContactDiscoveryProvider]:
    """Build the keyed name+domain brokers, in confidence order. Referenced via
    the module globals so tests can swap the classes for fakes."""
    providers: list[ContactDiscoveryProvider] = []
    if settings.apollo_api_key:
        providers.append(ApolloMatchProvider())
    if settings.pdl_api_key:
        providers.append(PdlProvider())
    if settings.hunter_api_key:
        providers.append(HunterProvider())
    return providers


def _alt_email(hit: DiscoveryResult, discovered: str) -> str | None:
    """A genuine alternate email from a broker hit -- personal preferred, and
    never the discovered address echoed back (it's already on the row)."""
    discovered_l = discovered.lower()
    personal = [
        h.value for h in hit.emails if h.type == "personal" and h.value.lower() != discovered_l
    ]
    if personal:
        return personal[0]
    other = [h.value for h in hit.emails if h.value.lower() != discovered_l]
    if other:
        return other[0]
    if hit.email and hit.email.lower() != discovered_l:
        return hit.email
    return None
