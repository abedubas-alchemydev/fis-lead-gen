"""Tier-2 Apollo enrichment for bank_contacts
(``services/bank_contact_enrichment.py`` + migration ``20260702_0004``).

All Apollo HTTP is mocked via respx — no real calls, ever. Locks the six
contracts the paid job must keep:

1. **Match-accept** — a close-name + plausible-org Apollo person fills
   email/phone/title where NULL and stamps ``matched``.
2. **Near-name variant (M3)** — an accepted name within 2 edits is recorded
   as a provenance note on ``context_snippet`` while the stored ``name`` (the
   extraction dedupe key) is left byte-stable, so a later ``--extract-contacts``
   of the same PDF can't re-insert the PDF-rendered name as a twin row.
3. **Reject ambiguous** — a different person name or an implausible org
   association fills NOTHING and stamps ``no_match`` (reject-and-log).
4. **Never overwrite extracted values** — channels/titles already on the
   row survive even when Apollo disagrees.
5. **Idempotent skip** — attempted rows (``enriched_at`` set) and rows
   that already carry an email are never re-planned: a re-run spends
   zero credits.
6. **Cap enforcement** — ``--limit`` bounds the number of paid lookups;
   dry-run planning makes zero HTTP calls; provider errors leave the row
   unstamped (retryable) and trip a circuit breaker.

Plus DB-less pins on the ``20260702_0004`` migration (single head, strict
child of the bank_contacts migration, nullable additive columns).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import String, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.bank import BankContact
from app.services.bank_contact_enrichment import (
    ENRICH_STATUS_MATCHED,
    ENRICH_STATUS_NO_MATCH,
    BankContactApolloClient,
    domain_from_website,
    evaluate_match,
    execute_enrichment,
    levenshtein,
    names_close,
    org_plausible,
    plan_enrichment,
    split_person_name,
    strip_bank_suffixes,
)
from app.services.bank_contact_extraction import (
    BankContactExtractionService,
    ExtractedBankContact,
)

_APOLLO_MATCH_URL = "https://api.apollo.io/api/v1/people/match"
_PDF_URL = "https://www.occ.gov/topics/digital-assets/apps/erebor-public.pdf"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # banks carries JSONB (PG-only), so create only the three columns the
        # plan query joins on via raw DDL; bank_contacts comes from metadata.
        await conn.execute(
            sa_text(
                "CREATE TABLE banks ("
                "id INTEGER PRIMARY KEY, "
                "name VARCHAR(255) NOT NULL, "
                "website VARCHAR(512))"
            )
        )
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=[BankContact.__table__]
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real exponential backoff sleep so retry tests stay fast."""

    async def _no_sleep(_attempt: int) -> None:
        return None

    monkeypatch.setattr(BankContactApolloClient, "_backoff", staticmethod(_no_sleep))


async def _seed_bank(
    session,
    *,
    bank_id: int = 1,
    name: str = "Erebor Bank, N.A.",
    website: str | None = "https://www.erebor.example",
) -> None:
    await session.execute(
        sa_text("INSERT INTO banks (id, name, website) VALUES (:id, :name, :website)"),
        {"id": bank_id, "name": name, "website": website},
    )
    await session.commit()


def _contact(**overrides) -> BankContact:
    defaults = dict(
        bank_id=1,
        name="John Hirshman",
        title=None,
        role_context="contact_person",
        email=None,
        phone=None,
        source="application_pdf",
        source_url=_PDF_URL,
        page_number=1,
        context_snippet="Contact person: John Hirshman",
    )
    defaults.update(overrides)
    return BankContact(**defaults)


def _apollo_payload(
    first: str = "John",
    last: str = "Hirshman",
    *,
    title: str | None = "Chief Executive Officer",
    email: str | None = "john.hirshman@erebor.example",
    phone: str | None = "+12025550100",
    org_name: str | None = "Erebor Bank",
    org_domain: str | None = "erebor.example",
) -> dict:
    person: dict = {
        "first_name": first,
        "last_name": last,
        "name": f"{first} {last}",
        "title": title,
        "email": email,
        "email_status": "verified",
        "phone_numbers": [{"sanitized_number": phone}] if phone else [],
        "linkedin_url": "https://www.linkedin.com/in/should-never-be-read",
    }
    if org_name or org_domain:
        person["organization"] = {"name": org_name, "primary_domain": org_domain}
    return {"person": person}


