"""FOCUS extraction → Apollo → FINRA fallback wiring.

Locks the contract added by ``FocusCeoExtractionService._apply_apollo_fallback``
and ``_apply_finra_fallback``:

  1. FOCUS extraction returns 0 execs + Apollo returns 1 -> executive_contact
     row inserted with ``source='apollo'`` and ``email``/``phone``/``linkedin_url``
     all NULL (PRD names-only constraint).
  2. Apollo returns 0 + FINRA has officers -> executive_contact rows inserted
     with ``source='finra'`` for the FINRA officers (final fallback).
  3. Apollo error -> NO row inserted (provider_error path is observable, not
     silently empty). Caller's BD state stays untouched so the next pipeline
     run retries.
  4. FOCUS returns >= 1 exec -> Apollo NOT called (no wasted API spend) — the
     fallback short-circuits when an executive_contact row already exists for
     the BD, regardless of source.
  5. APOLLO_API_KEY unset -> Apollo skipped, FINRA fallback still runs.

The tests reuse the ``_FakeSession`` shape from
``test_focus_ceo_persists_net_capital`` so the two stand-ins stay aligned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.services import focus_ceo_extraction as focus_module
from app.services.apollo import ApolloError, ApolloExecutive
from app.services.focus_ceo_extraction import FocusCeoExtractionService


# ───────────────────────── shared fakes ─────────────────────────


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    """Hand-rolled AsyncSession stand-in. ``execute()`` returns staged
    results in order; ``add()`` records the entity for later assertion."""

    def __init__(self, staged_results: list[list[Any]] | None = None) -> None:
        self._staged = list(staged_results or [])
        self.added: list[Any] = []
        self.execute_count = 0
        self.flush_count = 0

    async def execute(self, _stmt: Any) -> _FakeResult:
        if not self._staged:
            rows: list[Any] = []
        else:
            rows = self._staged.pop(0)
        self.execute_count += 1
        return _FakeResult(rows)

    def add(self, item: Any) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flush_count += 1


def _make_bd(
    *,
    bd_id: int = 42,
    name: str = "Acme Securities LLC",
    crd_number: str | None = "123456",
    executive_officers: list[dict] | None = None,
) -> BrokerDealer:
    bd = BrokerDealer()
    bd.id = bd_id
    bd.name = name
    bd.crd_number = crd_number
    bd.executive_officers = executive_officers
    return bd


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FocusCeoExtractionService:
    """Construct the service with a syntactically valid Gemini key so
    ``GeminiResponsesClient.__init__`` does not raise at construction."""
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSy" + "a" * 33)
    return FocusCeoExtractionService()


# ─────────── 1. Apollo hit -> executive_contact source='apollo' ───────────


@pytest.mark.asyncio
async def test_apollo_hit_persists_with_source_apollo(
    service: FocusCeoExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When FOCUS extraction yielded no CEO and Apollo finds one, the row
    is persisted with ``source='apollo'`` and contact channels left NULL."""
    monkeypatch.setattr(settings, "apollo_api_key", "test-key")

    bd = _make_bd()
    # No existing executive_contact row.
    session = _FakeSession(staged_results=[[]])

    fake_search = AsyncMock(
        return_value=[
            ApolloExecutive(first_name="Jane", last_name="Roe", officer_rank="ceo"),
        ]
    )
    monkeypatch.setattr(
        focus_module.ApolloClient,
        "search_executives",
        fake_search,
    )

    await service._apply_apollo_fallback(session, bd)  # type: ignore[arg-type]

    assert len(session.added) == 1
    row = session.added[0]
    assert isinstance(row, ExecutiveContact)
    assert row.bd_id == bd.id
    assert row.name == "Jane Roe"
    assert row.title == "Chief Executive Officer"
    assert row.source == "apollo"
    # PRD constraint — names-only path must NULL contact channels.
    assert row.email is None
    assert row.phone is None
    assert row.linkedin_url is None
    assert isinstance(row.enriched_at, datetime)
    assert row.enriched_at.tzinfo == timezone.utc

    fake_search.assert_awaited_once_with(bd.name, bd.crd_number)


# ─────────── 2. Apollo empty -> FINRA officers used ───────────


