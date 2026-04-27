"""Unit tests for the cooldown guard on ``ExecutiveContactService.enrich_contacts``.

Covers the cc-cli-02 fix: stop ``POST /broker-dealers/{id}/enrich`` from
re-firing on every detail-page visit for firms where Apollo previously
returned no result. The legacy 90-day guard reads off
``ExecutiveContact.enriched_at``, which never engages for empty-result
firms (no rows -> no timestamp). The new guard reads
``BrokerDealer.last_enrich_attempt_at``, which is stamped on every
Apollo-owned outcome (success + no-result) and skipped on transient
Apollo errors.

All tests use ``respx`` for HTTP and a hand-rolled fake ``AsyncSession``
so nothing hits a real DB / Apollo. Pattern mirrors
``test_contact_discovery.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import respx

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.services.contacts import ExecutiveContactService


APOLLO_SEARCH_URL = ExecutiveContactService._APOLLO_SEARCH_URL
APOLLO_ORG_URL = "https://api.apollo.io/api/v1/organizations/enrich"


# ──────────────────────────── Fixtures ────────────────────────────


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the settings the cooldown guard reads.

    The default cooldown is 24h; tests that want a different window can
    override this fixture's value with another ``monkeypatch.setattr``.
    """
    monkeypatch.setattr(settings, "contact_enrichment_provider", "apollo")
    monkeypatch.setattr(settings, "apollo_api_key", "test-apollo-key")
    monkeypatch.setattr(settings, "apollo_enrich_cooldown_hours", 24)


def _make_bd(*, last_attempt: datetime | None = None, name: str = "ACME LLC") -> BrokerDealer:
    """Build an in-memory broker-dealer for the service under test.

    The service only reads ``id``, ``name``, and ``last_enrich_attempt_at``
    off the BD, so we set just those. ``id`` is hand-assigned because we
    skip the SQLAlchemy session that would normally autoincrement it.
    """
    bd = BrokerDealer(name=name, matched_source="edgar", is_deficient=False, status="active")
    bd.id = 1
    bd.last_enrich_attempt_at = last_attempt
    return bd


class _FakeResult:
    """Minimal Result stand-in supporting ``.scalars().all()``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    """Tiny AsyncSession stand-in for the cooldown-guard tests.

    Returns ``existing_contacts`` for any ``execute()`` call (selects and
    deletes both flow through; the service ignores the delete result).
    Tracks ``add_all`` / ``commit`` so tests can assert that the success
    and no-result paths actually persisted while the cooldown-skip and
    transient-error paths didn't.
    """

    def __init__(self, existing_contacts: list[ExecutiveContact] | None = None) -> None:
        self.existing_contacts = existing_contacts or []
        self.added: list[ExecutiveContact] = []
        self.commit_count = 0
        self.execute_calls = 0

    async def execute(self, _stmt: Any) -> _FakeResult:
        self.execute_calls += 1
        return _FakeResult(self.existing_contacts)

    def add_all(self, items: list[ExecutiveContact]) -> None:
        self.added.extend(items)

    async def commit(self) -> None:
        self.commit_count += 1


# ──────────────────────────── Tests ────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_first_call_stamps_and_calls_apollo(patch_settings: None) -> None:
    """No prior attempt -> Apollo runs, the BD timestamp gets stamped."""
    bd = _make_bd(last_attempt=None)
    session = _FakeSession()

    search_route = respx.post(APOLLO_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "people": [
                    {
                        "name": "Alice Doe",
                        "title": "CEO",
                        "email": "alice@example.com",
                        "phone_numbers": [{"sanitized_number": "+15550100"}],
                        "linkedin_url": "https://linkedin.com/in/alice",
                    }
                ]
            },
        )
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert search_route.called, "Apollo search should be hit on first call"
    assert bd.last_enrich_attempt_at is not None, "Stamp should be set on success"
    assert session.commit_count == 1
    assert len(session.added) == 1
    assert session.added[0].name == "Alice Doe"


@pytest.mark.asyncio
@respx.mock
async def test_within_cooldown_short_circuits(patch_settings: None) -> None:
    """A recent attempt -> Apollo is NOT hit, stamp does not move, no commit.

    This is the empty-result fix: the BD has no ExecutiveContact rows
    (Apollo returned nothing last time) but the cooldown timestamp is
    enough on its own to short-circuit the call.
    """
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    bd = _make_bd(last_attempt=one_hour_ago)
    session = _FakeSession()

    search_route = respx.post(APOLLO_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"people": []})
    )

    service = ExecutiveContactService()
    result = await service.enrich_contacts(session, bd)

    assert not search_route.called, "Apollo must not be hit during cooldown"
    assert bd.last_enrich_attempt_at == one_hour_ago, "Stamp must not move"
    assert session.commit_count == 0, "No commit when cooldown short-circuits"
    assert result == []


@pytest.mark.asyncio
@respx.mock
async def test_past_cooldown_stamps_and_calls_apollo(patch_settings: None) -> None:
    """An attempt past the cooldown window -> Apollo runs, stamp moves forward."""
    twenty_five_hours_ago = datetime.now(timezone.utc) - timedelta(hours=25)
    bd = _make_bd(last_attempt=twenty_five_hours_ago)
    session = _FakeSession()

    search_route = respx.post(APOLLO_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"people": []})
    )
    respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(200, json={"organization": None})
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert search_route.called, "Past-cooldown call should fire Apollo again"
    assert bd.last_enrich_attempt_at is not None
    assert bd.last_enrich_attempt_at > twenty_five_hours_ago, "Stamp should move forward"
    assert session.commit_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_no_result_still_stamps(patch_settings: None) -> None:
    """Apollo cleanly returns no people on either strategy -> stamp anyway.

    Without this stamp, the FE's useEffect would re-fire on every visit
    because no ExecutiveContact rows exist for the firm.
    """
    bd = _make_bd(last_attempt=None)
    session = _FakeSession()

    search_route = respx.post(APOLLO_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"people": []})
    )
    respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(200, json={"organization": None})
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert search_route.called
    assert bd.last_enrich_attempt_at is not None, "Empty-result must still stamp"
    assert session.commit_count == 1
    assert session.added == [], "No-result -> nothing added to session"


@pytest.mark.asyncio
@respx.mock
async def test_transient_error_does_not_stamp(patch_settings: None) -> None:
    """Apollo 5xx on every attempt -> do NOT stamp.

    Stamping on a transient failure would lock out the firm for 24h after
    a single 502, so the next visit must be allowed to retry.
    """
    bd = _make_bd(last_attempt=None)
    session = _FakeSession()

    search_route = respx.post(APOLLO_SEARCH_URL).mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )
    respx.post(APOLLO_ORG_URL).mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )

    service = ExecutiveContactService()
    await service.enrich_contacts(session, bd)

    assert search_route.called
    assert bd.last_enrich_attempt_at is None, "Transient error must not stamp"
    assert session.commit_count == 0, "Transient error must not commit"