def _client() -> BankContactApolloClient:
    return BankContactApolloClient("test-key", max_attempts=1)


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_strip_bank_suffixes_variants() -> None:
    assert strip_bank_suffixes("Erebor Bank, N.A.") == "Erebor Bank"
    assert strip_bank_suffixes("Erebor Bank NA") == "Erebor Bank"
    assert strip_bank_suffixes("Erebor Bank, National Association") == "Erebor Bank"
    assert strip_bank_suffixes("Erebor Bank, N.A. (In Organization)") == "Erebor Bank"
    # No suffix -> unchanged; suffix-lookalikes inside words never clipped.
    assert strip_bank_suffixes("Bank of Montana") == "Bank of Montana"
    assert strip_bank_suffixes("Bank of America") == "Bank of America"


def test_split_person_name_handles_middles_suffixes_and_refusals() -> None:
    assert split_person_name("John A. Hirshman") == ("John", "Hirshman")
    assert split_person_name("Jane Doe, Esq.") == ("Jane", "Doe")
    assert split_person_name("Dr. Ann Lee Jr.") == ("Ann", "Lee")
    assert split_person_name("Madonna") is None
    assert split_person_name("") is None
    assert split_person_name(None) is None


def test_levenshtein_pdf_artifact_distance() -> None:
    # The motivating artifact: 'rn' rendered where 'm' was printed.
    assert levenshtein("hirshrnan", "hirshman") == 2
    assert levenshtein("same", "same") == 0
    assert levenshtein("", "abc") == 3


def test_names_close_exact_near_and_mismatch() -> None:
    assert names_close("John Hirshman", "John", "Hirshman") is True
    assert names_close("John A. Hirshrnan", "John", "Hirshman") is True  # lev 2
    assert names_close("John Hirshman", "Robert", "Smith") is False
    assert names_close("Madonna", "John", "Hirshman") is False  # unsplittable
    assert names_close("John Hirshman", None, "Hirshman") is False


def test_org_plausible_domain_name_and_generic_token_guard() -> None:
    # Domain equality wins even when the org name is Apollo's own casing.
    assert org_plausible("Erebor Bank, N.A.", "erebor.example", "Whatever", "www.erebor.example")
    # Normalized-name equality ('N.A.'/'National Association' noise dropped).
    assert org_plausible("Erebor Bank, N.A.", None, "Erebor Bank National Association", None)
    # Distinctive-token containment: 'erebor' ⊂ 'erebor bank'.
    assert org_plausible("Erebor Bank, N.A.", None, "Erebor", None)
    # Generic-only containment must NOT corroborate.
    assert not org_plausible("Alpha Trust Bank", None, "Trust", None)
    # A different company is not plausible; neither is no org info at all.
    assert not org_plausible("Erebor Bank, N.A.", "erebor.example", "Acme Widgets Inc", "acme.example")
    assert not org_plausible("Erebor Bank, N.A.", "erebor.example", None, None)


def test_domain_from_website() -> None:
    assert domain_from_website("https://www.erebor.example/about") == "erebor.example"
    assert domain_from_website("erebor.example") == "erebor.example"
    assert domain_from_website(None) is None
    assert domain_from_website("   ") is None
    assert domain_from_website("not-a-domain") is None


# ── Plan: eligibility, ordering, cap, zero HTTP ──────────────────────────────


@respx.mock
async def test_plan_orders_by_role_caps_and_makes_zero_apollo_calls(db_session) -> None:
    route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json=_apollo_payload())
    )
    await _seed_bank(db_session)
    db_session.add_all(
        [
            _contact(name="Cara Counsel", role_context="counsel"),
            _contact(name="Oscar Organizer", role_context="organizer"),
            _contact(name="Connie Contact", role_context="contact_person"),
            _contact(name="Pete Proposed", role_context="proposed_officer", title="CFO"),
        ]
    )
    await db_session.commit()

    plan = await plan_enrichment(db_session, limit=3)

    assert plan.eligible == 4
    assert [p.contact_name for p in plan.planned] == [
        "Connie Contact",
        "Pete Proposed",
        "Oscar Organizer",
    ]
    first = plan.planned[0]
    assert first.org_query == "Erebor Bank"          # ', N.A.' stripped
    assert first.domain == "erebor.example"          # from banks.website
    assert first.missing == ("email", "phone", "title")
    assert plan.planned[1].missing == ("email", "phone")  # has a title
    assert route.call_count == 0                     # dry-run plan = zero paid calls


