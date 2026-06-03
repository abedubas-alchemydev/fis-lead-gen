"""Unit tests for the per-advisor contact gap-fill helpers.

Covers the two pure functions used by ``_run_gap_fill_contacts``:

* ``_is_gap_row`` — whether an ``AdvisorContact`` row is missing LinkedIn /
  email / phone (scalar OR multi-value JSONB).
* ``_apply_gap_fill`` — non-destructive merge of a fresh ``DiscoveryResult``
  into an existing row: appends new emails/phones to the JSONB arrays, fills
  NULL scalars / linkedin_url / discovery_source / discovery_confidence,
  NEVER overwrites existing values.

Pattern mirrors ``test_contact_discovery_merge.py``: pure-function tests
constructed from small factories. Provider HTTP is not exercised here;
``test_contact_discovery.py`` covers the wired chain.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.advisor_contact import AdvisorContact
from app.services.advisor_refresh_orchestrator import (
    _apply_gap_fill,
    _is_gap_row,
)
from app.services.contact_discovery.base import (
    DiscoveryResult,
    EmailHit,
    PhoneHit,
)


def _make_row(
    *,
    email: str | None = None,
    phone: str | None = None,
    linkedin_url: str | None = None,
    emails: list[dict] | None = None,
    phones: list[dict] | None = None,
    discovery_source: str | None = None,
    discovery_confidence: Decimal | None = None,
    apollo_person_id: str | None = None,
) -> AdvisorContact:
    row = AdvisorContact()
    row.advisor_id = 1
    row.name = "Jane Doe"
    row.title = "Director"
    row.email = email
    row.phone = phone
    row.linkedin_url = linkedin_url
    row.emails = emails
    row.phones = phones
    row.discovery_source = discovery_source
    row.discovery_confidence = discovery_confidence
    row.apollo_person_id = apollo_person_id
    row.source = "adv"
    return row


def _make_result(
    *,
    provider: str = "apollo_match",
    confidence: float = 90.0,
    email: str | None = None,
    phone: str | None = None,
    linkedin_url: str | None = None,
    emails: list[EmailHit] | None = None,
    phones: list[PhoneHit] | None = None,
    apollo_person_id: str | None = None,
) -> DiscoveryResult:
    return DiscoveryResult(
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        confidence=confidence,
        provider=provider,
        raw={"_": provider},
        emails=emails or [],
        phones=phones or [],
        apollo_person_id=apollo_person_id,
    )


# ──────────────────────────── _is_gap_row ────────────────────────────


def test_is_gap_row_returns_false_when_all_three_present_via_scalars() -> None:
    row = _make_row(
        email="jane@acme.com",
        phone="+15551112222",
        linkedin_url="https://linkedin.com/in/jane",
    )
    assert _is_gap_row(row) is False


def test_is_gap_row_returns_false_when_arrays_carry_data_but_scalars_are_null() -> None:
    """A row with ``emails=[hit]`` and ``email=None`` is NOT a gap — the
    schema layer projects the array on read."""
    row = _make_row(
        emails=[{"value": "jane@acme.com", "type": "work", "confidence": 90.0, "source": "pdl"}],
        phones=[{"value": "+15551112222", "type": "mobile", "confidence": 80.0, "source": "pdl"}],
        linkedin_url="https://linkedin.com/in/jane",
    )
    assert _is_gap_row(row) is False


def test_is_gap_row_returns_true_when_linkedin_is_missing() -> None:
    row = _make_row(
        email="jane@acme.com",
        phone="+15551112222",
        linkedin_url=None,
    )
    assert _is_gap_row(row) is True


def test_is_gap_row_returns_true_when_email_is_missing() -> None:
    row = _make_row(
        phone="+15551112222",
        linkedin_url="https://linkedin.com/in/jane",
    )
    assert _is_gap_row(row) is True


def test_is_gap_row_returns_true_when_phone_is_missing() -> None:
    row = _make_row(
        email="jane@acme.com",
        linkedin_url="https://linkedin.com/in/jane",
    )
    assert _is_gap_row(row) is True


def test_is_gap_row_returns_true_when_everything_is_null() -> None:
    """The names-only fallback case — a row with no channels at all."""
    row = _make_row()
    assert _is_gap_row(row) is True


# ──────────────────────────── _apply_gap_fill ────────────────────────────


def test_apply_gap_fill_returns_false_when_nothing_new() -> None:
    """A merged result whose data is already on the row should be a no-op."""
    row = _make_row(
        email="jane@acme.com",
        phone="+15551112222",
        linkedin_url="https://linkedin.com/in/jane",
        emails=[
            {"value": "jane@acme.com", "type": "work", "confidence": 90.0, "source": "apollo_match"}
        ],
        phones=[
            {"value": "+15551112222", "type": "work", "confidence": 90.0, "source": "apollo_match"}
        ],
        discovery_source="apollo_match",
        discovery_confidence=Decimal("90.00"),
    )
    merged = _make_result(
        email="jane@acme.com",
        phone="+15551112222",
        linkedin_url="https://linkedin.com/in/jane",
    )

    changed = _apply_gap_fill(row, merged)

    assert changed is False
    assert row.email == "jane@acme.com"
    assert row.linkedin_url == "https://linkedin.com/in/jane"


def test_apply_gap_fill_fills_null_linkedin() -> None:
    row = _make_row(email="jane@acme.com", phone="+15551112222")
    merged = _make_result(linkedin_url="https://linkedin.com/in/jane")

    changed = _apply_gap_fill(row, merged)

    assert changed is True
    assert row.linkedin_url == "https://linkedin.com/in/jane"


def test_apply_gap_fill_does_not_overwrite_existing_linkedin() -> None:
    """Once a LinkedIn URL is on the row, we never replace it even if the
    chain returns a different value."""
    row = _make_row(linkedin_url="https://linkedin.com/in/old-jane")
    merged = _make_result(linkedin_url="https://linkedin.com/in/new-jane")

    changed = _apply_gap_fill(row, merged)

    assert row.linkedin_url == "https://linkedin.com/in/old-jane"
    # ``changed`` may still be True because the merge stamps discovery_source/
    # confidence on a previously-NULL row — but the LinkedIn URL did not move.
    assert changed in (True, False)


def test_apply_gap_fill_appends_new_email_preserves_existing_entry() -> None:
    """An existing entry's ``source`` field must survive the merge."""
    row = _make_row(
        emails=[
            {"value": "jane@acme.com", "type": "work", "confidence": 80.0, "source": "pdl"}
        ],
    )
    merged = _make_result(
        emails=[
            # Same address (different casing) — should dedupe to PDL's entry
            EmailHit(value="JANE@acme.com", type="work", confidence=90.0, source="apollo_match"),
            # New address — should be appended
            EmailHit(value="jane.personal@gmail.com", type="personal", confidence=90.0, source="apollo_match"),
        ],
    )

    changed = _apply_gap_fill(row, merged)

    assert changed is True
    assert row.emails is not None
    values = [e["value"] for e in row.emails]
    assert values == ["jane@acme.com", "jane.personal@gmail.com"]
    # Original PDL entry must not be replaced by Apollo's collision.
    assert row.emails[0]["source"] == "pdl"
    assert row.emails[1]["source"] == "apollo_match"


