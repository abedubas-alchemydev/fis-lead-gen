"""Unit tests for ``extract_other_business_names`` — the IA analog of the BD
``FinraService._parse_dba_names`` parser.

It pulls Form ADV Schedule D Section 1.B "Other Business Names" out of the
IAPD per-firm ``iacontent`` payload (``basicInformation.otherNames``),
dropping the firm's own primary + legal name (the IAPD array routinely
repeats the primary name) and de-duping. The parser fails closed to ``None``
so a wrong path guess degrades to "no data" rather than an error — and it is
independent of ``registrationStatus.effectiveDate``, which is the invariant
the refresh sub-pipeline's reorder relies on (other-names are written even
when a firm has no effectiveDate).
"""

from __future__ import annotations

from app.services.advisor_refresh_orchestrator import extract_other_business_names


def _extract(
    other_names: object,
    *,
    primary: str | None = "ACME ADVISORS LLC",
    legal: str | None = None,
) -> list[str] | None:
    return extract_other_business_names(
        {"basicInformation": {"otherNames": other_names}},
        primary_name=primary,
        legal_name=legal,
    )


def test_none_iacontent_returns_none() -> None:
    assert extract_other_business_names(None, primary_name="X", legal_name=None) is None


def test_missing_basic_information_and_top_level_returns_none() -> None:
    assert extract_other_business_names({}, primary_name="X", legal_name=None) is None


def test_other_names_not_a_list_returns_none() -> None:
    assert (
        extract_other_business_names(
            {"basicInformation": {"otherNames": "ACME CAPITAL"}},
            primary_name="X",
            legal_name=None,
        )
        is None
    )


def test_reads_basic_information_other_names() -> None:
    assert _extract(["ACME ADVISORS LLC", "ACME CAPITAL"]) == ["ACME CAPITAL"]


def test_falls_back_to_top_level_other_names() -> None:
    # Some payloads flatten the array to a top-level key (no basicInformation).
    assert (
        extract_other_business_names(
            {"otherNames": ["ACME ADVISORS LLC", "ACME CAPITAL"]},
            primary_name="ACME ADVISORS LLC",
            legal_name=None,
        )
        == ["ACME CAPITAL"]
    )


def test_drops_primary_name_case_and_whitespace_insensitive() -> None:
    # The array repeats the firm's own primary name (common) — dropped
    # regardless of case / extra internal whitespace.
    assert _extract(
        ["  acme   advisors llc ", "Acme Wealth"], primary="ACME ADVISORS LLC"
    ) == ["Acme Wealth"]


def test_drops_legal_name() -> None:
    assert _extract(
        ["ACME HOLDINGS INC", "Acme Wealth"],
        primary="ACME ADVISORS LLC",
        legal="Acme Holdings Inc",
    ) == ["Acme Wealth"]


def test_dedupes_case_insensitively() -> None:
    assert _extract(["Acme Wealth", "ACME WEALTH", "acme  wealth"]) == ["Acme Wealth"]


def test_strips_dba_prefix() -> None:
    assert _extract(["d/b/a Acme Wealth", "DBA Acme Capital"]) == [
        "Acme Wealth",
        "Acme Capital",
    ]


def test_empty_array_returns_none() -> None:
    assert _extract([]) is None


def test_all_entries_dropped_returns_none() -> None:
    # Only the firm's own name survives filtering → nothing usable → None, so
    # the column stays NULL and the FE hides the section.
    assert _extract(["ACME ADVISORS LLC"]) is None


def test_skips_blank_entries() -> None:
    assert _extract(["", "   ", "Acme Wealth"]) == ["Acme Wealth"]