async def test_plan_skips_rows_with_email_or_attempt_marker(db_session) -> None:
    await _seed_bank(db_session)
    from datetime import datetime, timezone

    db_session.add_all(
        [
            _contact(name="Has Email", email="x@erebor.example"),
            _contact(name="Already Matched", enriched_at=datetime.now(timezone.utc),
                     enrich_status=ENRICH_STATUS_MATCHED),
            _contact(name="Marked NoMatch", enriched_at=datetime.now(timezone.utc),
                     enrich_status=ENRICH_STATUS_NO_MATCH),
            _contact(name="Still Eligible"),
        ]
    )
    await db_session.commit()

    plan = await plan_enrichment(db_session, limit=50)

    assert plan.eligible == 1
    assert [p.contact_name for p in plan.planned] == ["Still Eligible"]


async def test_plan_counts_unparseable_names_without_spending_a_slot(db_session) -> None:
    await _seed_bank(db_session)
    db_session.add_all([_contact(name="Cher"), _contact(name="Full Name")])
    await db_session.commit()

    plan = await plan_enrichment(db_session, limit=50)

    assert plan.eligible == 2
    assert plan.unparseable == 1
    assert [p.contact_name for p in plan.planned] == ["Full Name"]


# ── Execute: accept / correct / reject / never-overwrite ────────────────────


@respx.mock
async def test_match_accept_fills_null_channels_and_stamps_matched(db_session) -> None:
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json=_apollo_payload())
    )
    await _seed_bank(db_session)
    db_session.add(_contact())
    await db_session.commit()

    client = _client()
    plan = await plan_enrichment(db_session, limit=50)
    stats = await execute_enrichment(db_session, client, plan.planned)

    (row,) = (await db_session.execute(select(BankContact))).scalars().all()
    assert row.email == "john.hirshman@erebor.example"
    assert row.phone == "+12025550100"
    assert row.title == "Chief Executive Officer"
    assert row.enrich_status == ENRICH_STATUS_MATCHED
    assert row.enriched_at is not None
    assert row.name == "John Hirshman"  # exact match -> no correction
    assert (stats.looked_up, stats.matched, stats.no_match) == (1, 1, 0)
    assert (stats.emails_added, stats.phones_added, stats.titles_added) == (1, 1, 1)
    assert stats.names_corrected == 0
    assert stats.credits_used == 1
    stats.eligible = plan.eligible
    assert stats.summary_line() == (
        "bank_contacts_enrich: eligible=1 looked_up=1 matched=1 "
        "emails_added=1 phones_added=1 titles_added=1 names_corrected=0 "
        "credits_used≈1"
    )


@respx.mock
async def test_near_name_variant_recorded_but_name_left_stable(db_session) -> None:
    # M3: Apollo's alternate spelling is recorded in the provenance note, but
    # the stored `name` (the extraction dedupe key) is LEFT UNCHANGED.
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json=_apollo_payload("John", "Hirshman"))
    )
    await _seed_bank(db_session)
    # The PDF text layer rendered 'm' as 'rn' — two edits away.
    db_session.add(
        _contact(name="John Hirshrnan", context_snippet="Contact person: John Hirshrnan")
    )
    await db_session.commit()

    client = _client()
    plan = await plan_enrichment(db_session, limit=50)
    stats = await execute_enrichment(db_session, client, plan.planned)

    (row,) = (await db_session.execute(select(BankContact))).scalars().all()
    assert row.name == "John Hirshrnan"  # dedupe key stays exactly as extracted
    assert row.enrich_status == ENRICH_STATUS_MATCHED
    assert "Apollo match rendered this name as 'John Hirshman'" in row.context_snippet
    assert "John Hirshrnan" in row.context_snippet  # filing rendering survives
    assert row.context_snippet.startswith("Contact person: John Hirshrnan")
    assert stats.names_corrected == 1  # variant recorded (name itself unchanged)
    assert stats.matched == 1


