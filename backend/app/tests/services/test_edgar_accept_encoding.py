"""Regression test: every EDGAR HTTP entry point must send ``Accept-Encoding: identity``.

httpx auto-negotiates ``Accept-Encoding: gzip, deflate, br, zstd`` by default,
but SEC EDGAR's Cloudflare gateway returns malformed compressed bodies that
raise ``Data-loss while decompressing corrupted data`` on every request — the
exact same gateway issue that bit FINRA. The fix mirrors ``services/finra.py``:
force ``identity`` on every outbound request.

:class:`EdgarService` builds three separate ``headers`` dicts:

* ``_fetch_via_company_search`` — SIC-filtered enumeration.
* ``fetch_records_for_sec_numbers`` — per-firm browse-edgar fallback.
* ``_ensure_bulk_submissions_zip`` — bulk submissions ZIP download.

Each one needs the override. We assert this by source inspection rather than
through a mocked HTTP harness: counting ``"Accept-Encoding": "identity"`` in
the module source is unambiguous, fast, and survives any test-mocking
peculiarities of the underlying methods. A "header cleanup" PR that drops any
of the three lines fails this test before it can ship.
"""

from __future__ import annotations

import inspect

from app.services import edgar as edgar_module


def test_edgar_service_module_pins_accept_encoding_identity_in_three_places() -> None:
    source = inspect.getsource(edgar_module)
    occurrences = source.count('"Accept-Encoding": "identity"')
    assert occurrences == 3, (
        "EdgarService must pin Accept-Encoding: identity in all three header "
        "dicts (_fetch_via_company_search, fetch_records_for_sec_numbers, "
        "_ensure_bulk_submissions_zip). Found "
        f"{occurrences} occurrence(s); expected 3. SEC EDGAR's Cloudflare "
        "gateway returns malformed compressed bodies that raise 'Data-loss "
        "while decompressing corrupted data' on every default-encoding "
        "request. Same fix as services/finra.py — see commit history."
    )
