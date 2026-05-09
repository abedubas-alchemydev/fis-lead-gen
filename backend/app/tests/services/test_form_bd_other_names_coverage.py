"""Regression: FINRA ``firm_other_names`` covers Form BD Item 1.B.

Locks the assumption — verified empirically on 2026-05-09 by running
``scripts/diag_form_bd_other_names_probe.py`` against six known multi-name
broker-dealers — that FINRA's BrokerCheck search endpoint exposes the
firm's Form BD "Other Names of this Firm" entries (Item 1.B) inside the
``firm_other_names`` payload, alongside historical predecessor / acquired
firm names.

If this test fails, FINRA's endpoint behavior has likely changed:
``firm_other_names`` no longer includes Form BD Item 1.B entries, and the
website resolver is missing brand-name tokens it relied on (e.g.
``firstclearing.com``, ``etrade.com``). The remediation is documented in
``~/.claude/plans/plan-do-the-best-golden-lobster.md``: re-run the probe
to confirm, then implement Path B (parse Form BD PDF "Other Names of
this Firm" subsection in ``brokercheck_pdf.py`` and merge into
``dba_names``).

Each fixture below is the raw ``firm_other_names`` value from the live
FINRA search endpoint, captured at the time of test authorship. The
``form_bd_other_names`` set is the firm-filed alternate-name list
extracted from the firm's Form BD Detailed Report PDF — the canonical
ground truth for Item 1.B.
"""

from __future__ import annotations

import pytest

from app.services.finra import FinraService


# Frozen FINRA payloads + Form BD ground truth, captured 2026-05-09 via
# ``scripts/diag_form_bd_other_names_probe.py``. Form BD Item 1.B values
# are extracted from the firm's Detailed Report PDF (the deterministic
# regulator-filed source, not subject to BrokerCheck endpoint drift).
_FIXTURES: list[tuple[str, str, str, list[str], list[str]]] = [
    # (crd, label, legal_name, finra_payload, form_bd_other_names)
    (
        "19616",
        "Wells Fargo Clearing Services",
        "WELLS FARGO CLEARING SERVICES, LLC",
        [
            "EVEREN SECURITIES, INC.",
            "WELLS FARGO CLEARING SERVICES, LLC",
            "WELLS FARGO ADVISORS, LLC",
            "WELLS FARGO ADVISORS",
            "WACHOVIA SECURITIES, LLC",
            "WACHOVIA SECURITIES, INC.",
            "KEMPER SECURITIES GROUP, INC.",
            "KEMPER CAPITAL MARKETS, INC.",
            "FIRST UNION SECURITIES, INC.",
            "FIRST CLEARING",
        ],
        ["FIRST CLEARING", "WELLS FARGO ADVISORS"],
    ),
    (
        "149777",
        "Morgan Stanley Smith Barney",
        "MORGAN STANLEY SMITH BARNEY LLC",
        [
            "CITIGROUP INSTITUTIONAL CONSULTING",
            "SMITH BARNEY",
            "PRIVATE PORTFOLIO GROUP",
            "MORGAN STANLEY WEALTH MANAGEMENT",
            "MORGAN STANLEY SMITH BARNEY LLC",
            "MORGAN STANLEY SMITH BARNEY",
            "MORGAN STANLEY PRIVATE WEALTH MANAGEMENT",
            "MORGAN STANLEY CONSULTING GROUP",
            "MORGAN STANLEY",
            "GRAYSTONE CONSULTING",
            "E*TRADE FROM MORGAN STANLEY",
            "CONSULTING GROUP",
        ],
        [
            "E*TRADE FROM MORGAN STANLEY",
            "GRAYSTONE CONSULTING",
            "MORGAN STANLEY CONSULTING GROUP",
            "MORGAN STANLEY PRIVATE WEALTH MANAGEMENT",
            "MORGAN STANLEY SMITH BARNEY",
            "MORGAN STANLEY WEALTH MANAGEMENT",
        ],
    ),
    (
        "793",
        "Stifel Nicolaus",
        "STIFEL, NICOLAUS & COMPANY, INCORPORATED",
        [
            "EATON PARTNERS",
            "WASHINGTON CROSSING ADVISORS",
            "TWP PRIVATE WEALTH MANAGEMENT",
            "STIFEL, NICOLAUS & COMPANY, INCORPORATED",
            "STIFEL NICOLAUS & CO INC INVESTMENT SERVICES",
            "STIFEL CAPITAL ADVISORS",
        ],
        ["EATON PARTNERS"],
    ),
    (
        "19585",
        "HSBC Securities",
        "HSBC SECURITIES (USA) INC.",
        [
            "CARROLL MCENTEE & MCGINLEY INCORPORATED",
            "INVESTDIRECT",
            "HSBC SECURITIES, INC.",
            "HSBC SECURITIES (USA) INC.",
        ],
        ["INVESTDIRECT"],
    ),
    (
        "19714",
        "Barclays Capital",
        "BARCLAYS CAPITAL INC.",
        [
            "BARCLAYS",
            "WEALTH AND INVESTMENT MANAGEMENT, AMERICAS",
            "WEALTH AND INVESTMENT MANAGEMENT A DIVISION OF BARCLAYS",
            "FUNDS AND ADVISORY-AMERICAS",
            "BZW SECURITIES INC.",
            "BCFS - AMERICAS",
            "BARCLAYS WEALTH AND INVESTMENT MANAGEMENT, AMERICAS",
            "BARCLAYS WEALTH AMERICAS",
            "BARCLAYS WEALTH",
            "BARCLAYS SECURITIES INC.",
            "BARCLAYS SECURITIES INC",
            "BARCLAYS DE ZOETE WEDD SECURITIES INC.",
            "BARCLAYS DE ZOETE WEDD GOVERNMENT SECURITIES, INC.",
            "BARCLAYS CAPITAL INC.",
            "BARCLAYS CAPITAL FUND SOLUTIONS - AMERICAS",
            "BARCLAYS CAPITAL",
        ],
        ["BARCLAYS CAPITAL", "BARCLAYS SECURITIES INC."],
    ),
    (
        "31194",
        "RBC Capital Markets",
        "RBC CAPITAL MARKETS, LLC",
        [
            "DAIN RAUSCHER INC",
            "REGIONAL OPERATIONS GROUP, INC.",
            "RBC WEALTH MANAGEMENT",
            "RBC DAIN RAUSCHER INC.",
            "RBC DAIN RAUSCHER",
            "RBC CORRESPONDENT SERVICES",
            "RBC CLEARING AND CUSTODY",
            "RBC CAPITAL MARKETS, LLC",
            "RBC CAPITAL MARKETS CORPORATION",
            "RBC ADVISOR SERVICES",
            "INTERRA CLEARING SERVICES, INC.",
            "DAIN RAUSCHER INCORPORATED",
        ],
        [
            "RBC ADVISOR SERVICES",
            "RBC CORRESPONDENT SERVICES",
            "RBC WEALTH MANAGEMENT",
        ],
    ),
]


