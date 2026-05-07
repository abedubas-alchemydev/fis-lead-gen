"""Regression test: FINRA BrokerCheck PDF fetcher must use identity + aiter_raw.

httpx auto-negotiates ``Accept-Encoding: gzip, deflate, br, zstd`` by default,
but FINRA's Cloudflare gateway responds with malformed compressed bodies that
cause pdfminer to surface ``Data-loss while decompressing corrupted data``
warnings on every Flate stream inside the PDF — the bytes are silently
mangled by httpx's auto-decompressor before pdfminer ever sees them. The fix
mirrors ``services/edgar.py`` and the SEC PDF path in
``services/pdf_downloader.py``: force ``Accept-Encoding: identity`` and read
via ``response.aiter_raw`` so bytes flow off the wire verbatim.

Source-inspection assertions are the right shape — counting the right strings
in the module is unambiguous, fast, and survives any test-mocking peculiarities
of ``httpx.AsyncClient``. A future "header cleanup" or "let's modernize the
streaming code" PR that drops either guard fails this test before it can ship.
"""

from __future__ import annotations

import inspect

from app.services import finra_pdf_service


def test_fetch_brokercheck_pdf_pins_accept_encoding_identity() -> None:
    source = inspect.getsource(finra_pdf_service)
    assert source.count('"Accept-Encoding": "identity"') >= 1, (
        "fetch_brokercheck_pdf must pin Accept-Encoding: identity. FINRA's "
        "Cloudflare gateway sends malformed compressed bodies that surface "
        "as 'Data-loss while decompressing corrupted data' warnings inside "
        "pdfminer once httpx silently mangles the PDF bytes. Same fix as "
        "services/edgar.py and the SEC PDF path in services/pdf_downloader.py."
    )


def test_fetch_brokercheck_pdf_uses_aiter_raw_not_aiter_bytes() -> None:
    source = inspect.getsource(finra_pdf_service)
    assert "response.aiter_raw(" in source, (
        "fetch_brokercheck_pdf must use response.aiter_raw(...) to bypass "
        "httpx's auto-decompression. Identity is the right hint to send, but "
        "Cloudflare sometimes sends Content-Encoding: gzip on PDF bodies "
        "anyway; aiter_bytes auto-decompresses based on that header and "
        "corrupts already-application-compressed PDF streams."
    )
    assert "response.aiter_bytes(" not in source, (
        "finra_pdf_service.py invokes response.aiter_bytes(...) somewhere — "
        "that auto-decompresses based on Content-Encoding. Use aiter_raw."
    )
