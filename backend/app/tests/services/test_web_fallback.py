"""Tests for the last-resort web-fallback composite.

The composite is the final "Generate More Details" stage: for People still
missing a channel it searches a public LinkedIn URL and crawls the firm's OWN
site for literal published emails (and, behind a flag, co-located phones), then
hands per-person ``DiscoveryResult``s back to the caller to merge.

The two underlying pieces (``LinkedInSearchProvider`` + ``SiteCrawler``) have
their own respx-backed suites; here we inject fakes so the composite's policy is
tested deterministically:

* literal-finds-only attribution (name-token match) and the generic-inbox guard
* a SINGLE crawl shared across all gap people
* LinkedIn-only when there is no domain to crawl
* people with nothing found are omitted
* crawl failures degrade gracefully (never raise)
* phones off by default; on, only co-located numbers attach
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.contact_discovery.base import DiscoveryResult
from app.services.contact_discovery.web_fallback import (
    GapPerson,
    _is_generic_local,
    discover_web_fallback,
    email_matches_person,
)
from app.services.email_extractor.base import (
    DiscoveredEmailDraft,
    DiscoveredPhoneDraft,
    DiscoveryResult as CrawlResult,
    PageText,
)


# ──────────────────────────── fakes ────────────────────────────


class FakeCrawler:
    """Stand-in for ``SiteCrawler`` that returns a canned crawl and counts runs."""

    def __init__(self, result: CrawlResult | None = None, *, raises: bool = False) -> None:
        self._result = result if result is not None else CrawlResult()
        self._raises = raises
        self.calls = 0
        self.domains: list[str] = []

    async def run(self, domain: str) -> CrawlResult:
        self.calls += 1
        self.domains.append(domain)
        if self._raises:
            raise RuntimeError("boom")
        return self._result


class FakeLinkedIn:
    """Stand-in for ``LinkedInSearchProvider``. Returns a URL per (first,last)."""

    def __init__(self, by_name: dict[tuple[str, str], str] | None = None) -> None:
        self._by_name = by_name or {}
        self.calls: list[tuple[str, str, str, str | None]] = []

    async def find_person(
        self, first: str, last: str, org_name: str, domain: str | None
    ) -> DiscoveryResult | None:
        self.calls.append((first, last, org_name, domain))
        url = self._by_name.get((first.lower(), last.lower()))
        if not url:
            return None
        return DiscoveryResult(
            email=None,
            phone=None,
            linkedin_url=url,
            confidence=80.0,
            provider="linkedin_search",
            raw={},
        )


def _emails(*addrs: str) -> CrawlResult:
    return CrawlResult(
        emails=[DiscoveredEmailDraft(email=a, source="site_crawler", confidence=0.75) for a in addrs]
    )


# ──────────────────────────── _is_generic_local ────────────────────────────


@pytest.mark.parametrize("local", ["info", "contact", "careers", "hr", "no-reply", "sales", "investors"])
def test_generic_inbox_locals_are_recognized(local: str) -> None:
    assert _is_generic_local(local) is True


@pytest.mark.parametrize("local", ["jane.smith", "jsmith", "j.doe", "robert.king"])
def test_person_locals_are_not_generic(local: str) -> None:
    assert _is_generic_local(local) is False


# ──────────────────────────── email_matches_person ────────────────────────────


@pytest.mark.parametrize(
    "local",
    ["jane.smith", "smith.jane", "janesmith", "smithjane", "jsmith", "smithj", "jane_smith"],
)
def test_email_matches_person_accepts_name_encodings(local: str) -> None:
    assert email_matches_person(local, "Jane", "Smith") is True


@pytest.mark.parametrize("local", ["info", "bob.jones", "j", "marketing", "smithy"])
def test_email_matches_person_rejects_non_matches(local: str) -> None:
    assert email_matches_person(local, "Jane", "Smith") is False


def test_email_matches_person_long_first_name_alone() -> None:
    # A distinctive (>=6) first name alone is accepted; a short one is not.
    assert email_matches_person("jacqueline", "Jacqueline", "Doe") is True
    assert email_matches_person("jane", "Jane", "Smith") is False


def test_email_matches_person_initial_plus_last_is_ambiguous_by_design() -> None:
    # "j.smith" matches BOTH Jane Smith and John Smith — documented limitation
    # of initial+last; acceptable for a last resort.
    assert email_matches_person("j.smith", "Jane", "Smith") is True
    assert email_matches_person("j.smith", "John", "Smith") is True


# ──────────────────────────── discover_web_fallback ────────────────────────────


@pytest.mark.asyncio
async def test_name_matched_email_attaches_generic_inbox_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "web_fallback_email_confidence", 71.0)
    monkeypatch.setattr(settings, "web_fallback_phones_enabled", False)
    crawler = FakeCrawler(
        _emails("jane.smith@acme.com", "info@acme.com", "careers@acme.com")
    )
    linkedin = FakeLinkedIn()  # no LinkedIn hits

    results = await discover_web_fallback(
        domain="acme.com",
        org_name="Acme Capital",
        people=[GapPerson(1, "Jane", "Smith"), GapPerson(2, "Bob", "Jones")],
        crawler=crawler,
        linkedin=linkedin,
    )

    # Jane gets her name-matched address; the generic inboxes never attach.
    assert set(results) == {1}
    jane = results[1]
    assert [h.value for h in jane.emails] == ["jane.smith@acme.com"]
    assert jane.emails[0].confidence == 71.0
    assert jane.emails[0].source == "web_fallback"
    assert jane.provider == "web_fallback"
    assert jane.linkedin_url is None
    # Bob matched nothing → omitted entirely.
    assert 2 not in results


@pytest.mark.asyncio
async def test_single_crawl_shared_across_people(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "web_fallback_phones_enabled", False)
    crawler = FakeCrawler(_emails("jane.smith@acme.com", "bob.jones@acme.com"))
    results = await discover_web_fallback(
        domain="acme.com",
        org_name="Acme",
        people=[GapPerson(1, "Jane", "Smith"), GapPerson(2, "Bob", "Jones"), GapPerson(3, "No", "Match")],
        crawler=crawler,
        linkedin=FakeLinkedIn(),
    )
    assert crawler.calls == 1  # crawled once, distributed to all three
    assert set(results) == {1, 2}


@pytest.mark.asyncio
async def test_linkedin_only_when_no_domain() -> None:
    crawler = FakeCrawler(_emails("jane.smith@acme.com"))
    linkedin = FakeLinkedIn({("jane", "smith"): "https://www.linkedin.com/in/jane-smith"})
    results = await discover_web_fallback(
        domain=None,
        org_name="Acme",
        people=[GapPerson(1, "Jane", "Smith")],
        crawler=crawler,
        linkedin=linkedin,
    )
    assert crawler.calls == 0  # no domain → no crawl
    assert results[1].linkedin_url == "https://www.linkedin.com/in/jane-smith"
    assert results[1].emails == []


@pytest.mark.asyncio
async def test_crawl_exception_is_swallowed_linkedin_still_runs() -> None:
    crawler = FakeCrawler(raises=True)
    linkedin = FakeLinkedIn({("jane", "smith"): "https://www.linkedin.com/in/jane-smith"})
    results = await discover_web_fallback(
        domain="acme.com",
        org_name="Acme",
        people=[GapPerson(1, "Jane", "Smith")],
        crawler=crawler,
        linkedin=linkedin,
    )
    assert crawler.calls == 1
    assert results[1].linkedin_url == "https://www.linkedin.com/in/jane-smith"
    assert results[1].emails == []


@pytest.mark.asyncio
async def test_no_people_returns_empty() -> None:
    crawler = FakeCrawler(_emails("jane.smith@acme.com"))
    results = await discover_web_fallback(
        domain="acme.com", org_name="Acme", people=[], crawler=crawler, linkedin=FakeLinkedIn()
    )
    assert results == {}
    assert crawler.calls == 0


@pytest.mark.asyncio
async def test_phones_disabled_by_default_ignores_crawled_phones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "web_fallback_phones_enabled", False)
    crawl = CrawlResult(
        emails=[DiscoveredEmailDraft(email="jane.smith@acme.com", source="site_crawler", confidence=0.75)],
        phones=[DiscoveredPhoneDraft(value="(212) 555-0100", source="site_crawler", confidence=0.75, attribution="https://acme.com/team")],
        pages=[PageText(url="https://acme.com/team", text="Jane Smith (212) 555-0100")],
    )
    results = await discover_web_fallback(
        domain="acme.com",
        org_name="Acme",
        people=[GapPerson(1, "Jane", "Smith")],
        crawler=FakeCrawler(crawl),
        linkedin=FakeLinkedIn(),
    )
    assert results[1].emails  # email still attaches
    assert results[1].phones == []  # phones gated off


@pytest.mark.asyncio
async def test_phones_enabled_attaches_colocated_not_switchboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "web_fallback_phones_enabled", True)
    monkeypatch.setattr(settings, "web_fallback_email_confidence", 70.0)
    crawl = CrawlResult(
        emails=[DiscoveredEmailDraft(email="jane.smith@acme.com", source="site_crawler", confidence=0.75)],
        phones=[
            DiscoveredPhoneDraft(value="(212) 555-0100", source="site_crawler", confidence=0.75, attribution="https://acme.com/team"),
            DiscoveredPhoneDraft(value="(800) 555-9999", source="site_crawler", confidence=0.6, attribution="https://acme.com/"),
        ],
        pages=[
            PageText(url="https://acme.com/team", text="Jane Smith, Managing Director — (212) 555-0100"),
            PageText(url="https://acme.com/", text="Main office: (800) 555-9999"),
        ],
    )
    results = await discover_web_fallback(
        domain="acme.com",
        org_name="Acme",
        people=[GapPerson(1, "Jane", "Smith")],
        crawler=FakeCrawler(crawl),
        linkedin=FakeLinkedIn(),
    )
    phones = [h.value for h in results[1].phones]
    assert phones == ["(212) 555-0100"]  # co-located with her name
    assert "(800) 555-9999" not in phones  # lone switchboard → not attached
    assert results[1].phones[0].source == "web_fallback"
    assert results[1].phones[0].confidence == 70.0