@pytest.mark.parametrize(
    "crd,label,legal_name,finra_payload,form_bd_other_names",
    _FIXTURES,
    ids=[f[0] for f in _FIXTURES],
)
def test_finra_other_names_covers_form_bd_item_1b(
    crd: str,
    label: str,
    legal_name: str,
    finra_payload: list[str],
    form_bd_other_names: list[str],
) -> None:
    """Every Form BD "Other Names of this Firm" entry is present in the
    parsed FINRA ``firm_other_names`` set.

    The website resolver consumes ``dba_names`` (parsed from
    ``firm_other_names``) for domain-anchor token coverage. As long as
    this test passes, no separate Form BD PDF "Other Names" extractor
    is needed — FINRA's endpoint already delivers that data alongside
    historical predecessor names.
    """
    parsed = FinraService._parse_dba_names(finra_payload, legal_name=legal_name)
    assert parsed is not None, (
        f"CRD {crd} ({label}): _parse_dba_names returned None for non-empty "
        f"FINRA payload — parser regression."
    )

    parsed_norm = {" ".join(name.lower().split()) for name in parsed}
    missing = [
        name
        for name in form_bd_other_names
        if " ".join(name.lower().split()) not in parsed_norm
    ]
    assert not missing, (
        f"CRD {crd} ({label}): Form BD Item 1.B 'Other Names of this Firm' "
        f"entries not present in parsed FINRA firm_other_names: {missing}. "
        f"Re-run scripts/diag_form_bd_other_names_probe.py and consider Path B "
        f"(see ~/.claude/plans/plan-do-the-best-golden-lobster.md)."
    )


def test_fixture_self_consistency() -> None:
    """Sanity: every fixture's Form BD list is non-empty (the whole point
    of this regression suite is multi-name firms)."""
    for crd, label, _legal, _payload, form_bd in _FIXTURES:
        assert form_bd, (
            f"CRD {crd} ({label}): fixture has empty form_bd_other_names — "
            f"this CRD shouldn't be in the multi-name regression set."
        )