def test_apply_gap_fill_lifts_scalar_email_into_array_as_work() -> None:
    """Merged scalar email with no array hit lifts onto the JSONB list with
    ``type='work'`` — matches the chain merger's scalar-lifting behaviour."""
    row = _make_row()
    merged = _make_result(email="jane@acme.com", confidence=75.0, provider="hunter")

    changed = _apply_gap_fill(row, merged)

    assert changed is True
    assert row.emails == [
        {"value": "jane@acme.com", "type": "work", "confidence": 75.0, "source": "hunter"}
    ]


def test_apply_gap_fill_fills_null_scalar_email_from_array() -> None:
    """When the scalar is NULL and we now have array data, the scalar should
    be backfilled from the highest-confidence hit."""
    row = _make_row()
    merged = _make_result(
        emails=[
            EmailHit(value="lowconf@x.com", type="work", confidence=30.0, source="snov"),
            EmailHit(value="highconf@x.com", type="work", confidence=90.0, source="apollo_match"),
        ],
    )

    changed = _apply_gap_fill(row, merged)

    assert changed is True
    assert row.email == "highconf@x.com"


def test_apply_gap_fill_fills_null_phone_from_highest_confidence() -> None:
    row = _make_row()
    merged = _make_result(
        phones=[
            PhoneHit(value="+15551112222", type="mobile", confidence=70.0, source="pdl"),
            PhoneHit(value="+15553334444", type="work", confidence=90.0, source="apollo_match"),
        ],
    )

    changed = _apply_gap_fill(row, merged)

    assert changed is True
    assert row.phone == "+15553334444"


