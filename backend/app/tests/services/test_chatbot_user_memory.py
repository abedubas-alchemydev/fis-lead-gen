"""Unit tests for ``app.services.chatbot_user_memory``.

Pins the pure normalization that keys per-user de-dupe (the same role
``normalize_term`` plays for the global glossary) plus the two
``create_memory`` branches that don't need real SQL execution semantics:
the empty-content no-op and the de-dupe short-circuit (an existing row is
returned instead of inserting a near-identical one). The full insert / cap /
ordering round-trip exercises real Postgres in
``app/tests/api/test_doxie_memory.py`` (integration-marked).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.chatbot_user_memory import (
    MAX_MEMORY_CONTENT_CHARS,
    create_memory,
    normalize_content,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Self-Clearing", "self-clearing"),
        ("  Focus on TX  ", "focus on tx"),
        ("Fee   is   25bps", "fee is 25bps"),
        ("ALWAYS draft\nformally", "always draft formally"),
        ("\tprefers email\t", "prefers email"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_content(raw: str, expected: str) -> None:
    assert normalize_content(raw) == expected


class _DedupeSession:
    """Fake AsyncSession whose first ``execute`` (the de-dupe SELECT) returns
    an existing row, so ``create_memory`` short-circuits before any insert."""

    def __init__(self, existing: object) -> None:
        scalars = MagicMock()
        scalars.first.return_value = existing
        result = MagicMock()
        result.scalars.return_value = scalars
        self.execute = AsyncMock(return_value=result)
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()


async def test_empty_content_is_a_noop() -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    result = await create_memory(db, user_id="u1", content="   ")
    assert result is None
    db.execute.assert_not_awaited()


async def test_dedupe_returns_existing_without_insert() -> None:
    existing = SimpleNamespace(id=7, content="Focus on TX")
    db = _DedupeSession(existing)

    result = await create_memory(db, user_id="u1", content="  focus on TX ")

    assert result is existing
    # De-dupe hit short-circuits: only the SELECT ran, no insert/commit.
    db.execute.assert_awaited_once()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


def test_content_cap_is_a_sane_bound() -> None:
    # Guards the schema description (which advertises the same number) against
    # drifting away from the storage cap.
    assert MAX_MEMORY_CONTENT_CHARS == 2000
