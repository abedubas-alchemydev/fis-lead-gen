"""Web-scraper enrichment provider -- last-resort, no vendor credit.

Fourth and final link in the default chain. Where the paid providers reverse a
data broker's index, this reverses the *email itself*: it derives the person's
name from the local-part (``jonathan_lemco@vanguard.com`` -> "Jonathan Lemco"),
then reuses the in-house web fallback to (a) find their public LinkedIn URL via
a ``site:linkedin.com/in`` search and (b) crawl the firm's OWN public site for
a literal published email / a phone co-located with their name. It never guesses
addresses and never scrapes LinkedIn pages (auth-walled / ToS) -- same policy as
``contact_discovery/web_fallback.py``, whose tested machinery it delegates to.

Only attempts a person when the local-part cleanly splits into two name tokens
and isn't a firm-wide inbox (``info@``, ``investor.relations@`` ...). Gated on
``settings.web_fallback_enabled`` so it stays dormant (and makes no network
calls) until a deployment opts in. Soft-fails to a clean miss; a crawl/search
blow-up is surfaced as an :class:`EnrichmentError` for the orchestrator to log.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.services.contact_discovery.base import DiscoveryResult
from app.services.contact_discovery.linkedin_search import LinkedInSearchProvider
from app.services.contact_discovery.web_fallback import (
    _GENERIC_LOCALS,
    GapPerson,
    discover_web_fallback,
)
from app.services.email_extractor.base import DiscoveryResult as CrawlResult
from app.services.email_extractor.enrichment.base import (
    EmailEnrichmentProvider,
    EnrichmentData,
    EnrichmentError,
    brand_from_domain,
    split_name_from_local,
)
from app.services.email_extractor.site_crawler import SiteCrawler

logger = logging.getLogger(__name__)


class WebScraperEnrichProvider(EmailEnrichmentProvider):
    name = "web_scraper"

    def is_configured(self) -> bool:
        return bool(settings.web_fallback_enabled)

    async def enrich(self, email: str, needed: set[str] | None = None) -> EnrichmentData | None:
        if not settings.web_fallback_enabled:
            return None

        local, _, domain = email.partition("@")
        domain = domain.strip().lower()
        if not domain or not local:
            return None

        first, last = split_name_from_local(local)
        if not first or not last:
            return None
        # A firm-wide inbox (info@, investor.relations@) belongs to no one
        # person -- never attribute it to a named officer.
        if first in _GENERIC_LOCALS or last in _GENERIC_LOCALS:
            return None

        # Only run the sub-steps whose output the chain still needs. Crucially
        # this skips the SerpAPI-backed LinkedIn search when an upstream
        # provider already supplied the URL (Apollo covers most), keeping the
        # web scraper off scarce SerpAPI quota; likewise it skips the site
        # crawl when no crawl-sourced field (phone/email) is still missing.
        need_linkedin = needed is None or "linkedin_url" in needed
        need_channels = needed is None or bool({"phone", "email"} & needed)
        if not need_linkedin and not need_channels:
            return None

        org_name = brand_from_domain(domain)
        try:
            results = await discover_web_fallback(
                domain=domain,
                org_name=org_name,
                people=[GapPerson(row_id=0, first_name=first, last_name=last)],
                crawler=None if need_channels else _NULL_CRAWLER,
                linkedin=None if need_linkedin else _NULL_LINKEDIN,
            )
        except Exception as exc:  # defensive: crawler/search blow-up -> retryable
            raise EnrichmentError(f"web_scraper: {exc}") from exc

        result = results.get(0)
        if result is None:
            return None

        phone = result.phones[0].value if result.phones else None
        # A literal published address that differs from the discovered one is a
        # genuine alternate; the discovered address echoed back is not.
        alt_email = next(
            (hit.value for hit in result.emails if hit.value.lower() != email.lower()),
            None,
        )
        if not (result.linkedin_url or phone or alt_email):
            return None

        # We only reach here because the web actually surfaced a channel for
        # this person, so attaching the name parsed from their address is safe.
        return EnrichmentData(
            name=f"{first.title()} {last.title()}",
            linkedin_url=result.linkedin_url,
            email=alt_email,
            phone=phone,
        )


class _NullCrawler(SiteCrawler):
    """Skips the site crawl (returns an empty result) when no crawl-sourced
    field is still needed. Subclasses ``SiteCrawler`` so it's a drop-in for
    ``discover_web_fallback``'s typed ``crawler`` parameter."""

    async def run(self, domain: str) -> CrawlResult:
        return CrawlResult()


class _NullLinkedIn(LinkedInSearchProvider):
    """Skips the SerpAPI-backed LinkedIn search when the URL is already in hand.
    Subclasses the real provider to stay type-compatible with the ``linkedin``
    parameter."""

    async def find_person(
        self,
        first_name: str,
        last_name: str,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        return None


# Stateless stubs -- one shared instance each is enough.
_NULL_CRAWLER = _NullCrawler()
_NULL_LINKEDIN = _NullLinkedIn()
