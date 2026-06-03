"""SEC ``429 Too Many Requests`` resilience for the focus-report path.

Regression coverage for the client-reported failure where clicking a firm's
FOCUS-report link returned a raw ``502`` carrying
``Client error '429 Too Many Requests' for url
'https://data.sec.gov/submissions/CIK...json'``.

Root cause: SEC EDGAR throttles the shared Cloud Run egress IP (~10 req/s,
project-wide) and ``pdf_downloader``'s SEC clients — unlike ``services/edgar.py``
— neither honored the ``Retry-After`` header nor cached the per-firm
submissions doc, so a momentary throttle (concurrent extraction jobs or
rapid clicks) blew up into a user-facing error.

These tests pin the fix:
  * ``_sec_retry_after_seconds`` — header parsing + capped fallback.
  * the JSON + streaming retry loops ride out a transient 429 and only
    surface the error once retries are exhausted.
  * the submissions cache absorbs a repeat fetch within its TTL.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import settings
from app.services import pdf_downloader as pdf_downloader_module
from app.services.pdf_downloader import PdfDownloaderService, _sec_retry_after_seconds

_SEC_JSON_URL = "https://data.sec.gov/submissions/CIK0001234567.json"
_SEC_PDF_URL = (
    "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/primary.pdf"
)


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every retry sleep instant so the (honored) Retry-After wait
    does not slow the suite down."""

    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.pdf_downloader.asyncio.sleep", _instant_sleep)


@pytest.fixture
def patch_sec_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sec_request_max_retries", 3)
    monkeypatch.setattr(settings, "sec_request_timeout_seconds", 5.0)


@pytest.fixture
def clear_submissions_cache() -> None:
    """The submissions cache is module-global; isolate each test from
    cross-test pollution by clearing it before and after."""
    pdf_downloader_module._SUBMISSIONS_CACHE.clear()
    yield
    pdf_downloader_module._SUBMISSIONS_CACHE.clear()


class TestSecRetryAfterSeconds:
    """The helper turns a 429 response into a backoff duration."""

    def test_honors_integer_retry_after(self) -> None:
        resp = httpx.Response(429, headers={"retry-after": "5"})
        assert _sec_retry_after_seconds(resp, attempt=1) == 5.0

    def test_caps_retry_after_at_60(self) -> None:
        """A hostile / huge Retry-After cannot wedge a request-path call."""
        resp = httpx.Response(429, headers={"retry-after": "9999"})
        assert _sec_retry_after_seconds(resp, attempt=1) == 60.0

    def test_malformed_retry_after_falls_back_to_backoff(self) -> None:
        """An HTTP-date or junk Retry-After falls back to exp backoff
        rather than raising. attempt=2 -> min(2**2, 30) == 4."""
        resp = httpx.Response(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
        assert _sec_retry_after_seconds(resp, attempt=2) == 4.0

    def test_missing_retry_after_uses_capped_exponential_backoff(self) -> None:
        resp = httpx.Response(429)
        assert _sec_retry_after_seconds(resp, attempt=3) == 8.0
        assert _sec_retry_after_seconds(resp, attempt=10) == 30.0  # capped


class TestGetJsonRetryOn429:
    """``_get_json_with_retries`` — the call that 429'd in the client report."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_429_then_200_rides_out_throttle(
        self, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        """A transient 429 followed by a 200 must succeed — not 502 — so the
        focus-report endpoint rides out a momentary SEC throttle."""
        responses = iter(
            [
                httpx.Response(429, headers={"retry-after": "1"}),
                httpx.Response(200, json={"filings": {"recent": {}}}),
            ]
        )
        route = respx.get(_SEC_JSON_URL).mock(side_effect=lambda _req: next(responses))

        downloader = PdfDownloaderService()
        payload = await downloader._get_json_with_retries(_SEC_JSON_URL)

        assert payload == {"filings": {"recent": {}}}
        assert route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_persistent_429_exhausts_then_raises_429(
        self, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        """A sustained 429 still surfaces as an HTTPStatusError carrying the
        429 status (so the endpoint can map it to a clean 503) after all
        retries are exhausted."""
        route = respx.get(_SEC_JSON_URL).mock(
            return_value=httpx.Response(429, headers={"retry-after": "1"})
        )

        downloader = PdfDownloaderService()
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await downloader._get_json_with_retries(_SEC_JSON_URL)

        assert excinfo.value.response.status_code == 429
        assert route.call_count == 3


class TestStreamRetryOn429:
    """The streaming PDF download paths gained the same 429 handling."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_stream_to_path_429_then_200(
        self, tmp_path: Path, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        body = b"%PDF-1.7 streamed-after-throttle"
        responses = iter(
            [
                httpx.Response(429, headers={"retry-after": "1"}),
                httpx.Response(200, content=body, headers={"content-type": "application/pdf"}),
            ]
        )
        route = respx.get(_SEC_PDF_URL).mock(side_effect=lambda _req: next(responses))

        downloader = PdfDownloaderService()
        target = tmp_path / "streamed.pdf"
        byte_size = await downloader._stream_to_path(
            _SEC_PDF_URL, target, max_size_bytes=10 * 1024 * 1024
        )

        assert byte_size == len(body)
        assert target.read_bytes() == body
        assert route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_download_bytes_429_then_200(
        self, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        body = b"%PDF-1.7 legacy-path-after-throttle"
        responses = iter(
            [
                httpx.Response(429, headers={"retry-after": "1"}),
                httpx.Response(200, content=body, headers={"content-type": "application/pdf"}),
            ]
        )
        route = respx.get(_SEC_PDF_URL).mock(side_effect=lambda _req: next(responses))

        downloader = PdfDownloaderService()
        result = await downloader._download_bytes_with_retries(_SEC_PDF_URL)

        assert result == body
        assert route.call_count == 2


class TestSubmissionsCache:
    """``_get_submissions_json_cached`` absorbs repeat fetches of the same
    firm's submissions doc — the property that stops rage-clicks and
    concurrent extraction from each re-hitting SEC."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_second_call_within_ttl_is_served_from_cache(
        self,
        patch_sec_retries: None,
        no_backoff_sleep: None,
        clear_submissions_cache: None,
    ) -> None:
        route = respx.get(_SEC_JSON_URL).mock(
            return_value=httpx.Response(200, json={"filings": {"recent": {}}})
        )

        downloader = PdfDownloaderService()
        first = await downloader._get_submissions_json_cached(_SEC_JSON_URL)
        second = await downloader._get_submissions_json_cached(_SEC_JSON_URL)

        assert first == {"filings": {"recent": {}}}
        assert second is first  # exact cached object returned
        assert route.call_count == 1  # only one SEC fetch happened

    @respx.mock
    @pytest.mark.asyncio
    async def test_expired_entry_is_refetched(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_sec_retries: None,
        no_backoff_sleep: None,
        clear_submissions_cache: None,
    ) -> None:
        """With a zero TTL every lookup is treated as stale, so the second
        call re-hits SEC. Pins that the cache honors its TTL rather than
        caching forever."""
        monkeypatch.setattr(
            pdf_downloader_module, "_SUBMISSIONS_CACHE_TTL_SECONDS", 0
        )
        route = respx.get(_SEC_JSON_URL).mock(
            return_value=httpx.Response(200, json={"filings": {"recent": {}}})
        )

        downloader = PdfDownloaderService()
        await downloader._get_submissions_json_cached(_SEC_JSON_URL)
        await downloader._get_submissions_json_cached(_SEC_JSON_URL)

        assert route.call_count == 2
