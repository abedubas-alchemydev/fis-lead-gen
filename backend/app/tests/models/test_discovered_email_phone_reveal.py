"""Unit tests for ``DiscoveredEmail.phone_reveal_pending`` -- DB-free.

The property drives the email-extractor "finding phone…" spinner. It must be
pending ONLY while an Apollo phone-reveal callback could still arrive: enriched,
no phone yet, an ``apollo_person_id`` to correlate the callback, reveal wired,
AND the reveal was requested within ``PHONE_REVEAL_TTL``. The TTL is what stops
a row spinning forever when Apollo never calls back.

These build an in-memory ``DiscoveredEmail`` (never persisted) and monkeypatch
``apollo_phone_reveal_configured`` -- the static config check the property reads
-- so they stay in the default suite with no Postgres dependency, mirroring
``test_auth_models.py``'s model-contract style.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.discovered_email import PHONE_REVEAL_TTL, DiscoveredEmail


def _enriched_pending_row(**overrides: object) -> DiscoveredEmail:
    """A row in the canonical 'reveal in flight' shape: enriched, no phone, has
    an apollo_person_id, requested just now. Individual tests override one field
    to assert the boundary it controls."""
    row = DiscoveredEmail(
        run_id=1,
        email="mark.weinberg@jpmorgan.com",
        domain="jpmorgan.com",
        source="apollo",
        enrichment_status="enriched",
        enriched_phone=None,
        apollo_person_id="587cf802f65125cad923a266",
        phone_reveal_requested_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.fixture(autouse=True)
def _reveal_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat the async reveal flow as wired. The property imports this symbol
    from ``contact_discovery._shared`` at call time, so patch it there."""
    monkeypatch.setattr(
        "app.services.contact_discovery._shared.apollo_phone_reveal_configured",
        lambda: True,
    )


def test_pending_when_requested_recently() -> None:
    """Enriched, no phone, has person id, reveal configured, requested 1 min ago
    (< TTL) -> the spinner stays up while the callback may still land."""
    assert _enriched_pending_row().phone_reveal_pending is True


def test_not_pending_when_requested_at_is_null() -> None:
    """A NULL request timestamp is never pending -- this is what clears the
    legacy rows stuck before the column existed."""
    assert _enriched_pending_row(phone_reveal_requested_at=None).phone_reveal_pending is False


def test_not_pending_after_ttl_elapses() -> None:
    """Requested longer ago than the TTL -> resolved; the row stops spinning
    even though Apollo never delivered a phone."""
    stale = datetime.now(timezone.utc) - (PHONE_REVEAL_TTL + timedelta(minutes=15))
    assert _enriched_pending_row(phone_reveal_requested_at=stale).phone_reveal_pending is False


def test_not_pending_when_phone_already_set() -> None:
    """A phone landed -> never pending, even with a fresh request timestamp."""
    assert _enriched_pending_row(enriched_phone="+19998887777").phone_reveal_pending is False


def test_not_pending_when_naive_timestamp_is_stale() -> None:
    """A tz-naive stored value is read as UTC (no naive/aware subtraction
    raises); a naive timestamp older than the TTL still resolves to False."""
    naive_stale = datetime.utcnow() - (PHONE_REVEAL_TTL + timedelta(minutes=15))
    assert naive_stale.tzinfo is None
    assert _enriched_pending_row(phone_reveal_requested_at=naive_stale).phone_reveal_pending is False
