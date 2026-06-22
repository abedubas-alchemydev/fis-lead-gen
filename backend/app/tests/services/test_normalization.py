"""Unit tests for the pure normalization helpers.

Covers ``normalize_sec_file_number`` / ``is_canonical_sec_file_number`` /
``normalize_entity_name`` (and the ``_to_canonical_sec_number`` guard they
delegate to). These are pure string transforms — no DB, no async, no
fixtures — so the file mirrors the parametrize style of
``test_competitors.py``.

All expected outputs below were confirmed by running the real functions;
where a result is a surprising-but-current behavior (em/en-dash inputs
collapsing to ``None``), the case is annotated so a future reader knows it
pins observed behavior, not an aspiration.
"""

from __future__ import annotations

import pytest

from app.services.normalization import (
    is_canonical_sec_file_number,
    normalize_entity_name,
    normalize_sec_file_number,
)

# ---------------------------------------------------------------------------
# normalize_sec_file_number — canonical + variant inputs collapse to "8-N"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "8-12345",            # already canonical
        "08-012345",          # leading zeros on both segments
        "008-12345",          # leading zeros on the prefix
        "812345",             # compact, no dash
        "8 - 12345",          # spaced dash
        "SEC File No. 8-12345",  # noise prefix words
        "File Number: 8-12345",  # noise prefix words + colon
        "BD 8-12345",         # broker-dealer noise prefix
    ],
)
def test_normalize_sec_file_number_canonicalizes_variants(raw: str) -> None:
    assert normalize_sec_file_number(raw) == "8-12345"


def test_normalize_sec_file_number_strips_leading_zeros_single_digit() -> None:
    assert normalize_sec_file_number("08-00007") == "8-7"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("abc 8-12345 xyz", "8-12345"),       # embedded between word chars
        ("Filed under 8 - 777 today", "8-777"),  # embedded, spaced dash
    ],
)
def test_normalize_sec_file_number_embedded_dashed_match(raw: str, expected: str) -> None:
    # Exercises the third fallback (_SEC_EMBEDDED_DASHED_PATTERN) when the
    # direct dashed/compact anchors don't match the whole cleaned string.
    assert normalize_sec_file_number(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n "])
def test_normalize_sec_file_number_blank_inputs_return_none(raw) -> None:
    assert normalize_sec_file_number(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "9-12345",   # wrong prefix (not an 8-series broker-dealer number)
        "12-99",     # wrong prefix
        "abc",       # no digits at all
    ],
)
def test_normalize_sec_file_number_non_eight_prefix_returns_none(raw: str) -> None:
    assert normalize_sec_file_number(raw) is None


def test_normalize_sec_file_number_zero_numeric_guard() -> None:
    # ``_to_canonical_sec_number`` rejects numeric <= 0, so "8-0" is invalid.
    assert normalize_sec_file_number("8-0") is None
    assert normalize_sec_file_number("08-0000") is None


def test_normalize_sec_file_number_unicode_only_string_returns_none() -> None:
    # A string whose characters all drop out under NFKD -> ascii("ignore")
    # leaves an empty value and short-circuits to None.
    assert normalize_sec_file_number("中文") is None


@pytest.mark.parametrize("dash", ["–", "—"])
def test_normalize_sec_file_number_unicode_dash_inputs_canonicalize(dash: str) -> None:
    # The NFKD/ascii("ignore") pass strips en/em dashes, leaving "812345",
    # which the compact-number fallback pattern then canonicalizes. So a
    # unicode-dashed file number still resolves rather than dropping out.
    assert normalize_sec_file_number(f"8{dash}12345") == "8-12345"


# ---------------------------------------------------------------------------
# is_canonical_sec_file_number — strict "8-[1-9]N" check, .strip() tolerant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["8-1", "8-12345", "8-9", " 8-1 "])
def test_is_canonical_true_for_canonical_forms(raw: str) -> None:
    # The trailing/leading whitespace case confirms the internal .strip().
    assert is_canonical_sec_file_number(raw) is True


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "08-12345",   # leading zero is not canonical
        "8-0",        # leading-digit-zero disallowed by [1-9]
        "8-01",       # second segment can't start with 0
        "9-1",        # wrong prefix
        "8-1 extra",  # internal garbage after strip still fails the anchor
        "BD 8-1",     # noise words are not canonical input
    ],
)
def test_is_canonical_false_for_non_canonical(raw) -> None:
    assert is_canonical_sec_file_number(raw) is False


# ---------------------------------------------------------------------------
# normalize_entity_name — ascii-fold, lowercase, strip suffixes, collapse ws
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, ""])
def test_normalize_entity_name_blank_returns_empty_string(raw) -> None:
    assert normalize_entity_name(raw) == ""


def test_normalize_entity_name_strips_suffixes_and_articles() -> None:
    assert (
        normalize_entity_name("The Goldman Sachs Group, Inc.")
        == "goldman sachs group"
    )


def test_normalize_entity_name_lowercases_and_strips_punctuation() -> None:
    assert normalize_entity_name("Apex Clearing Corp.") == "apex clearing"


def test_normalize_entity_name_folds_accents_to_ascii() -> None:
    # "Café" -> "cafe"; the "Corp" suffix token is dropped.
    assert normalize_entity_name("Café Corp") == "cafe"


def test_normalize_entity_name_collapses_repeated_whitespace() -> None:
    # The single-letter "a" is itself a noise token, so it is removed.
    assert normalize_entity_name("A  B   C  Inc") == "b c"


def test_normalize_entity_name_only_noise_tokens_returns_empty() -> None:
    assert normalize_entity_name("The Company LLC") == ""


@pytest.mark.parametrize(
    "suffix",
    [
        "inc",
        "llc",
        "lp",
        "corp",
        "corporation",
        "company",
        "co",
        "ltd",
        "limited",
        "plc",
    ],
)
def test_normalize_entity_name_drops_each_corporate_suffix(suffix: str) -> None:
    assert normalize_entity_name(f"Acme {suffix}") == "acme"