@respx.mock
async def test_enriched_name_variant_does_not_spawn_twin_on_reextraction(db_session) -> None:
    # The M3 must-fix, end to end: enrich a row whose name Apollo spells
    # differently, then re-run the PDF extraction upsert with the ORIGINAL
    # PDF-rendered name. Because enrichment never mutated the dedupe key, the
    # re-extraction lands on the SAME row (idempotent update) instead of
    # inserting a second, PDF-named twin (which would also re-spend a credit).
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json=_apollo_payload("John", "Hirshman"))
    )
    await _seed_bank(db_session)
    # Title present up front so enrichment doesn't fill it — isolates the NAME
    # axis this test is about (title-fill collisions are covered separately).
    db_session.add(
        _contact(
            name="John Hirshrnan",
            title="President",
            context_snippet="Contact person: John Hirshrnan, President",
        )
    )
    await db_session.commit()

    plan = await plan_enrichment(db_session, limit=50)
    await execute_enrichment(db_session, _client(), plan.planned)

    # A subsequent `--extract-contacts` of the same PDF re-derives the SAME
    # PDF-rendered name/title/source.
    service = BankContactExtractionService()
    reextracted = ExtractedBankContact(
        name="John Hirshrnan",
        title="President",
        role_context="contact_person",
        email=None,
        phone=None,
        source_url=_PDF_URL,
        page_number=1,
        context_snippet="Contact person: John Hirshrnan, President",
        source="application_pdf",
    )
    inserted, updated = await service.upsert_contacts(db_session, 1, [reextracted])
    await db_session.commit()

    assert (inserted, updated) == (0, 1)  # matched the existing row, no twin
    rows = (await db_session.execute(select(BankContact))).scalars().all()
    assert len(rows) == 1  # exactly one row for this person
    assert rows[0].name == "John Hirshrnan"
    assert rows[0].email == "john.hirshman@erebor.example"  # enrichment survives


@respx.mock
async def test_reject_different_person_name_fills_nothing(db_session) -> None:
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json=_apollo_payload("Robert", "Smith"))
    )
    await _seed_bank(db_session)
    db_session.add(_contact())
    await db_session.commit()

    client = _client()
    plan = await plan_enrichment(db_session, limit=50)
    stats = await execute_enrichment(db_session, client, plan.planned)

    (row,) = (await db_session.execute(select(BankContact))).scalars().all()
    assert row.email is None and row.phone is None and row.title is None
    assert row.name == "John Hirshman"
    assert row.enrich_status == ENRICH_STATUS_NO_MATCH
    assert row.enriched_at is not None  # attempted: never re-spend the credit
    assert (stats.matched, stats.no_match) == (0, 1)


@respx.mock
async def test_reject_implausible_org_association(db_session) -> None:
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_apollo_payload(
                org_name="Acme Widgets Inc", org_domain="acme.example"
            ),
        )
    )
    await _seed_bank(db_session)
    db_session.add(_contact())
    await db_session.commit()

    client = _client()
    plan = await plan_enrichment(db_session, limit=50)
    stats = await execute_enrichment(db_session, client, plan.planned)

    (row,) = (await db_session.execute(select(BankContact))).scalars().all()
    assert row.email is None
    assert row.enrich_status == ENRICH_STATUS_NO_MATCH
    assert stats.no_match == 1


@respx.mock
async def test_no_person_response_stamps_no_match_without_credit(db_session) -> None:
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json={"person": None})
    )
    await _seed_bank(db_session)
    db_session.add(_contact())
    await db_session.commit()

    client = _client()
    plan = await plan_enrichment(db_session, limit=50)
    stats = await execute_enrichment(db_session, client, plan.planned)

    (row,) = (await db_session.execute(select(BankContact))).scalars().all()
    assert row.enrich_status == ENRICH_STATUS_NO_MATCH
    assert stats.credits_used == 0  # no person returned -> no credit counted


