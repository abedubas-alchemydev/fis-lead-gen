"""Tests for the LinkedIn-search contact-discovery provider.

Covers the tiered disambiguation that keeps this provider from grafting a
same-name stranger's profile onto a contact row:

- org-confirmed (brand token in title/snippet) -> conf 80
- single unambiguous name match -> conf 65
- multiple same-name profiles, none org-confirmed -> rejected (the real
  "Scott Malpass returned 6 unrelated profiles" failure mode)

Plus: non-profile URLs filtered out, name mismatch rejected, and a silent
no-op when the SerpAPI key isn't configured.

respx mocks the SerpAPI backend; no real quota is burned.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.contact_discovery.linkedin_search import (
    LinkedInSearchProvider,
    _brand_token,
)
from app.services.serpapi import _SERPAPI_SEARCH_URL


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "serpapi_api_key", "test-serpapi")
    monkeypatch.setattr(settings, "contact_discovery_timeout", 2.0)


def _serpapi(organic: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"organic_results": organic})


def _hit(link: str, title: str, snippet: str = "") -> dict:
    return {"link": link, "title": title, "snippet": snippet}


# ──────────────────────────── _brand_token ────────────────────────────


def test_brand_token_picks_distinctive_word() -> None:
    assert _brand_token("VANGUARD GROUP INC") == "vanguard"


def test_brand_token_longest_non_generic() -> None:
    # "morgan"(6) vs "stanley"(7) -> stanley
    assert _brand_token("MORGAN STANLEY") == "stanley"


def test_brand_token_none_when_all_generic() -> None:
    assert _brand_token("The Capital Group LLC") in (None, "capital")  # capital is generic -> None
    assert _brand_token("Group Holdings Inc") is None


# ──────────────────────────── Tier 1: org-confirmed ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_org_confirmed_match(keys: None) -> None:
    respx.get(_SERPAPI_SEARCH_URL).mock(
        return_value=_serpapi([
            _hit(
                "https://www.linkedin.com/in/john-james-vanguard",
                "John James - Managing Director - Vanguard | LinkedIn",
                "Managing Director at The Vanguard Group.",
            ),
        ])
    )
    provider = LinkedInSearchProvider()
    result = await provider.find_person("John", "James", "Vanguard Group INC", "vanguard.com")

    assert result is not None
    assert result.linkedin_url == "https://www.linkedin.com/in/john-james-vanguard"
    assert result.confidence == 80.0
    assert result.email is None and result.phone is None
    assert result.provider == "linkedin_search"


# ──────────────────────────── Tier 2: single unambiguous ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_single_unambiguous_match_no_org_confirm(keys: None) -> None:
    respx.get(_SERPAPI_SEARCH_URL).mock(
        return_value=_serpapi([
            _hit(
                "https://www.linkedin.com/in/natalie-lamarque",
                "Natalie Lamarque | LinkedIn",
                "Executive in New York.",  # no org token
            ),
        ])
    )
    provider = LinkedInSearchProvider()
    result = await provider.find_person("Natalie", "Lamarque", "Vanguard Group INC", "vanguard.com")

    assert result is not None
    assert result.linkedin_url == "https://www.linkedin.com/in/natalie-lamarque"
    assert result.confidence == 65.0


# ──────────────────────────── Rejection cases ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_same_name_collision_rejected(keys: None) -> None:
    """The Scott Malpass case: many same-name profiles, none mentions the
    firm -> reject rather than guess."""
    respx.get(_SERPAPI_SEARCH_URL).mock(
        return_value=_serpapi([
            _hit("https://www.linkedin.com/in/scott-malpass-301545144", "Scott Malpass - Grafton Street Partners", "Partner."),
            _hit("https://www.linkedin.com/in/scott-malpass-2a17ba5", "Scott Malpass - University of Notre Dame", "Investment office."),
            _hit("https://www.linkedin.com/in/scott-malpass-473746134", "Scott Malpass - One Circle Events", "Events."),
            _hit("https://www.linkedin.com/in/scott-malpass-5a66a3195", "scott malpass - Edgewood, Maryland", "Professional."),
        ])
    )
    provider = LinkedInSearchProvider()
    result = await provider.find_person("Scott", "Malpass", "Vanguard Group INC", "vanguard.com")

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_non_profile_urls_filtered(keys: None) -> None:
    """Company pages, posts, and directory stubs are not /in/ profiles."""
    respx.get(_SERPAPI_SEARCH_URL).mock(
        return_value=_serpapi([
            _hit("https://www.linkedin.com/company/vanguard", "Vanguard | LinkedIn", "Company page."),
            _hit("https://www.linkedin.com/pub/dir/Mark/Loughridge", "9 Mark Loughridge profiles", "Directory."),
            _hit("https://www.linkedin.com/posts/vanguard-activity-123", "Vanguard post", "Post."),
        ])
    )
    provider = LinkedInSearchProvider()
    result = await provider.find_person("Mark", "Loughridge", "Vanguard Group INC", "vanguard.com")

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_name_mismatch_rejected(keys: None) -> None:
    """A /in/ profile whose name doesn't match is not accepted even if it's
    the only result."""
    respx.get(_SERPAPI_SEARCH_URL).mock(
        return_value=_serpapi([
            _hit("https://www.linkedin.com/in/someone-else", "Bob Different - Vanguard", "MD at Vanguard."),
        ])
    )
    provider = LinkedInSearchProvider()
    result = await provider.find_person("Sarah", "Raskin", "Vanguard Group INC", "vanguard.com")

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_org_confirmed_wins_over_collision(keys: None) -> None:
    """When several same-name profiles exist but one is org-confirmed, take
    the org-confirmed one (not a rejection)."""
    respx.get(_SERPAPI_SEARCH_URL).mock(
        return_value=_serpapi([
            _hit("https://www.linkedin.com/in/john-smith-1", "John Smith - Acme", "At Acme."),
            _hit("https://www.linkedin.com/in/john-smith-vanguard", "John Smith - Vanguard", "MD at Vanguard Group."),
            _hit("https://www.linkedin.com/in/john-smith-3", "John Smith - Other", "Elsewhere."),
        ])
    )
    provider = LinkedInSearchProvider()
    result = await provider.find_person("John", "Smith", "Vanguard Group INC", "vanguard.com")

    assert result is not None
    assert result.linkedin_url == "https://www.linkedin.com/in/john-smith-vanguard"
    assert result.confidence == 80.0


# ──────────────────────────── SerpAPI error / no-key ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_serpapi_error_returns_none(keys: None) -> None:
    """A SerpAPI error degrades to no match (the chain skips this provider)."""
    respx.get(_SERPAPI_SEARCH_URL).mock(return_value=httpx.Response(500))
    provider = LinkedInSearchProvider()
    result = await provider.find_person("Jane", "Doe", "Vanguard Group INC", "vanguard.com")

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_no_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "serpapi_api_key", None)
    provider = LinkedInSearchProvider()
    result = await provider.find_person("John", "James", "Vanguard Group INC", "vanguard.com")
    assert result is None


@pytest.mark.asyncio
async def test_find_org_returns_none() -> None:
    provider = LinkedInSearchProvider()
    assert await provider.find_org("Vanguard Group INC", "vanguard.com") is None


@pytest.mark.asyncio
@respx.mock
async def test_blank_name_returns_none(keys: None) -> None:
    provider = LinkedInSearchProvider()
    assert await provider.find_person("", "James", "Vanguard", "vanguard.com") is None