@pytest.mark.asyncio
async def test_apollo_empty_falls_back_to_finra(
    service: FocusCeoExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Apollo returns no people, the FINRA ``executive_officers`` blob
    on the BD is the final fallback. Rows persist with ``source='finra'``."""
    monkeypatch.setattr(settings, "apollo_api_key", "test-key")

    bd = _make_bd(
        executive_officers=[
            {"name": "Pat Quinn", "title": "Chief Compliance Officer"},
            {"name": "Casey Stone", "title": "President"},
        ]
    )
    session = _FakeSession(staged_results=[[]])

    monkeypatch.setattr(
        focus_module.ApolloClient,
        "search_executives",
        AsyncMock(return_value=[]),
    )

    await service._apply_apollo_fallback(session, bd)  # type: ignore[arg-type]

    assert len(session.added) == 2
    sources = {row.source for row in session.added}
    assert sources == {"finra"}
    names = sorted(row.name for row in session.added)
    assert names == ["Casey Stone", "Pat Quinn"]
    # PRD: names-only on the FINRA path too.
    for row in session.added:
        assert row.email is None
        assert row.phone is None
        assert row.linkedin_url is None


# ─────────── 3. Apollo error -> no rows, observable failure ───────────


@pytest.mark.asyncio
async def test_apollo_error_does_not_silently_empty(
    service: FocusCeoExtractionService,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider_error path. When Apollo raises ``ApolloError`` (5xx, 429
    exhausted, network), the helper logs and returns without inserting a
    row. We verify no sentinel/empty rows are added and the BD's state
    is untouched so the next pipeline run retries."""
    monkeypatch.setattr(settings, "apollo_api_key", "test-key")

    bd = _make_bd(
        executive_officers=[{"name": "Pat Quinn", "title": "President"}],
    )
    session = _FakeSession(staged_results=[[]])

    monkeypatch.setattr(
        focus_module.ApolloClient,
        "search_executives",
        AsyncMock(side_effect=ApolloError("503 from Apollo")),
    )

    with caplog.at_level("WARNING"):
        await service._apply_apollo_fallback(session, bd)  # type: ignore[arg-type]

    assert session.added == [], (
        "ApolloError must NOT silently fall through to FINRA — that would "
        "hide a transient outage behind the same UI as a genuine no-match. "
        "Next pipeline run retries the whole chain."
    )
    assert any("provider_error" in rec.getMessage() for rec in caplog.records), (
        "Provider_error must be logged so on-call can spot the outage."
    )


# ─────────── 4. FOCUS returns >= 1 exec -> Apollo NOT called ───────────


@pytest.mark.asyncio
async def test_existing_contact_skips_apollo(
    service: FocusCeoExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the BD already has any executive_contact row (any source),
    the fallback short-circuits — no Apollo HTTP call, no FINRA write."""
    monkeypatch.setattr(settings, "apollo_api_key", "test-key")

    bd = _make_bd(
        executive_officers=[{"name": "Pat Quinn", "title": "President"}],
    )
    # First execute() returns one existing row → short-circuit.
    session = _FakeSession(staged_results=[[123]])

    fake_search = AsyncMock(
        return_value=[
            ApolloExecutive(first_name="X", last_name="Y", officer_rank="ceo"),
        ]
    )
    monkeypatch.setattr(
        focus_module.ApolloClient,
        "search_executives",
        fake_search,
    )

    await service._apply_apollo_fallback(session, bd)  # type: ignore[arg-type]

    assert session.added == [], "no row should be added when contacts already exist"
    fake_search.assert_not_awaited()


# ─────────── 5. Missing APOLLO_API_KEY -> FINRA still runs ───────────


@pytest.mark.asyncio
async def test_missing_api_key_falls_back_to_finra(
    service: FocusCeoExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If APOLLO_API_KEY is unset, the Apollo branch is skipped entirely
    but the FINRA fallback still runs so we don't lose the FINRA-only
    enrichment for installs without an Apollo subscription."""
    monkeypatch.setattr(settings, "apollo_api_key", None)

    bd = _make_bd(
        executive_officers=[{"name": "Casey Stone", "title": "CEO"}],
    )
    session = _FakeSession(staged_results=[[]])

    # If Apollo got called we'd blow up on missing key inside ApolloClient.
    sentinel = AsyncMock(side_effect=AssertionError("Apollo must not be called"))
    monkeypatch.setattr(
        focus_module.ApolloClient,
        "search_executives",
        sentinel,
    )

    await service._apply_apollo_fallback(session, bd)  # type: ignore[arg-type]

    sentinel.assert_not_awaited()
    assert len(session.added) == 1
    assert session.added[0].source == "finra"
    assert session.added[0].name == "Casey Stone"


# ─────────── 6. Apollo + FINRA both empty -> nothing persisted ───────────


@pytest.mark.asyncio
async def test_both_empty_persists_nothing(
    service: FocusCeoExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Apollo returns no people AND the BD has no FINRA officers,
    the helper persists nothing — the firm legitimately has no public
    executive data and ``not_yet_extracted`` is the right unknown_reason."""
    monkeypatch.setattr(settings, "apollo_api_key", "test-key")

    bd = _make_bd(executive_officers=None)
    session = _FakeSession(staged_results=[[]])

    monkeypatch.setattr(
        focus_module.ApolloClient,
        "search_executives",
        AsyncMock(return_value=[]),
    )

    await service._apply_apollo_fallback(session, bd)  # type: ignore[arg-type]

    assert session.added == []