@respx.mock
async def test_never_overwrites_extracted_values(db_session) -> None:
    # Apollo disagrees with the filing on phone AND title — the filing wins.
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_apollo_payload(title="Consultant", phone="+19995550000"),
        )
    )
    await _seed_bank(db_session)
    db_session.add(
        _contact(phone="(202) 555-0100", title="President and Chief Executive Officer")
    )
    await db_session.commit()

    client = _client()
    plan = await plan_enrichment(db_session, limit=50)
    stats = await execute_enrichment(db_session, client, plan.planned)

    (row,) = (await db_session.execute(select(BankContact))).scalars().all()
    assert row.email == "john.hirshman@erebor.example"      # NULL -> filled
    assert row.phone == "(202) 555-0100"                    # extracted -> kept
    assert row.title == "President and Chief Executive Officer"
    assert (stats.emails_added, stats.phones_added, stats.titles_added) == (1, 0, 0)


@respx.mock
async def test_apollo_locked_email_sentinel_is_never_persisted(db_session) -> None:
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(
            200, json=_apollo_payload(email="email_not_unlocked@domain.com")
        )
    )
    await _seed_bank(db_session)
    db_session.add(_contact())
    await db_session.commit()

    client = _client()
    plan = await plan_enrichment(db_session, limit=50)
    stats = await execute_enrichment(db_session, client, plan.planned)

    (row,) = (await db_session.execute(select(BankContact))).scalars().all()
    assert row.email is None
    assert row.enrich_status == ENRICH_STATUS_MATCHED  # name+org fine; channels partial
    assert stats.emails_added == 0
    assert stats.phones_added == 1


@respx.mock
async def test_title_fill_dedupe_collision_keeps_channel_fills_only(db_session) -> None:
    # M3: the name is never mutated, but a TITLE fill can still move the
    # (bank, name, coalesce(title,''), source) key onto an existing row. The
    # guard drops the title fill while keeping the channel fills.
    respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json=_apollo_payload())  # Apollo title = CEO
    )
    await _seed_bank(db_session)
    # Another row already holds (bank, 'John Hirshman', 'Chief Executive
    # Officer', 'application_pdf'); filling the eligible row's title to CEO
    # would trip uq_bank_contacts_dedupe.
    db_session.add(
        _contact(
            name="John Hirshman",
            title="Chief Executive Officer",
            email="already@erebor.example",  # has email -> not eligible itself
        )
    )
    db_session.add(_contact(name="John Hirshman", title=None))  # eligible, no title
    await db_session.commit()

    client = _client()
    plan = await plan_enrichment(db_session, limit=50)
    assert [p.contact_name for p in plan.planned] == ["John Hirshman"]
    stats = await execute_enrichment(db_session, client, plan.planned)

    rows = (await db_session.execute(select(BankContact))).scalars().all()
    enriched = next(r for r in rows if r.email == "john.hirshman@erebor.example")
    assert enriched.title is None            # title fill guarded (would collide)
    assert enriched.name == "John Hirshman"  # name never mutated
    assert enriched.enrich_status == ENRICH_STATUS_MATCHED
    assert stats.names_corrected == 0 and stats.titles_added == 0
    assert stats.emails_added == 1


# ── Idempotency, cap, provider failures ──────────────────────────────────────


@respx.mock
async def test_rerun_is_idempotent_and_spends_nothing(db_session) -> None:
    route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json=_apollo_payload(email=None)),
    )
    await _seed_bank(db_session)
    # One row Apollo matches WITHOUT an email, one row it rejects: both get
    # stamped, so the re-run must re-plan neither.
    db_session.add(_contact(name="John Hirshman"))
    await db_session.commit()

    plan = await plan_enrichment(db_session, limit=50)
    await execute_enrichment(db_session, _client(), plan.planned)
    assert route.call_count == 1

    replan = await plan_enrichment(db_session, limit=50)
    assert replan.eligible == 0
    assert replan.planned == []
    stats = await execute_enrichment(db_session, _client(), replan.planned)
    assert route.call_count == 1  # zero further Apollo calls
    assert stats.looked_up == 0


@respx.mock
async def test_limit_caps_paid_lookups(db_session) -> None:
    route = respx.post(_APOLLO_MATCH_URL).mock(
        return_value=httpx.Response(200, json=_apollo_payload())
    )
    await _seed_bank(db_session)
    db_session.add_all(
        [_contact(name=f"Person Number{i}", role_context="organizer") for i in range(5)]
    )
    await db_session.commit()

    plan = await plan_enrichment(db_session, limit=2)
    assert plan.eligible == 5
    assert len(plan.planned) == 2
    stats = await execute_enrichment(db_session, _client(), plan.planned)

    assert stats.looked_up == 2
    assert route.call_count == 2  # the paid cap, enforced


