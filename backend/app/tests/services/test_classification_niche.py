"""Tests for ``classify_niche_restricted`` — the FINRA Form BD niche flag.

A firm is niche-restricted only if EVERY entry in its types_of_business
list belongs to ``_NICHE_TYPES``. The set was widened on 2026-05-04 to
include 10 single-vertical Form BD Item 12 categories (munis-only,
insurance-only, DPP, mortgage paper, oil & gas, real estate, non-profit
bonds, CD solicitor) on top of the original 6 entries (private
placements + investment advisory variants).

Tests below lock the contract so future PRs can't accidentally widen
the regex into mainstream territory or shrink it back below the
2026-05-04 set without breaking visible product behaviour.
"""

from __future__ import annotations

from app.services.classification import classify_niche_restricted


# Pre-existing behaviour — keep working


def test_returns_false_when_types_is_none() -> None:
    assert classify_niche_restricted(None) is False


def test_returns_false_when_types_is_empty_list() -> None:
    assert classify_niche_restricted([]) is False


def test_original_private_placement_only_firm_still_flags_niche() -> None:
    assert classify_niche_restricted(["Private placements of securities"]) is True


def test_original_investment_advisory_only_firm_still_flags_niche() -> None:
    assert classify_niche_restricted(["Investment advisory services"]) is True


# New widened categories (2026-05-04)


def test_municipal_securities_broker_only_flags_niche() -> None:
    assert classify_niche_restricted(["Municipal securities broker"]) is True


def test_municipal_securities_broker_and_dealer_combo_flags_niche() -> None:
    assert (
        classify_niche_restricted(
            ["Municipal securities broker", "Municipal securities dealer"]
        )
        is True
    )


def test_variable_life_insurance_only_flags_niche() -> None:
    assert (
        classify_niche_restricted(
            ["Broker or dealer selling variable life insurance or annuities"]
        )
        is True
    )


def test_oil_and_gas_only_flags_niche() -> None:
    assert (
        classify_niche_restricted(["Broker or dealer selling oil and gas interests"])
        is True
    )


def test_real_estate_syndicator_only_flags_niche() -> None:
    assert classify_niche_restricted(["Real estate syndicator"]) is True


def test_non_profit_securities_only_flags_niche() -> None:
    assert (
        classify_niche_restricted(
            [
                "Broker or dealer selling securities of non-profit organizations "
                "(e.g., churches, hospitals)"
            ]
        )
        is True
    )


def test_dpp_primary_only_flags_niche() -> None:
    assert (
        classify_niche_restricted(
            [
                "Broker or dealer selling tax shelters or limited partnerships "
                "in primary distributions"
            ]
        )
        is True
    )


def test_dpp_secondary_only_flags_niche() -> None:
    assert (
        classify_niche_restricted(
            [
                "Broker or dealer selling tax shelters or limited partnerships "
                "in the secondary market"
            ]
        )
        is True
    )


def test_mortgage_paper_only_flags_niche() -> None:
    assert (
        classify_niche_restricted(
            ["Broker or dealer selling interests in mortgages or other receivables"]
        )
        is True
    )


def test_cd_solicitor_only_flags_niche() -> None:
    assert (
        classify_niche_restricted(
            ["Solicitor of time deposits in a financial institution"]
        )
        is True
    )


# Mainstream types must NOT flag niche


def test_mainstream_corporate_equity_does_not_flag_niche() -> None:
    assert (
        classify_niche_restricted(
            ["Broker or dealer retailing corporate equity securities over-the-counter"]
        )
        is False
    )


def test_mainstream_mutual_fund_retailer_does_not_flag_niche() -> None:
    assert classify_niche_restricted(["Mutual fund retailer"]) is False


def test_one_mainstream_type_disqualifies_otherwise_niche_firm() -> None:
    """The classifier requires ALL types to be niche. A munis-only firm
    that ALSO does corporate equities is NOT niche."""
    assert (
        classify_niche_restricted(
            [
                "Municipal securities broker",
                "Broker or dealer retailing corporate equity securities over-the-counter",
            ]
        )
        is False
    )


def test_municipal_plus_investment_advisory_flags_niche() -> None:
    """Both in the niche set, so the firm qualifies — even though munis
    + advisory is a real-world combo. This is intentional: 'niche'
    means every business line is single-vertical specialist, not that
    the firm only does one thing."""
    assert (
        classify_niche_restricted(
            ["Municipal securities broker", "Investment advisory services"]
        )
        is True
    )


# Casing tolerance


def test_matching_is_case_insensitive() -> None:
    """The classifier lowercases each type before comparison; the
    underlying _NICHE_TYPES set stores lowercase strings. ALL-CAPS data
    from FINRA's mixed casing must still match."""
    assert classify_niche_restricted(["MUNICIPAL SECURITIES BROKER"]) is True
    assert classify_niche_restricted(["Municipal Securities Broker"]) is True
