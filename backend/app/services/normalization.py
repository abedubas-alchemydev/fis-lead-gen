from __future__ import annotations

import re
import unicodedata


# Canonical SEC broker-dealer file-number format: 8-N (no leading zeros).
# Input variants seen in source data include:
#   "8-12345", "08-012345", "008-12345", "812345", "8 - 12345",
#   "SEC File No. 8-12345", "File Number: 8-12345", "BD 8-12345"
#
# Step 1: direct cleanup before canonical parsing.
_SEC_DIRECT_DASHED_PATTERN = re.compile(r"^0*8\s*-\s*0*(\d+)$")

# Step 2: fallback for numbers embedded in longer strings.
_SEC_DIRECT_COMPACT_PATTERN = re.compile(r"^0*8\s*0*(\d+)$")
_SEC_EMBEDDED_DASHED_PATTERN = re.compile(r"(?:^|[^0-9])0*8\s*-\s*0*(\d+)(?:[^0-9]|$)")
_SEC_CANONICAL_PATTERN = re.compile(r"^8-[1-9][0-9]*$")
_SEC_FILE_NUMBER_NOISE = re.compile(
    r"(?i)\b(sec|file|number|no|bd|form|broker|dealer)\b"
)


def normalize_sec_file_number(value: str | None) -> str | None:
    """Normalize SEC broker-dealer file numbers into canonical ``8-N`` format."""
    if value is None:
        return None

    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    if not ascii_value:
        return None

    cleaned = ascii_value.replace("\u2013", "-").replace("\u2014", "-")
    cleaned = _SEC_FILE_NUMBER_NOISE.sub(" ", cleaned)
    cleaned = re.sub(r"[#:/,.;()\[\]{}]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    direct_match = _SEC_DIRECT_DASHED_PATTERN.match(cleaned)
    if direct_match:
        return _to_canonical_sec_number(direct_match.group(1))

    compact_value = cleaned.replace(" ", "")
    direct_compact_match = _SEC_DIRECT_COMPACT_PATTERN.match(compact_value)
    if direct_compact_match:
        return _to_canonical_sec_number(direct_compact_match.group(1))

    embedded_match = _SEC_EMBEDDED_DASHED_PATTERN.search(cleaned)
    if embedded_match:
        return _to_canonical_sec_number(embedded_match.group(1))

    return None


# ──────────────────────────────────────────────────────────────
# Entity-name normalization for fuzzy matching.
# ──────────────────────────────────────────────────────────────

def is_canonical_sec_file_number(value: str | None) -> bool:
    if not value:
        return False
    return bool(_SEC_CANONICAL_PATTERN.match(value.strip()))


def _to_canonical_sec_number(digits: str | None) -> str | None:
    if not digits:
        return None
    try:
        numeric = int(digits)
    except ValueError:
        return None
    if numeric <= 0:
        return None
    return f"8-{numeric}"


_ENTITY_NOISE_TOKENS = frozenset({
    "inc", "llc", "lp", "corp", "corporation", "company", "co",
    "ltd", "limited", "plc", "the", "of", "and", "a",
})


def normalize_entity_name(value: str | None) -> str:
    """Normalize a company name for fuzzy matching.

    - Converts to ASCII lowercase
    - Strips punctuation
    - Removes common corporate suffixes (Inc, LLC, Corp, etc.)
    - Collapses whitespace
    """
    if not value:
        return ""

    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", ascii_value.lower())
    tokens = [
        token
        for token in normalized.split()
        if token and token not in _ENTITY_NOISE_TOKENS
    ]
    return " ".join(tokens)
