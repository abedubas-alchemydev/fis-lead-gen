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


def test_bulk_zip_streaming_uses_aiter_raw_not_aiter_bytes() -> None:
    """The bulk submissions ZIP must be streamed via ``aiter_raw``, not
    ``aiter_bytes``.

    Identity-encoding is the right hint to send (above test), but Akamai POPs
    that serve GCP egress IPs ignore it and reply with ``Content-Encoding:
    gzip`` anyway. ``aiter_bytes`` auto-decompresses based on the Content-
    Encoding header and surfaces ~1500 "Data-loss while decompressing
    corrupted data" errors per 1.5 GB download (one per chunk).

    ``aiter_raw`` yields the bytes off the wire verbatim. Since the body is
    a ``.zip`` file (already application-layer compressed), the bytes are
    valid for ``zipfile.ZipFile`` regardless of any bogus transport-layer
    Content-Encoding header.

    A future "let's modernize the streaming code" PR that swaps back to
    ``aiter_bytes`` will fail this test before it can ship.
    """
    source = inspect.getsource(edgar_module)
    # Match the actual call site (the `response.aiter_*(...)` line), not bare
    # mentions in comments.
    assert "response.aiter_raw(" in source, (
        "Bulk ZIP download must use response.aiter_raw(...) to bypass httpx's "
        "auto-decompression. Akamai-from-GCP-egress sets Content-Encoding: "
        "gzip on the .zip body which causes aiter_bytes to fail per-chunk."
    )
    # Defence-in-depth: assert no actual response.aiter_bytes(...) call exists
    # (comments mentioning aiter_bytes for documentation are fine). A future
    # PR that swaps the call back to aiter_bytes fails this test.
    assert "response.aiter_bytes(" not in source, (
        "edgar.py invokes response.aiter_bytes(...) somewhere — that auto-"
        "decompresses based on Content-Encoding. SEC EDGAR via GCP egress "
        "sends bogus gzip headers; use response.aiter_raw(...) instead."
    )
