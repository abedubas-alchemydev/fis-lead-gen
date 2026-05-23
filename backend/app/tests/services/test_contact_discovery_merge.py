"""Unit tests for the discovery-chain merge helper.

Covers ``_merge_discovery_results`` in isolation: empty input handling,
email/phone union+dedupe with scalar lifting, ``linkedin_url`` first-non-null
selection, ``provider`` resolution by highest confidence, and the keyed
``raw`` dict layout.

The orchestrator integration tests in ``test_contact_discovery.py`` and
``test_contact_discovery_pdl_chain.py`` cover the wired ``asyncio.gather``
fan-out; these tests exercise the merge function directly so the contract
is locked independent of any provider HTTP layer.
"""

from __future__ import annotations

from app.services.contact_discovery.base import (
    DiscoveryResult,
    EmailHit,
    PhoneHit,
)
from app.services.contact_discovery.orchestrator import _merge_discovery_results


def _make_result(
    *,
    provider: str,
    confidence: float,
    email: str | None = None,
    phone: str | None = None,
    linkedin_url: str | None = None,
    emails: list[EmailHit] | None = None,
    phones: list[PhoneHit] | None = None,
    raw: dict | None = None,
) -> DiscoveryResult:
    return DiscoveryResult(
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        confidence=confidence,
        provider=provider,
        raw=raw if raw is not None else {"_": provider},
        emails=emails or [],
        phones=phones or [],
    )


def test_merge_empty_list_returns_none() -> None:
    """Empty input mirrors the old fall-through semantics: no result, no row."""
    assert _merge_discovery_results([]) is None


def test_merge_two_providers_unions_emails_and_phones() -> None:
    """Two providers contribute overlapping multi-value lists + scalar
    email/phone. The merger should union them, dedupe on the canonical
    key (lowercase email, raw phone), keep the first occurrence's typed
    hit, and lift any remaining scalars to ``type='work'`` hits."""
    pdl_result = _make_result(
        provider="pdl",
        confidence=80.0,
        email="jane@acme.com",
        phone="+15551112222",
        emails=[
            EmailHit(value="jane@acme.com", type="work", confidence=80.0, source="pdl"),
            EmailHit(value="jane.personal@gmail.com", type="personal", confidence=80.0, source="pdl"),
        ],
        phones=[
            PhoneHit(value="+15551112222", type="mobile", confidence=80.0, source="pdl"),
        ],
    )
    apollo_result = _make_result(
        provider="apollo_match",
        confidence=90.0,
        # Apollo's scalar email collides with PDL's typed hit (different
        # case to also lock the lowercase dedupe) -> PDL's typed hit wins.
        email="Jane@Acme.com",
        # Apollo's scalar phone is brand new and should be lifted to a
        # ``type='work'`` hit on the merged list.
        phone="+15554445555",
        emails=[],
        phones=[],
    )

    merged = _merge_discovery_results([pdl_result, apollo_result])

    assert merged is not None
    # Two distinct emails: PDL's typed work hit (kept as-is) + PDL's personal
    # hit. Apollo's scalar dedupes against PDL's lowercase entry.
    assert len(merged.emails) == 2
    assert merged.emails[0].value == "jane@acme.com"
    assert merged.emails[0].source == "pdl"
    assert merged.emails[0].type == "work"
    assert merged.emails[1].value == "jane.personal@gmail.com"
    assert merged.emails[1].source == "pdl"

    # Two distinct phones: PDL's typed mobile + Apollo's lifted scalar.
    assert len(merged.phones) == 2
    assert merged.phones[0].value == "+15551112222"
    assert merged.phones[0].type == "mobile"
    assert merged.phones[0].source == "pdl"
    assert merged.phones[1].value == "+15554445555"
    assert merged.phones[1].type == "work"
    assert merged.phones[1].source == "apollo_match"


def test_merge_picks_first_non_null_linkedin() -> None:
    """``linkedin_url`` is first-non-null in chain order so the first
    provider that returned something wins, even if a later (higher
    confidence) provider also has a value."""
    pdl_result = _make_result(
        provider="pdl",
        confidence=70.0,
        email="jane@acme.com",
        linkedin_url="https://linkedin.com/in/jane-pdl",
    )
    apollo_result = _make_result(
        provider="apollo_match",
        confidence=90.0,
        email="jane@acme.com",
        linkedin_url=None,
    )

    merged = _merge_discovery_results([pdl_result, apollo_result])

    assert merged is not None
    assert merged.linkedin_url == "https://linkedin.com/in/jane-pdl"


def test_merge_linkedin_skips_none_then_picks_first() -> None:
    """If the first provider has no LinkedIn, the next non-null wins."""
    pdl_result = _make_result(
        provider="pdl",
        confidence=70.0,
        email="jane@acme.com",
        linkedin_url=None,
    )
    apollo_result = _make_result(
        provider="apollo_match",
        confidence=90.0,
        email="jane@acme.com",
        linkedin_url="https://linkedin.com/in/jane-apollo",
    )

    merged = _merge_discovery_results([pdl_result, apollo_result])

    assert merged is not None
    assert merged.linkedin_url == "https://linkedin.com/in/jane-apollo"