@respx.mock
async def test_provider_error_leaves_row_unstamped_for_retry(
    db_session, fast_backoff
) -> None:
    respx.post(_APOLLO_MATCH_URL).mock(return_value=httpx.Response(500))
    await _seed_bank(db_session)
    db_session.add(_contact())
    await db_session.commit()

    client = BankContactApolloClient("test-key", max_attempts=2)
    plan = await plan_enrichment(db_session, limit=50)
    stats = await execute_enrichment(db_session, client, plan.planned)

    (row,) = (await db_session.execute(select(BankContact))).scalars().all()
    assert row.enriched_at is None and row.enrich_status is None  # retryable
    assert stats.provider_errors == 1
    assert stats.looked_up == 0

    replan = await plan_enrichment(db_session, limit=50)
    assert replan.eligible == 1  # next run picks it up again


@respx.mock
async def test_consecutive_provider_errors_trip_the_circuit_breaker(
    db_session, fast_backoff
) -> None:
    route = respx.post(_APOLLO_MATCH_URL).mock(return_value=httpx.Response(500))
    await _seed_bank(db_session)
    db_session.add_all(
        [_contact(name=f"Person Number{i}", role_context="organizer") for i in range(5)]
    )
    await db_session.commit()

    client = BankContactApolloClient("test-key", max_attempts=1)
    plan = await plan_enrichment(db_session, limit=50)
    stats = await execute_enrichment(db_session, client, plan.planned)

    # Breaker aborts after 3 consecutive provider errors: contacts 4 and 5
    # are never attempted, so a dead key can't hammer the whole table.
    assert stats.provider_errors == 3
    assert route.call_count == 3


# ── evaluate_match unit coverage (no HTTP, no DB) ───────────────────────────


def test_evaluate_match_reason_codes() -> None:
    from app.services.bank_contact_enrichment import ApolloPersonMatch, PlannedLookup

    item = PlannedLookup(
        contact_id=1,
        bank_id=1,
        bank_name="Erebor Bank, N.A.",
        contact_name="John Hirshman",
        role_context="contact_person",
        first_name="John",
        last_name="Hirshman",
        org_query="Erebor Bank",
        domain="erebor.example",
        missing=("email",),
    )

    def _match(first: str, last: str, org: str | None, dom: str | None) -> ApolloPersonMatch:
        return ApolloPersonMatch(
            first_name=first, last_name=last, title=None, email=None, phone=None,
            organization_name=org, organization_domain=dom,
        )

    assert evaluate_match(item, None) == (False, "no_person")
    assert evaluate_match(item, _match("Robert", "Smith", "Erebor Bank", "erebor.example")) == (
        False, "name_mismatch",
    )
    assert evaluate_match(item, _match("John", "Hirshman", "Acme Widgets Inc", "acme.example")) == (
        False, "org_mismatch",
    )
    assert evaluate_match(item, _match("John", "Hirshman", "Erebor Bank", None)) == (
        True, "accepted",
    )


# ── Migration 20260702_0004 shape (DB-less pins) ────────────────────────────

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _script_directory() -> ScriptDirectory:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_enrichment_migration_is_the_single_head() -> None:
    # The enrichment migration is no longer the head itself — the banks
    # full-directory OCC charter-number index (20260703_0001) now sits
    # directly on top of it — but the chain must stay single-headed.
    script = _script_directory()
    assert script.get_heads() == ["20260703_0001"]
    assert script.get_revision("20260703_0001").down_revision == "20260702_0004"


def test_enrichment_migration_is_an_additive_child_of_bank_contacts() -> None:
    revision = _script_directory().get_revision("20260702_0004")
    assert revision.down_revision == "20260702_0003"


def test_bookkeeping_columns_are_nullable_and_additive() -> None:
    enriched_at = BankContact.__table__.c.enriched_at
    enrich_status = BankContact.__table__.c.enrich_status
    assert enriched_at.nullable and enrich_status.nullable
    assert isinstance(enrich_status.type, String)
    assert enrich_status.type.length == 32
