"""Tests for the per-advisor roster backfill (_run_backfill_roster_contacts).

The backfill walks the executive_officers roster and, per officer, either
gap-fills the existing advisor_contacts row or creates the missing one — it
deliberately bypasses the _run_enrich_contacts idempotency guard but stays
non-destructive and idempotent on re-run.

Harness: an in-memory fake SessionLocal (the function uses the module-global
SessionLocal across several `async with` blocks) plus a stubbed _walk_chain
returning canned DiscoveryResults keyed by (first, last). No DB / network.
"""

from __future__ import annotations

import pytest

import app.services.advisor_refresh_orchestrator as orch
from app.models.advisor_contact import AdvisorContact
from app.models.investment_advisor import InvestmentAdvisor
from app.models.pipeline_run import PipelineRun
from app.services.advisor_refresh_orchestrator import (
    _roster_match_key,
    _run_backfill_roster_contacts,
)
from app.services.contact_discovery.base import DiscoveryResult


# ──────────────────────────── _roster_match_key ────────────────────────────


def test_match_key_collapses_both_name_forms() -> None:
    assert _roster_match_key("PEROLD, ANDRE, FRANCOIS") == ("andre", "perold")
    assert _roster_match_key("Andre Perold") == ("andre", "perold")


def test_match_key_none_for_unparseable() -> None:
    assert _roster_match_key("") is None
    assert _roster_match_key("Madonna") is None


# ──────────────────────────── Fake session harness ────────────────────────────


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list:
        return list(self._rows)


class _Store:
    def __init__(self) -> None:
        self.advisor: InvestmentAdvisor | None = None
        self.contacts: dict[int, AdvisorContact] = {}
        self.run: PipelineRun | None = None
        self.next_id = 1000
        self.commits = 0


class _FakeSession:
    def __init__(self, store: _Store) -> None:
        self.store = store

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, model, pk):
        if model is InvestmentAdvisor:
            a = self.store.advisor
            return a if a is not None and a.id == pk else None
        if model is PipelineRun:
            r = self.store.run
            return r if r is not None and r.id == pk else None
        if model is AdvisorContact:
            return self.store.contacts.get(pk)
        return None

    async def execute(self, _stmt):
        # Only used for select(AdvisorContact).where(advisor_id == ...)
        return _Result(list(self.store.contacts.values()))

    def add(self, obj) -> None:
        if isinstance(obj, AdvisorContact):
            if getattr(obj, "id", None) is None:
                obj.id = self.store.next_id
                self.store.next_id += 1
            self.store.contacts[obj.id] = obj

    async def commit(self) -> None:
        self.store.commits += 1

    async def refresh(self, _obj) -> None:
        return None


def _install(monkeypatch: pytest.MonkeyPatch, store: _Store, chain: dict) -> None:
    monkeypatch.setattr(orch, "SessionLocal", lambda: _FakeSession(store))

    async def _fake_walk_chain(entity_type, *, first_name, last_name, org_name, domain, cache_name):
        return chain.get((first_name.lower(), last_name.lower()))

    monkeypatch.setattr(orch, "_walk_chain", _fake_walk_chain)


def _result(*, email=None, phone=None, linkedin_url=None, provider="apollo_match", confidence=90.0):
    return DiscoveryResult(
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        confidence=confidence,
        provider=provider,
        raw={"_": provider},
    )


def _advisor(roster: list[dict]) -> InvestmentAdvisor:
    a = InvestmentAdvisor()
    a.id = 24053
    a.name = "Vanguard Group INC"
    a.website = "https://vanguard.com"
    a.executive_officers = roster
    a.last_gap_fill_attempt_at = None
    return a


def _contact(cid: int, name: str, **kw) -> AdvisorContact:
    c = AdvisorContact()
    c.id = cid
    c.advisor_id = 24053
    c.name = name
    c.title = kw.get("title", "DIRECTOR")
    c.email = kw.get("email")
    c.phone = kw.get("phone")
    c.linkedin_url = kw.get("linkedin_url")
    c.emails = kw.get("emails")
    c.phones = kw.get("phones")
    c.discovery_source = kw.get("discovery_source")
    c.discovery_confidence = kw.get("discovery_confidence")
    c.apollo_person_id = kw.get("apollo_person_id")
    c.source = kw.get("source", "adv")
    return c


# ──────────────────────────── Backfill behaviour ────────────────────────────