def test_merge_provider_field_is_highest_confidence_contributor() -> None:
    """``provider`` resolves to the highest-confidence contributor (Apollo
    here at 90 vs PDL at 65), regardless of input order."""
    pdl_result = _make_result(provider="pdl", confidence=65.0, email="x@a.com")
    apollo_result = _make_result(provider="apollo_match", confidence=90.0, email="x@a.com")

    merged = _merge_discovery_results([pdl_result, apollo_result])

    assert merged is not None
    assert merged.provider == "apollo_match"
    assert merged.confidence == 90.0


def test_merge_provider_ties_break_to_input_order() -> None:
    """``max`` returns the first maximum, so on a confidence tie the chain
    order (input order) wins."""
    pdl_result = _make_result(provider="pdl", confidence=80.0, email="x@a.com")
    apollo_result = _make_result(provider="apollo_match", confidence=80.0, email="x@a.com")

    merged = _merge_discovery_results([pdl_result, apollo_result])

    assert merged is not None
    assert merged.provider == "pdl"


def test_merge_scalar_email_picks_highest_confidence_value() -> None:
    """Scalar ``email`` should be the value from the merged list with the
    highest confidence (PDL's work hit at 80 over Apollo's lifted scalar
    at 60)."""
    pdl_result = _make_result(
        provider="pdl",
        confidence=80.0,
        email="jane@acme.com",
        emails=[
            EmailHit(value="jane@acme.com", type="work", confidence=80.0, source="pdl"),
        ],
    )
    apollo_result = _make_result(
        provider="apollo_match",
        confidence=60.0,
        email="alt@acme.com",
    )

    merged = _merge_discovery_results([pdl_result, apollo_result])

    assert merged is not None
    assert merged.email == "jane@acme.com"
    assert len(merged.emails) == 2  # both distinct


def test_merge_raw_is_keyed_by_provider() -> None:
    """``raw`` is a dict keyed by provider so audit consumers can see each
    provider's native payload separately."""
    pdl_result = _make_result(
        provider="pdl", confidence=70.0, email="x@a.com", raw={"likelihood": 7}
    )
    apollo_result = _make_result(
        provider="apollo_match",
        confidence=90.0,
        email="y@a.com",
        raw={"email_status": "verified"},
    )

    merged = _merge_discovery_results([pdl_result, apollo_result])

    assert merged is not None
    assert merged.raw == {"pdl": {"likelihood": 7}, "apollo_match": {"email_status": "verified"}}


def test_merge_single_result_passes_through() -> None:
    """One provider, one hit -> merged shape equals the input result's
    fields (with scalar lifted into ``emails``/``phones`` if not already
    present and ``raw`` rewrapped under the provider key)."""
    apollo_result = _make_result(
        provider="apollo_match",
        confidence=90.0,
        email="jane@acme.com",
        phone="+15551234567",
        linkedin_url="https://linkedin.com/in/jane",
        raw={"email_status": "verified"},
    )

    merged = _merge_discovery_results([apollo_result])

    assert merged is not None
    assert merged.email == "jane@acme.com"
    assert merged.phone == "+15551234567"
    assert merged.linkedin_url == "https://linkedin.com/in/jane"
    assert merged.confidence == 90.0
    assert merged.provider == "apollo_match"
    assert merged.raw == {"apollo_match": {"email_status": "verified"}}
    # Scalar email/phone lifted into the JSONB lists.
    assert len(merged.emails) == 1
    assert merged.emails[0].value == "jane@acme.com"
    assert merged.emails[0].type == "work"
    assert merged.emails[0].source == "apollo_match"
    assert len(merged.phones) == 1
    assert merged.phones[0].value == "+15551234567"
    assert merged.phones[0].type == "work"


def test_merge_phone_dedup_is_case_sensitive_and_keeps_punctuation() -> None:
    """Phone dedupe key is ``value.strip()`` only — no lowercase, since
    phone strings carry meaningful punctuation (``+``, ``(``, ``-``). Two
    formattings of the same digits are treated as distinct."""
    pdl_result = _make_result(
        provider="pdl",
        confidence=80.0,
        phones=[
            PhoneHit(value="+1 555-111-2222", type="mobile", confidence=80.0, source="pdl"),
        ],
    )
    apollo_result = _make_result(
        provider="apollo_match",
        confidence=90.0,
        phone="(555) 111-2222",  # different formatting
    )

    merged = _merge_discovery_results([pdl_result, apollo_result])

    assert merged is not None
    # Different surface forms are distinct values; we don't normalise here.
    assert len(merged.phones) == 2
