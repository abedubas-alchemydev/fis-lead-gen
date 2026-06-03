"""Unit tests for ``app.services.chatbot_learned_terms``.

The glossary's DB round-trip (``upsert_learned_term`` / ``get_learned_term``)
exercises Postgres ``ON CONFLICT`` and is covered by the migration running in
CI plus the ``research_term`` handler tests (which mock these helpers). Here
we pin the pure normalization that keys the glossary — get this wrong and
the same term researched twice would store two rows.
"""

from __future__ import annotations

import pytest

from app.services.chatbot_learned_terms import normalize_term


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SOFR", "sofr"),
        ("  Reg BI  ", "reg bi"),
        ("T+1   Settlement", "t+1 settlement"),
        ("CcP", "ccp"),
        ("\tFINRA\nRule 4530\t", "finra rule 4530"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_term(raw: str, expected: str) -> None:
    assert normalize_term(raw) == expected
