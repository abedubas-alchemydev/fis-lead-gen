"""Unit tests for the bank website → scan-domain resolver.

Pure-function; no DB, no HTTP. Covers the registrable-domain extraction the
bank "Extract Email" flow uses to normalize a raw ``banks.website`` into the
apex domain the Email Extractor scans.
"""

from __future__ import annotations

import pytest

from app.services.email_extractor.domain import bank_domain_from_website


@pytest.mark.parametrize(
    ("website", "expected"),
    [
        # Scheme + www + path -> registrable domain.
        ("https://www.exchangebank.com/personal", "exchangebank.com"),
        # Bare host, no scheme.
        ("exchangebank.com", "exchangebank.com"),
        # Deeper subdomain collapses to the last two labels.
        ("http://banking.first-fed.com/login", "first-fed.com"),
        ("https://secure.online.mybank.com", "mybank.com"),
        # Port is stripped.
        ("banking.first-fed.com:8443/login", "first-fed.com"),
        # Uppercase + trailing dot normalized.
        ("HTTPS://WWW.MyBank.COM./", "mybank.com"),
        # A .gov apex (OCC links) round-trips.
        ("occ.gov", "occ.gov"),
    ],
)
def test_resolves_registrable_domain(website: str, expected: str) -> None:
    assert bank_domain_from_website(website) == expected


@pytest.mark.parametrize(
    "website",
    [
        None,
        "",
        "   ",
        # A firm NAME, not a URL — no host, no dot.
        "Exchange Bank",
        # Scheme only, no host.
        "https://",
        # Single-label host is not a registrable domain.
        "localhost",
    ],
)
def test_returns_none_for_unusable_input(website: str | None) -> None:
    assert bank_domain_from_website(website) is None
