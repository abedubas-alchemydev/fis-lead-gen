"""Gate tests for the IA refresh orchestrator's ``decide_pipelines``.

Focus: the website gate must re-open when the stored website is a
social/aggregator host (the IAPD bulk import frequently writes those), so
the resolver gets a chance to replace it with the real corporate domain.
"""

from __future__ import annotations

from app.models.investment_advisor import InvestmentAdvisor
from app.services.advisor_refresh_orchestrator import (
    SUB_REFRESH_OWNERS_OFFICERS,
    SUB_RESOLVE_ADVISOR_WEBSITE,
    decide_pipelines,
)


def _advisor(*, website: str | None, executive_officers: object | None = None) -> InvestmentAdvisor:
    advisor = InvestmentAdvisor()
    advisor.website = website
    advisor.executive_officers = executive_officers
    return advisor


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
