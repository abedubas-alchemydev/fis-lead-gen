"""LinkedIn-search provider: recover a person's LinkedIn profile URL via a
public Google search when the data-broker providers (PDL / Apollo / Hunter /
Snov) all came back without one.

Why this exists
---------------
The broker providers index people by their PRIMARY employer. For someone
whose target-firm role is secondary (an outside board director, an advisor
who sits on multiple boards), the firm-anchored match often returns no
``linkedin_url`` even though a public LinkedIn profile exists. A site-scoped
Google search (``site:linkedin.com/in``) finds those profiles. This is the
last link in ``contact_discovery_chain`` so it only contributes its LinkedIn
when every upstream provider missed (the merge takes first-non-null
``linkedin_url`` in chain order).

Precision over recall
----------------------
A naive ``"<name>" site:linkedin.com/in`` search is dangerous: common names
return many same-name strangers (the "Scott Malpass" case returned six
unrelated profiles — a bartender, a farmer, a Notre Dame admin, etc.).
Grafting the wrong person's profile onto a contact row is a data-quality
regression, so acceptance is tiered:

- **org-confirmed** (the firm's distinctive brand token appears in the
  result title or snippet, AND the person's name matches the profile) ->
  confidence 80. Strong: Google says this profile is affiliated with the
  firm we asked about.
- **unique unambiguous** (exactly one ``/in/`` profile in the results and
  its slug/title matches the full name) -> confidence 65. The single-result
  signal rules out the same-name-collision failure mode.
- **ambiguous** (multiple same-name profiles, none org-confirmed) ->
  rejected. We return ``None`` rather than guess.

Both confidence tiers clear the default ``contact_discovery_min_confidence``
(60) so the merge keeps them; tightening that floor disables the weaker tier
without code changes.

Search providers
----------------
Reuses the website-resolver's search clients: serper.dev primary (cheap),
SerpAPI fallback. Both expose ``search_firm(query)`` which is really just an
arbitrary Google query returning ``SerpResult`` (url / domain / title /
snippet). No key for either -> the provider returns ``None`` silently so the
chain skips it without spurious warnings.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from app.core.config import settings
from app.services.contact_discovery.base import (
    ContactDiscoveryProvider,
    DiscoveryResult,
)
from app.services.serpapi import SerpAPIClient, SerpAPIError, SerpResult
from app.services.serper import SerperClient, SerperError

logger = logging.getLogger(__name__)


_CONF_ORG_CONFIRMED = 80.0
_CONF_UNIQUE_MATCH = 65.0

# Legal-suffix / generic org tokens that aren't distinctive enough to confirm
# an affiliation on their own ("Group", "Capital", "Inc" all collide across
# unrelated firms). The org-confirmation check uses the firm's LONGEST
# non-generic token as its brand signal instead.
_GENERIC_ORG_TOKENS: frozenset[str] = frozenset(
    {
        "group", "inc", "llc", "lp", "llp", "lllp", "ltd", "co", "corp",
        "company", "capital", "partners", "management", "advisors",
        "advisers", "asset", "assets", "investment", "investments",
        "securities", "financial", "fund", "funds", "holdings", "the",
        "and", "of", "global", "international", "national", "us", "usa",
    }
)


class LinkedInSearchProvider(ContactDiscoveryProvider):
    name = "linkedin_search"

    async def find_person(
        self,
        first_name: str,
        last_name: str,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        first = (first_name or "").strip()
        last = (last_name or "").strip()
        if not first or not last:
            return None

        results = await self._search(first, last, org_name)
        if not results:
            return None

        profiles = [r for r in results if _is_linkedin_profile(r)]
        if not profiles:
            return None

        name_matched = [r for r in profiles if _name_matches(r, first, last)]
        if not name_matched:
            return None

        brand = _brand_token(org_name)
        # Tier 1: org-confirmed — brand token in title/snippet.
        for r in name_matched:
            if brand and _org_confirmed(r, brand):
                return self._result(r.url, _CONF_ORG_CONFIRMED, org_confirmed=True)

        # Tier 2: a single unambiguous name-matched profile (no collisions).
        if len(name_matched) == 1 and len(profiles) == 1:
            return self._result(name_matched[0].url, _CONF_UNIQUE_MATCH, org_confirmed=False)

        # Ambiguous: multiple same-name profiles, none org-confirmed. Don't guess.
        logger.info(
            "linkedin_search: %d ambiguous profiles for %s %s @ %s, none org-confirmed; skipping",
            len(name_matched),
            first,
            last,
            org_name,
        )
        return None

    async def find_org(
        self,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        # Company LinkedIn pages aren't person contacts — out of scope.
        return None

    def _result(self, url: str, confidence: float, *, org_confirmed: bool) -> DiscoveryResult:
        return DiscoveryResult(
            email=None,
            phone=None,
            linkedin_url=url.strip(),
            confidence=confidence,
            provider=self.name,
            raw={"linkedin_url": url, "org_confirmed": org_confirmed},
        )

    async def _search(
        self, first: str, last: str, org_name: str
    ) -> list[SerpResult]:
        """serper primary, SerpAPI fallback. Returns [] when no key/both miss."""
        org = (org_name or "").strip()
        query = f'"{first} {last}" {org} site:linkedin.com/in'.strip()

        serper_key = getattr(settings, "serper_api_key", None)
        if serper_key:
            try:
                hits = await SerperClient(serper_key).search_firm(query)
                if hits:
                    return hits
            except SerperError as exc:
                logger.info("linkedin_search serper error for %s %s: %s", first, last, exc)

        serpapi_key = getattr(settings, "serpapi_api_key", None)
        if serpapi_key:
            try:
                return await SerpAPIClient(serpapi_key).search_firm(query)
            except SerpAPIError as exc:
                logger.info("linkedin_search serpapi error for %s %s: %s", first, last, exc)

        return []


_LINKEDIN_HOSTS = ("linkedin.com", "www.linkedin.com")


def _is_linkedin_profile(r: SerpResult) -> bool:
    """True for a personal profile URL (``linkedin.com/in/<slug>``). Excludes
    company pages, posts, directory stubs (``/pub/dir/``), and country
    subdomains' non-profile paths."""
    host = (r.domain or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return False
    path = urlparse(r.url).path.lower()
    return "/in/" in path


def _tokens(value: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (value or "").lower()) if t]