@pytest.mark.asyncio
async def test_creates_missing_and_gapfills_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    store.advisor = _advisor([
        {"name": "PEROLD, ANDRE, FRANCOIS", "title": "DIRECTOR"},  # existing row, missing LinkedIn
        {"name": "DOE, JANE", "title": "MANAGING DIRECTOR"},        # no row yet -> create
    ])
    # Existing Perold row already has an email but no LinkedIn (a gap).
    store.contacts = {500: _contact(500, "Andre Perold", email="andre@vanguard.com")}
    store.run = PipelineRun()
    store.run.id = 900

    chain = {
        ("andre", "perold"): _result(email="andre@vanguard.com", linkedin_url="https://linkedin.com/in/andre"),
        ("jane", "doe"): _result(email="jane@vanguard.com", linkedin_url="https://linkedin.com/in/jane"),
    }
    _install(monkeypatch, store, chain)

    await _run_backfill_roster_contacts(900, 24053, "test")

    assert store.run.status == "completed"
    # Perold's existing row got its LinkedIn filled (gap-fill, not a new row).
    assert store.contacts[500].linkedin_url == "https://linkedin.com/in/andre"
    # Jane Doe got a brand-new row.
    names = sorted(c.name for c in store.contacts.values())
    assert names == ["Andre Perold", "Jane Doe"]
    jane = next(c for c in store.contacts.values() if c.name == "Jane Doe")
    assert jane.email == "jane@vanguard.com"
    assert jane.linkedin_url == "https://linkedin.com/in/jane"
    # advisor cooldown stamped
    assert store.advisor.last_gap_fill_attempt_at is not None


@pytest.mark.asyncio
async def test_names_only_row_on_chain_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    store.advisor = _advisor([{"name": "PASTOR, LUBOS", "title": "DIRECTOR"}])
    store.run = PipelineRun()
    store.run.id = 901
    chain = {}  # chain finds nothing
    _install(monkeypatch, store, chain)

    await _run_backfill_roster_contacts(901, 24053, "test")

    assert store.run.status == "completed"
    assert len(store.contacts) == 1
    row = next(iter(store.contacts.values()))
    # Names-only fallback: raw roster name, no channels, source 'adv'.
    assert row.name == "PASTOR, LUBOS"
    assert row.email is None and row.phone is None and row.linkedin_url is None
    assert row.source == "adv"


@pytest.mark.asyncio
async def test_existing_row_matched_via_raw_roster_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing row stored under the raw ADV form still matches the roster
    officer (both parse to the same key) — so we gap-fill, not duplicate."""
    store = _Store()
    store.advisor = _advisor([{"name": "MALPASS, SCOTT, CHARLES", "title": "DIRECTOR"}])
    # Existing row happens to be stored in the raw form too.
    store.contacts = {600: _contact(600, "MALPASS, SCOTT, CHARLES", email="scott@vanguard.com")}
    store.run = PipelineRun()
    store.run.id = 902
    chain = {("scott", "malpass"): _result(email="scott@vanguard.com", linkedin_url="https://linkedin.com/in/scott")}
    _install(monkeypatch, store, chain)

    await _run_backfill_roster_contacts(902, 24053, "test")

    assert len(store.contacts) == 1  # no duplicate created
    assert store.contacts[600].linkedin_url == "https://linkedin.com/in/scott"


@pytest.mark.asyncio
async def test_duplicate_roster_entries_create_one_row(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    store.advisor = _advisor([
        {"name": "DOE, JANE", "title": "MD"},
        {"name": "Doe, Jane", "title": "MD"},  # same person, different casing/form
    ])
    store.run = PipelineRun()
    store.run.id = 903
    chain = {("jane", "doe"): _result(email="jane@vanguard.com")}
    _install(monkeypatch, store, chain)

    await _run_backfill_roster_contacts(903, 24053, "test")

    assert len(store.contacts) == 1  # de-duped within the run


@pytest.mark.asyncio
async def test_rerun_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second backfill over the same roster doesn't create duplicate rows."""
    store = _Store()
    store.advisor = _advisor([{"name": "DOE, JANE", "title": "MD"}])
    store.run = PipelineRun()
    store.run.id = 904
    chain = {("jane", "doe"): _result(email="jane@vanguard.com", linkedin_url="https://linkedin.com/in/jane")}
    _install(monkeypatch, store, chain)

    await _run_backfill_roster_contacts(904, 24053, "test")
    assert len(store.contacts) == 1
    # Second run: the row now exists, so it's matched + gap-filled, not recreated.
    store.run = PipelineRun()
    store.run.id = 905
    await _run_backfill_roster_contacts(905, 24053, "test")
    assert len(store.contacts) == 1


@pytest.mark.asyncio
async def test_empty_roster_completes_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    store.advisor = _advisor([])
    store.run = PipelineRun()
    store.run.id = 906
    _install(monkeypatch, store, {})

    await _run_backfill_roster_contacts(906, 24053, "test")

    assert store.run.status == "completed"
    assert len(store.contacts) == 0
    assert store.advisor.last_gap_fill_attempt_at is not None
