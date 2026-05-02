"""Regression tests: EDGAR HTTP requests must send ``Accept-Encoding: identity``.

httpx auto-negotiates ``Accept-Encoding: gzip, deflate, br, zstd`` by default,
but SEC EDGAR's Cloudflare gateway returns malformed compressed bodies that
raise ``Data-loss while decompressing corrupted data`` on every request — the
exact same gateway issue that bit FINRA. The fix mirrors ``services/finra.py``:
force ``identity`` on every outbound request.

Three HTTP entry points in :class:`EdgarService` each build their own
``headers`` dict, so one ``identity`` line per method. These tests use
``respx`` to capture each outbound request and assert the header survives.
A "header cleanup" PR that drops any of the three entries will fail one of
these tests before the regression can ship.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import respx

from app.services.edgar import EdgarService


@respx.mock
async def test_company_search_sends_accept_encoding_identity() -> None:
    """``_fetch_via_company_search`` builds the first headers block.

    Call it directly so we don't have to mock pagination thresholds or the
    bulk-ZIP fallback that ``fetch_all_broker_dealers`` would chain into.
    """
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        if "headers" not in captured:
            captured["headers"] = dict(request.headers)
        return httpx.Response(200, text="<html><body><table></table></body></html>")

    respx.get(url__startswith="https://www.sec.gov/cgi-bin/browse-edgar").mock(
        side_effect=_capture,
    )

    with patch("app.services.edgar.settings") as mock_settings:
        mock_settings.edgar_target_sic_codes = "6199"
        mock_settings.sec_user_agent = "test-agent contact@example.com"
        mock_settings.sec_request_timeout_seconds = 30
        mock_settings.sec_request_max_retries = 1
        mock_settings.edgar_rate_limit_per_second = 0
        await EdgarService()._fetch_via_company_search(limit=1)

    assert "headers" in captured, "no outbound request was captured"
    assert captured["headers"].get("accept-encoding") == "identity", (
        "_fetch_via_company_search must send Accept-Encoding: identity to bypass "
        "SEC EDGAR Cloudflare's broken compression; got "
        f"{captured['headers'].get('accept-encoding')!r}"
    )


@respx.mock
async def test_fetch_records_for_sec_numbers_sends_accept_encoding_identity() -> None:
    """``fetch_records_for_sec_numbers`` builds the second headers block when
    a requested SEC number is not in the bulk-list cache and falls through to
    the per-firm browse-edgar path.

    Mock ``fetch_all_broker_dealers`` to return an empty bulk list so the
    requested SEC number is "missing" and the per-firm path fires.
    """
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        if "headers" not in captured:
            captured["headers"] = dict(request.headers)
        return httpx.Response(404, text="not found")

    respx.get(url__startswith="https://www.sec.gov/cgi-bin/browse-edgar").mock(
        side_effect=_capture,
    )

    with patch("app.services.edgar.settings") as mock_settings:
        mock_settings.sec_user_agent = "test-agent contact@example.com"
        mock_settings.sec_request_timeout_seconds = 30
        mock_settings.edgar_rate_limit_per_second = 0
        with patch.object(
            EdgarService, "fetch_all_broker_dealers", new=AsyncMock(return_value=[])
        ):
            await EdgarService().fetch_records_for_sec_numbers(["8-12345"])

    assert "headers" in captured, "no outbound request was captured"
    assert captured["headers"].get("accept-encoding") == "identity", (
        "fetch_records_for_sec_numbers per-firm path must send "
        "Accept-Encoding: identity; got "
        f"{captured['headers'].get('accept-encoding')!r}"
    )


@respx.mock
async def test_bulk_submissions_zip_download_sends_accept_encoding_identity(
    tmp_path: Path,
) -> None:
    """The bulk ZIP path is the most error-amplifying — each chunk failed
    decompression generates one log line. Verify identity is set."""
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        if "headers" not in captured:
            captured["headers"] = dict(request.headers)
        return httpx.Response(200, content=b"not-a-real-zip")

    respx.get(url__startswith="https://www.sec.gov").mock(side_effect=_capture)

    zip_target = tmp_path / "submissions.zip"
    with patch("app.services.edgar.settings") as mock_settings:
        mock_settings.sec_user_agent = "test-agent contact@example.com"
        mock_settings.sec_bulk_submissions_zip_path = str(zip_target)
        mock_settings.sec_bulk_submissions_url = (
            "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
        )
        await EdgarService()._ensure_bulk_submissions_zip(force_refresh=True)

    assert "headers" in captured, "no outbound request was captured"
    assert captured["headers"].get("accept-encoding") == "identity", (
        "bulk submissions ZIP download must send Accept-Encoding: identity to bypass "
        "SEC EDGAR Cloudflare's broken compression; got "
        f"{captured['headers'].get('accept-encoding')!r}"
    )
