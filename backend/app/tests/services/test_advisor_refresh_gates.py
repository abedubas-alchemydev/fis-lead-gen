"""Gate tests for the IA refresh orchestrator's ``decide_pipelines``.

Focus: the website gate must re-open when the stored website is a
social/aggregator host (the IAPD bulk import frequently writes those), so
the resolver gets a chance to replace it with the real corporate domain.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.investment_advisor import InvestmentAdvisor
from app.services.advisor_refresh_orchestrator import (
    ADV_REEXTRACT_COOLDOWN,
    SUB_REFRESH_OWNERS_OFFICERS,
    SUB_RESOLVE_ADVISOR_WEBSITE,
    _parse_iso_date,
    _people_to_json,
    decide_pipelines,
)
from app.services.gemini_responses import (
    GeminiAdvisorPerson,
    GeminiAdvisorProfileExtraction,
)


def _advisor(
    *,
    website: str | None = None,
    executive_officers: object | None = None,
    direct_owners: object | None = None,
    client_types: object | None = None,
    firm_operations_text: str | None = None,
    last_enrich_attempt_at: datetime | None = None,
) -> InvestmentAdvisor:
    advisor = InvestmentAdvisor()
    advisor.website = website
    advisor.executive_officers = executive_officers
    advisor.direct_owners = direct_owners
    advisor.client_types = client_types
    advisor.firm_operations_text = firm_operations_text
    advisor.last_enrich_attempt_at = last_enrich_attempt_at
    return advisor


def _complete_advisor(**overrides: object) -> InvestmentAdvisor:
    """An advisor whose ADV profile fields are all populated (owners gate
    closed). Tests override individual fields to re-open it."""
    base: dict[str, object] = {
        "executive_officers": [{"name": "Jane Doe"}],
        "direct_owners": [{"name": "Parent Co"}],
        "client_types": ["Individuals"],
        "firm_operations_text": "Provides advisory services.",
    }
    base.update(overrides)
    return _advisor(**base)  # type: ignore[arg-type]


def test_website_gate_open_when_null() -> None:
    decision = decide_pipelines(_advisor(website=None))
    assert SUB_RESOLVE_ADVISOR_WEBSITE in decision.to_run


def test_website_gate_closed_for_real_corporate_domain() -> None:
    decision = decide_pipelines(_advisor(website="https://institutional.vanguard.com"))
    assert SUB_RESOLVE_ADVISOR_WEBSITE in decision.to_skip
    assert SUB_RESOLVE_ADVISOR_WEBSITE not in decision.to_run


def test_website_gate_reopens_for_social_host() -> None:
    # The IAPD import wrote Vanguard's website as a Twitter/X handle. The
    # gate must treat a blocklisted social host as "needs resolving".
    for bad in (
        "HTTPS://X.COM/VANGUARD_INSTL",
        "https://twitter.com/focusfinancial",
        "https://www.linkedin.com/company/blackstonegroup",
        "https://www.facebook.com/blackstone",
    ):
        decision = decide_pipelines(_advisor(website=bad))
        assert SUB_RESOLVE_ADVISOR_WEBSITE in decision.to_run, bad


def test_owners_gate_independent_of_website() -> None:
    # A firm with a good website but no officers still runs the owners
    # pipeline and skips website.
    decision = decide_pipelines(
        _advisor(website="https://institutional.vanguard.com", executive_officers=None)
    )
    assert SUB_REFRESH_OWNERS_OFFICERS in decision.to_run
    assert SUB_RESOLVE_ADVISOR_WEBSITE in decision.to_skip


# ── owners/ADV gate: incompleteness + re-extract cooldown ──────────────────


def test_owners_gate_open_when_any_profile_field_missing() -> None:
    # Officers present but owners/client_types/operations missing → still open.
    decision = decide_pipelines(
        _complete_advisor(direct_owners=None)
    )
    assert SUB_REFRESH_OWNERS_OFFICERS in decision.to_run


def test_owners_gate_closed_when_profile_complete() -> None:
    decision = decide_pipelines(_complete_advisor())
    assert SUB_REFRESH_OWNERS_OFFICERS in decision.to_skip
    assert SUB_REFRESH_OWNERS_OFFICERS not in decision.to_run


def test_owners_gate_suppressed_by_recent_attempt() -> None:
    # Incomplete profile, but we attempted within the cooldown → don't re-run
    # the paid Gemini extraction (some fields may simply be absent from the ADV).
    recent = datetime.now(timezone.utc) - (ADV_REEXTRACT_COOLDOWN / 2)
    decision = decide_pipelines(
        _advisor(executive_officers=None, last_enrich_attempt_at=recent)
    )
    assert SUB_REFRESH_OWNERS_OFFICERS in decision.to_skip


def test_owners_gate_reopens_after_cooldown_expires() -> None:
    old = datetime.now(timezone.utc) - (ADV_REEXTRACT_COOLDOWN + timedelta(days=1))
    decision = decide_pipelines(
        _advisor(executive_officers=None, last_enrich_attempt_at=old)
    )
    assert SUB_REFRESH_OWNERS_OFFICERS in decision.to_run


# ── pure write helpers ─────────────────────────────────────────────────────


def test_people_to_json_shapes_and_skips_blanks() -> None:
    people = [
        GeminiAdvisorPerson(name="Jane Doe", title="CEO", ownership="50% or more"),
        GeminiAdvisorPerson(name="  ", title="Ghost"),  # blank name dropped
        GeminiAdvisorPerson(name="John Roe"),  # no title/ownership
    ]
    rows = _people_to_json(people)
    assert rows == [
        {"name": "Jane Doe", "title": "CEO", "ownership": "50% or more"},
        {"name": "John Roe"},
    ]


def test_parse_iso_date() -> None:
    assert _parse_iso_date("2001-05-08") is not None
    assert _parse_iso_date("2001-05-08T00:00:00") is not None  # trims to date
    assert _parse_iso_date(None) is None
    assert _parse_iso_date("not a date") is None
    assert _parse_iso_date("") is None


# ── Gemini schema round-trips a realistic payload ──────────────────────────


def test_advisor_profile_model_parses_full_payload() -> None:
    payload = {
        "executive_officers": [{"name": "Mortimer Buckley", "title": "CEO", "ownership": None}],
        "direct_owners": [{"name": "Vanguard Group", "title": "Owner", "ownership": "75% or more"}],
        "indirect_owners": [],
        "client_types": ["Investment companies", "Pooled investment vehicles"],
        "registration_date": "1983-05-01",
        "formation_date": None,
        "firm_operations_text": "Manages mutual funds and ETFs.",
        "confidence_score": 0.92,
        "rationale": "Schedule A + Item 5.D.",
    }
    parsed = GeminiAdvisorProfileExtraction.model_validate(payload)
    assert parsed.executive_officers[0].name == "Mortimer Buckley"
    assert parsed.client_types == ["Investment companies", "Pooled investment vehicles"]
    assert parsed.indirect_owners == []
    assert parsed.confidence_score == 0.92
