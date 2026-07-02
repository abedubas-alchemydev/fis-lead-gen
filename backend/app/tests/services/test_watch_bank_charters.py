"""Unit tests for the pure window resolution in the bank-charter watcher
(``scripts/watch_bank_charters.py``).

Everything else in the script is orchestration over pieces covered by their
own suites (``test_fdic_bankfind.py``, ``test_occ_cas.py``,
``test_banks_repository.py``); the window arithmetic is the part where an
off-by-one or a swapped bound silently turns the nightly run into a no-op,
so it gets pinned here. Import bootstrap mirrors ``test_extract_new_bds.py``
— the script's module top-level is deliberately light (no ``app.*`` imports
at import time).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

# Repo root = backend/app/tests/services/<this file> → parents[4].
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.watch_bank_charters import resolve_window  # noqa: E402

TODAY = date(2026, 7, 2)


def test_default_trailing_window() -> None:
    start, end = resolve_window(from_date=None, to_date=None, window_days=30, today=TODAY)
    assert (start, end) == (date(2026, 6, 2), TODAY)


def test_explicit_backfill_from_date_wins() -> None:
    start, end = resolve_window(
        from_date=date(2024, 1, 1), to_date=None, window_days=30, today=TODAY
    )
    assert (start, end) == (date(2024, 1, 1), TODAY)


def test_explicit_to_date_anchors_the_trailing_window() -> None:
    start, end = resolve_window(
        from_date=None, to_date=date(2026, 3, 31), window_days=7, today=TODAY
    )
    assert (start, end) == (date(2026, 3, 24), date(2026, 3, 31))


def test_backwards_window_raises_instead_of_silently_no_oping() -> None:
    with pytest.raises(ValueError):
        resolve_window(
            from_date=date(2026, 7, 3), to_date=date(2026, 7, 1), window_days=30, today=TODAY
        )
