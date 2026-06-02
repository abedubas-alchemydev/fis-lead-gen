"""Unit tests for the Phase-2 email-domain anchor in the BD enrich endpoint.

A firm's website host (e.g. ``aegiscapcorp.com``) is frequently NOT its
email-sending domain (e.g. ``aegiscap.com``). ``_derive_email_domain`` recovers
the real email domain from addresses already on the firm's contacts so the
Phase-2 email finders (Hunter/Snov) search the right place instead of the
website host.
"""
from __future__ import annotations

from app.api.v1.endpoints.broker_dealers import (
    _derive_email_domain,
    _known_contact_emails,
)


def test_derive_picks_most_common_corporate_domain() -> None:
    # Three real emails on aegiscap.com outvote one stray on the website host.
    emails = [
        "reide@aegiscap.com",
        "rfeinman@aegiscap.com",
        "tposs@aegiscap.com",
        "noreply@aegiscapcorp.com",
    ]
    assert _derive_email_domain(emails) == "aegiscap.com"


def test_derive_ignores_free_mailbox_providers() -> None:
    emails = ["ceo.personal@gmail.com", "ceo@acme-advisors.com"]
    assert _derive_email_domain(emails) == "acme-advisors.com"


def test_derive_returns_none_without_corporate_email() -> None:
    # No usable corporate email -> caller falls back to the website host.
    assert _derive_email_domain([]) is None
    assert _derive_email_domain(["x@gmail.com", "garbage", ""]) is None


def test_derive_is_deterministic_on_ties() -> None:
    # One hit each -> alphabetical tie-break keeps the result stable.
    assert _derive_email_domain(["b@beta.com", "a@alpha.com"]) == "alpha.com"


def test_derive_normalises_case_and_trailing_dot() -> None:
    assert _derive_email_domain(["A@AegisCap.com.", "b@aegiscap.com"]) == "aegiscap.com"


class _Contact:
    """Minimal stand-in for an ExecutiveContact row (duck-typed)."""

    def __init__(self, email: str | None = None, emails: list | None = None) -> None:
        self.email = email
        self.emails = emails


def test_known_contact_emails_gathers_scalar_and_jsonb() -> None:
    contacts = [
        _Contact(email="scalar@firm.com"),
        _Contact(emails=[{"value": "array@firm.com", "type": "work"}, {"type": "work"}]),
        _Contact(email=None, emails=None),
    ]
    got = _known_contact_emails(contacts)  # type: ignore[arg-type]
    assert sorted(got) == ["array@firm.com", "scalar@firm.com"]