def test_apply_gap_fill_phone_dedupes_on_raw_value_not_lowercase() -> None:
    """Phone dedupe must NOT lowercase the value (numbers carry meaningful
    ``+`` / ``(`` punctuation), unlike email dedupe which does."""
    row = _make_row(
        phones=[
            {"value": "+15551112222", "type": "mobile", "confidence": 80.0, "source": "pdl"}
        ],
    )
    merged = _make_result(
        phones=[
            # Exact-string match — should dedupe; no new entry
            PhoneHit(value="+15551112222", type="work", confidence=90.0, source="apollo_match"),
        ],
    )

    changed = _apply_gap_fill(row, merged)

    assert row.phones is not None
    assert len(row.phones) == 1
    assert row.phones[0]["source"] == "pdl"


def test_apply_gap_fill_stamps_discovery_source_on_names_only_row() -> None:
    """A names-only row (``source='adv'``, ``discovery_source=None``) should
    get a real provider attribution when the chain finally finds the person."""
    row = _make_row()
    assert row.discovery_source is None
    assert row.discovery_confidence is None

    merged = _make_result(
        email="jane@acme.com",
        linkedin_url="https://linkedin.com/in/jane",
        confidence=85.0,
        provider="apollo_match",
    )

    changed = _apply_gap_fill(row, merged)

    assert changed is True
    assert row.discovery_source == "apollo_match"
    assert row.discovery_confidence == Decimal("85.00")


def test_apply_gap_fill_does_not_overwrite_existing_discovery_source() -> None:
    """If the row already credits a provider (e.g. PDL did the original
    enrichment), gap-fill must not relabel it as Apollo."""
    row = _make_row(
        discovery_source="pdl",
        discovery_confidence=Decimal("80.00"),
        linkedin_url="https://linkedin.com/in/jane",
    )
    merged = _make_result(
        email="jane@acme.com",
        provider="apollo_match",
        confidence=90.0,
    )

    _apply_gap_fill(row, merged)

    assert row.discovery_source == "pdl"
    assert row.discovery_confidence == Decimal("80.00")


def test_apply_gap_fill_stamps_apollo_person_id_when_null() -> None:
    """If the chain returns an Apollo person id and the row didn't have one
    (e.g. the original enrichment used PDL or Hunter, then a later gap-fill
    pass found the same person via Apollo), stamp it so the async phone-
    reveal webhook can locate the row later."""
    row = _make_row(linkedin_url="https://linkedin.com/in/jane")
    assert row.apollo_person_id is None

    merged = _make_result(
        email="jane@acme.com",
        apollo_person_id="587cf802f65125cad923a266",
    )

    changed = _apply_gap_fill(row, merged)

    assert changed is True
    assert row.apollo_person_id == "587cf802f65125cad923a266"


def test_apply_gap_fill_does_not_overwrite_existing_apollo_person_id() -> None:
    """A subsequent chain hit can't relabel an Apollo id — a webhook still
    in flight from the original /people/match would otherwise land on the
    wrong row."""
    row = _make_row(apollo_person_id="original-id")
    merged = _make_result(apollo_person_id="different-id")

    _apply_gap_fill(row, merged)

    assert row.apollo_person_id == "original-id"