def _name_matches(r: SerpResult, first: str, last: str) -> bool:
    """Both name tokens must appear in the profile slug OR the result title.

    The slug (``/in/sarah-bloom-raskin-12345``) is the strongest signal;
    the title ("Sarah Bloom Raskin - ... | LinkedIn") is the fallback when
    the slug is an opaque vanity string."""
    slug = urlparse(r.url).path.lower()
    slug_tokens = set(_tokens(slug))
    title_tokens = set(_tokens(r.title))
    f, l = first.lower(), last.lower()
    haystack = slug_tokens | title_tokens
    return f in haystack and l in haystack


def _brand_token(org_name: str) -> str | None:
    """The firm's most distinctive token (longest non-generic word), used as
    the org-affiliation signal. ``"VANGUARD GROUP INC"`` -> ``"vanguard"``;
    ``"MORGAN STANLEY"`` -> ``"stanley"`` (longest). Returns ``None`` when the
    name is all generic tokens (can't confirm affiliation safely)."""
    candidates = [t for t in _tokens(org_name) if len(t) >= 4 and t not in _GENERIC_ORG_TOKENS]
    if not candidates:
        return None
    return max(candidates, key=len)


def _org_confirmed(r: SerpResult, brand: str) -> bool:
    """The firm's brand token appears in the result title or snippet."""
    hay = f"{r.title} {r.snippet}".lower()
    return brand in hay
