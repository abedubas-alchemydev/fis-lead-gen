"""Last-resort web fallback (composite, per-firm + multi-person).

Runs AFTER the paid provider chain for People still missing a channel. It does
NOT scrape LinkedIn (auth-walled / ToS) and does NOT guess addresses. It
composes two existing public sources:

* :class:`LinkedInSearchProvider` -> the person's *public* profile URL via a
  ``site:linkedin.com/in`` Google search (serper / SerpAPI), precision-tiered.
* :class:`SiteCrawler` -> literal emails (and, when
  ``settings.web_fallback_phones_enabled``, phones) published on the firm's OWN
  public website, robots-respecting.

Policy ("only literal finds"): a crawled email attaches to a person ONLY when its
local-part demonstrably encodes that person's name; generic inboxes (``info@``,
``careers@``, ...) never attach to a named officer. Phones (off by default)
attach only when co-located on the same page as the person's name/email.

The firm site is crawled ONCE; LinkedIn is searched once per gap person. This
module is pure -- no DB, no commit. Callers merge the returned per-person
:class:`DiscoveryResult` via
:func:`app.services.contact_discovery.gap_fill_common.apply_gap_fill`, which is
non-destructive (append-only arrays, scalars/linkedin filled only when NULL).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.core.config import settings
from app.services.contact_discovery.base import (
    DiscoveryResult,
    EmailHit,
    PhoneHit,
)
from app.services.contact_discovery.linkedin_search import LinkedInSearchProvider
from app.services.email_extractor.site_crawler import SiteCrawler

logger = logging.getLogger(__name__)

_PROVIDER_NAME = "web_fallback"

# Inbox local-parts that belong to the FIRM, never to a specific officer. A
# crawled address whose local-part is one of these is dropped before name
# matching so we never attribute ``info@firm.com`` to "Jane Smith".
_GENERIC_LOCALS: frozenset[str] = frozenset(
    {
        "info", "contact", "contactus", "hello", "hi", "sales", "support",
        "help", "helpdesk", "admin", "administrator", "office", "careers",
        "career", "jobs", "recruiting", "recruitment", "hr", "press", "media",
        "marketing", "general", "enquiries", "enquiry", "inquiries", "inquiry",
        "mail", "email", "noreply", "no-reply", "donotreply", "do-not-reply",
        "webmaster", "postmaster", "abuse", "compliance", "legal", "privacy",
        "security", "billing", "accounts", "accounting", "ar", "ap", "invoices",
        "service", "services", "team", "clientservices", "clientservice",
        "client", "clients", "feedback", "newsletter", "subscribe", "events",
        "rsvp", "investor", "investors", "ir", "operations", "ops",
    }
)


@dataclass(frozen=True)
class GapPerson:
    """A People-section person still missing a channel after the chain.

    ``row_id`` is the executive_contacts / advisor_contacts row id so callers
    map results straight back to the row to merge.
    """

    row_id: int
    first_name: str
    last_name: str


def _tokens(value: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (value or "").lower()) if t]


def _is_generic_local(local_part: str) -> bool:
    """True if ``local_part`` is a firm-wide inbox that no officer owns."""
    return local_part.lower().strip() in _GENERIC_LOCALS


def email_matches_person(local_part: str, first: str, last: str) -> bool:
    """True if the email local-part demonstrably belongs to ``<first> <last>``.

    Literal-finds policy: we never invent an address; we only accept a published
    one whose local-part encodes this person's name. Accepted shapes (case- and
    separator-insensitive):

    - both whole name tokens present: ``jane.smith`` / ``smith.jane``
    - compacted full name: ``janesmith`` / ``smithjane``
    - first-initial + last (or reverse), >= 4 chars to avoid ``jli`` collisions:
      ``jsmith`` / ``smithj``
    - a long, distinctive first name alone (>= 6 chars) as a whole token

    Generic inboxes are rejected by the caller via :func:`_is_generic_local`
    before this is consulted.
    """
    fn = (first or "").lower().strip()
    ln = (last or "").lower().strip()
    if not fn or not ln:
        return False
    local = (local_part or "").lower().strip()
    if not local:
        return False
    parts = set(_tokens(local))
    compact = re.sub(r"[^a-z0-9]", "", local)
    fi = fn[0]

    # full first + last as separate tokens (jane.smith, smith_jane)
    if fn in parts and ln in parts:
        return True
    # compacted full name in either order (janesmith / smithjane)
    if compact in (fn + ln, ln + fn):
        return True
    # first-initial + last or reverse (jsmith / smithj) -- guard tiny collisions
    if len(fi + ln) >= 4 and compact in (fi + ln, ln + fi):
        return True
    # a long, distinctive first name alone (jacqueline@) as a whole token
    if len(fn) >= 6 and fn in parts:
        return True
    return False


async def discover_web_fallback(
    *,
    domain: str | None,
    org_name: str,
    people: list[GapPerson],
    crawler: SiteCrawler | None = None,
    linkedin: LinkedInSearchProvider | None = None,
) -> dict[int, DiscoveryResult]:
    """Build a per-person :class:`DiscoveryResult` from public web sources.

    Crawls ``domain`` ONCE (when set) for literal firm emails/phones, searches
    LinkedIn once per person, then attributes results by name. Returns a dict
    keyed by ``GapPerson.row_id`` containing only people who got >= 1 channel.
    Never raises: crawl/search failures degrade to fewer channels, never an
    exception that could abort the enclosing enrichment run.
    """
    if not people:
        return {}

    linkedin = linkedin or LinkedInSearchProvider()
    phones_enabled = bool(settings.web_fallback_phones_enabled)
    conf = float(settings.web_fallback_email_confidence)

    # ---- crawl the firm site ONCE; distribute across all gap people ----
    crawled_emails: list[tuple[str, str]] = []  # (local_part, full_email)
    page_texts: dict[str, str] = {}
    phones_by_page: dict[str, list[str]] = {}

    norm_domain = (domain or "").strip().lower().lstrip(".")
    if norm_domain.startswith("www."):
        norm_domain = norm_domain[4:]

    if norm_domain:
        crawler = crawler or SiteCrawler(collect_page_text=phones_enabled)
        try:
            crawl = await crawler.run(norm_domain)
        except Exception:  # pragma: no cover - defensive; crawler soft-fails itself
            logger.warning(
                "web_fallback: crawl raised for %s; continuing LinkedIn-only",
                norm_domain,
                exc_info=True,
            )
            crawl = None
        if crawl is not None:
            for draft in crawl.emails:
                local = (draft.email or "").split("@", 1)[0]
                if local and not _is_generic_local(local):
                    crawled_emails.append((local, draft.email))
            if phones_enabled:
                for page in getattr(crawl, "pages", []) or []:
                    page_texts[page.url] = (page.text or "").lower()
                for pdraft in getattr(crawl, "phones", []) or []:
                    if pdraft.attribution and pdraft.value:
                        phones_by_page.setdefault(pdraft.attribution, []).append(
                            pdraft.value
                        )

    out: dict[int, DiscoveryResult] = {}
    for person in people:
        first, last = person.first_name, person.last_name

        # (a) public LinkedIn URL
        linkedin_url: str | None = None
        linkedin_conf = 0.0
        try:
            li = await linkedin.find_person(first, last, org_name, domain)
        except Exception:  # pragma: no cover - provider already swallows its own
            li = None
        if li is not None and li.linkedin_url:
            linkedin_url = li.linkedin_url
            linkedin_conf = li.confidence

        # (b) name-matched literal emails from the single crawl
        email_hits: list[EmailHit] = []
        matched_emails: set[str] = set()
        for local, full in crawled_emails:
            if not email_matches_person(local, first, last):
                continue
            key = full.lower().strip()
            if key in matched_emails:
                continue
            matched_emails.add(key)
            email_hits.append(
                EmailHit(value=full, type="work", confidence=conf, source=_PROVIDER_NAME)
            )

        # (c) phones co-located with this person (opt-in, precision-first)
        phone_hits: list[PhoneHit] = []
        if phones_enabled and page_texts:
            seen_phone: set[str] = set()
            for url, text in page_texts.items():
                name_present = first.lower() in text and last.lower() in text
                email_present = any(em in text for em in matched_emails)
                if not (name_present or email_present):
                    continue
                for value in phones_by_page.get(url, []):
                    if value in seen_phone:
                        continue
                    seen_phone.add(value)
                    phone_hits.append(
                        PhoneHit(
                            value=value,
                            type="work",
                            confidence=conf,
                            source=_PROVIDER_NAME,
                        )
                    )

        if not (linkedin_url or email_hits or phone_hits):
            continue

        confidences = [conf for _ in email_hits] + [conf for _ in phone_hits]
        if linkedin_url:
            confidences.append(linkedin_conf or conf)
        out[person.row_id] = DiscoveryResult(
            email=None,
            phone=None,
            linkedin_url=linkedin_url,
            confidence=max(confidences) if confidences else 0.0,
            provider=_PROVIDER_NAME,
            raw={"domain": norm_domain, "matched_emails": sorted(matched_emails)},
            emails=email_hits,
            phones=phone_hits,
        )

    return out


__all__ = [
    "GapPerson",
    "discover_web_fallback",
    "email_matches_person",
]
