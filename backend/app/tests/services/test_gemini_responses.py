"""Tests for Gemini client error handling and key-shape validation.

Covers ticket S-4: malformed-key startup validator, transport-error
surfacing, 4xx/5xx retry behavior.

Also covers the 2026-04-25 OOM incident remediation: dispatch between
inline base64 and the Files API based on PDF size
(``gemini_files_api_threshold_mb``), with a hard ceiling enforced by
``gemini_inline_pdf_max_size_mb``. See
``reports/incident-extract-focus-ceo-503-2026-04-25.md``.

Ref: .claude/focus-fix/diagnosis.md §9 ticket S-4 and the original
incident where a corrupted key produced httpx.LocalProtocolError that
surfaced as an opaque "network error" with no traceback.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiResponsesClient,
)


_VALID_KEY = "AIzaSy" + "a" * 33  # 39 chars, matches ^AIzaSy[A-Za-z0-9_\-]{33}$


@pytest.fixture
def patch_valid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a syntactically valid key so __init__ passes. The key is not
    used on the wire — respx intercepts all outbound HTTP."""
    monkeypatch.setattr(settings, "gemini_api_key", _VALID_KEY)
    monkeypatch.setattr(settings, "gemini_api_base", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setattr(settings, "gemini_pdf_model", "gemini-2.5-pro")
    monkeypatch.setattr(settings, "gemini_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "gemini_request_max_retries", 3)


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the `await asyncio.sleep(...)` between retries so tests are fast."""
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.gemini_responses.asyncio.sleep", _instant_sleep)


_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"


# ───────────────────────── __init__ validator ─────────────────────────


class TestInitKeyShapeValidator:
    @pytest.mark.parametrize(
        "bad_key",
        [
            # The incident — shape from a Windows `echo -n` misfire
            '-n "' + _VALID_KEY + '"\r\n',
            "-n " + _VALID_KEY,
            # Too short / too long
            "AIzaSyABC",
            "AIzaSy" + "a" * 34,
            # Right length, wrong prefix
            "XYzaSy" + "a" * 33,
            "AIzaSx" + "a" * 33,
            # Contains whitespace
            "AIzaSy " + "a" * 32,
            # Contains control chars
            "AIzaSy" + "a" * 32 + "\r",
            "AIzaSy" + "a" * 32 + "\n",
            # Contains disallowed punctuation
            "AIzaSy" + "a" * 32 + "!",
        ],
    )
    def test_malformed_key_raises(self, bad_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "gemini_api_key", bad_key)
        with pytest.raises(GeminiConfigurationError, match="invalid shape"):
            GeminiResponsesClient()

    def test_empty_key_permits_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty / unset keys are allowed — per-call guard in extract_*
        raises GeminiConfigurationError at first use, which lets local dev
        boot without Gemini configured."""
        monkeypatch.setattr(settings, "gemini_api_key", None)
        # Should not raise.
        GeminiResponsesClient()

        monkeypatch.setattr(settings, "gemini_api_key", "")
        # Should not raise.
        GeminiResponsesClient()

    def test_valid_key_permits_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "gemini_api_key", _VALID_KEY)
        # Should not raise.
        GeminiResponsesClient()


# ─────────────────────── _post_with_retries behavior ───────────────────


class TestPostWithRetriesTransportErrors:
    """Transport-level failures (ConnectError, ReadTimeout, LocalProtocolError)
    land in the `except httpx.HTTPError` branch and produce the 'network
    error' wrapped message — only AFTER all retries are exhausted."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_connect_error_exhausts_retries_then_raises(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        route = respx.post(_GEMINI_URL).mock(side_effect=httpx.ConnectError("refused"))
        client = GeminiResponsesClient()

        with pytest.raises(GeminiExtractionError, match="network error"):
            await client._post_with_retries({"contents": []})

        assert route.call_count == 3  # max_retries=3

    @respx.mock
    @pytest.mark.asyncio
    async def test_read_timeout_surfaces_as_network_error(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        respx.post(_GEMINI_URL).mock(side_effect=httpx.ReadTimeout("slow"))
        client = GeminiResponsesClient()

        with pytest.raises(GeminiExtractionError, match="network error"):
            await client._post_with_retries({"contents": []})

    @respx.mock
    @pytest.mark.asyncio
    async def test_local_protocol_error_surfaces_as_network_error(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        """This is the exact failure mode of the original incident: a
        corrupted API key contained \\r\\n, which httpx rejected at header-
        validation time with LocalProtocolError — a subclass of HTTPError
        but NOT of HTTPStatusError. Regression guard: the ValueError we
        raise now at __init__ catches this before it ever reaches the
        wire, but the runtime path must still handle it the same way."""
        respx.post(_GEMINI_URL).mock(side_effect=httpx.LocalProtocolError("Illegal header value"))
        client = GeminiResponsesClient()

        with pytest.raises(GeminiExtractionError, match="network error"):
            await client._post_with_retries({"contents": []})


class TestPostWithRetriesStatusErrors:
    """HTTP-level failures (4xx/5xx) go through the `except httpx.HTTPStatusError`
    branch. Retryable codes re-loop; non-retryable fail immediately."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_400_fails_immediately_no_retry(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        route = respx.post(_GEMINI_URL).mock(
            return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
        )
        client = GeminiResponsesClient()

        with pytest.raises(GeminiExtractionError, match="status 400"):
            await client._post_with_retries({"contents": []})

        assert route.call_count == 1  # Non-retryable — one attempt only.

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_fails_immediately_no_retry(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        route = respx.post(_GEMINI_URL).mock(
            return_value=httpx.Response(401, json={"error": {"message": "invalid key"}})
        )
        client = GeminiResponsesClient()

        with pytest.raises(GeminiExtractionError, match="status 401"):
            await client._post_with_retries({"contents": []})

        assert route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_429_retries_then_surfaces_status(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        """429 is in the retryable set {408, 409, 429, 500, 502, 503, 504}.
        All three attempts return 429; the third attempt surfaces the
        status-code error."""
        route = respx.post(_GEMINI_URL).mock(
            return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
        )
        client = GeminiResponsesClient()

        with pytest.raises(GeminiExtractionError, match="status 429"):
            await client._post_with_retries({"contents": []})

        assert route.call_count == 3

    @respx.mock
    @pytest.mark.asyncio
    async def test_503_retries_then_succeeds(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        """Transient 5xx followed by a 200 should succeed on the later attempt."""
        route = respx.post(_GEMINI_URL).mock(
            side_effect=[
                httpx.Response(503, json={"error": "try later"}),
                httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}),
            ]
        )
        client = GeminiResponsesClient()

        result = await client._post_with_retries({"contents": []})

        assert result["candidates"][0]["content"]["parts"][0]["text"] == "ok"
        assert route.call_count == 2


# ─────────────────── Files API dispatch (2026-04-25 OOM fix) ───────────────────

# Test scaling rationale: production thresholds are 20 MB / 45 MB. Generating
# 25-50 MB synthetic byte buffers per test would burn ~100 MB of churn and slow
# CI. We patch the thresholds to small values (1 MB / 3 MB) and use proportional
# synthetic PDFs (0.5 MB / 2 MB / 4 MB) — the dispatch logic is identical, the
# only thing the test cares about is which side of each threshold the size lands.

_FILES_UPLOAD_URL_PATTERN = (
    r"^https://generativelanguage\.googleapis\.com/upload/v1beta/files"
)
_FILES_RESOURCE_URL_PATTERN = (
    r"^https://generativelanguage\.googleapis\.com/v1beta/files/"
)

_FOCUS_CEO_RESPONSE = httpx.Response(
    200,
    json={
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                '{"ceo_name": null, "ceo_title": null, '
                                '"ceo_phone": null, "ceo_email": null, '
                                '"net_capital": null, "report_date": null, '
                                '"confidence_score": 0.5, '
                                '"rationale": "synthetic test response"}'
                            )
                        }
                    ]
                }
            }
        ]
    },
)


def _make_synthetic_pdf_b64(size_mb: float) -> str:
    """Return a base64 string whose decoded payload is ~``size_mb`` MB.

    The bytes start with ``%PDF-1.4`` so the synthetic payload is structurally
    plausible at byte zero, even though no PDF parsing happens in these tests.
    """
    size_bytes = int(size_mb * 1024 * 1024)
    if size_bytes < 9:
        size_bytes = 9
    raw = b"%PDF-1.4\n" + (b"x" * (size_bytes - 9))
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture
def patch_dispatch_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink dispatch thresholds so tests can exercise paths with tiny PDFs.

    Threshold = 1 MB → Files API kicks in above 1 MB.
    Max ceiling = 3 MB → reject above 3 MB.
    """
    monkeypatch.setattr(settings, "gemini_files_api_threshold_mb", 1)
    monkeypatch.setattr(settings, "gemini_inline_pdf_max_size_mb", 3)


class TestPdfDispatchInlineVsFilesApi:
    """Routing between inline base64 and Files API based on PDF size."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_inline_path_used_for_small_pdf(
        self,
        patch_valid_key: None,
        patch_dispatch_thresholds: None,
        no_backoff_sleep: None,
    ) -> None:
        """PDF below threshold → existing inline base64 path; Files API never hit."""
        pdf_b64 = _make_synthetic_pdf_b64(0.5)  # below 1 MB threshold

        captured: dict[str, bytes] = {}

        def capture_and_respond(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return _FOCUS_CEO_RESPONSE

        generate_route = respx.post(_GEMINI_URL).mock(side_effect=capture_and_respond)
        upload_route = respx.post(url__regex=_FILES_UPLOAD_URL_PATTERN).mock(
            return_value=httpx.Response(500, json={"error": "should not be called"})
        )

        client = GeminiResponsesClient()
        await client.extract_focus_ceo_data(prompt="t", pdf_bytes_base64=pdf_b64)

        assert generate_route.call_count == 1
        assert upload_route.call_count == 0
        assert b'"inline_data"' in captured["body"]
        assert b'"file_data"' not in captured["body"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_files_api_path_used_for_large_pdf(
        self,
        patch_valid_key: None,
        patch_dispatch_thresholds: None,
        no_backoff_sleep: None,
    ) -> None:
        """PDF above threshold → upload to Files API, reference by file_uri, delete after."""
        pdf_b64 = _make_synthetic_pdf_b64(2.0)  # above 1 MB threshold, below 3 MB ceiling

        upload_response = httpx.Response(
            200,
            json={
                "file": {
                    "name": "files/abc123",
                    "uri": "https://generativelanguage.googleapis.com/v1beta/files/abc123",
                    "state": "ACTIVE",
                    "mimeType": "application/pdf",
                }
            },
        )
        upload_route = respx.post(url__regex=_FILES_UPLOAD_URL_PATTERN).mock(
            return_value=upload_response
        )

        captured: dict[str, bytes] = {}

        def capture_and_respond(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return _FOCUS_CEO_RESPONSE

        generate_route = respx.post(_GEMINI_URL).mock(side_effect=capture_and_respond)
        delete_route = respx.delete(url__regex=_FILES_RESOURCE_URL_PATTERN).mock(
            return_value=httpx.Response(200, json={})
        )

        client = GeminiResponsesClient()
        await client.extract_focus_ceo_data(prompt="t", pdf_bytes_base64=pdf_b64)

        assert upload_route.call_count == 1, "Files API upload was not called"
        assert generate_route.call_count == 1, "downstream generateContent was not called"
        assert delete_route.call_count == 1, "uploaded file was not deleted"

        body = captured["body"]
        assert b'"file_data"' in body, "downstream call should reference file_data"
        assert b'"file_uri"' in body, "downstream call should reference file_uri"
        assert b"files/abc123" in body, "downstream call should reference uploaded file"
        assert b'"inline_data"' not in body, "downstream call must NOT carry inline base64"

    @respx.mock
    @pytest.mark.asyncio
    async def test_oversized_pdf_rejected(
        self,
        patch_valid_key: None,
        patch_dispatch_thresholds: None,
        no_backoff_sleep: None,
    ) -> None:
        """PDF above the hard ceiling → reject before any HTTP call.

        In production the downloader caps before bytes reach this client, but
        the dispatch keeps a defense-in-depth check so a misconfigured caller
        cannot push a 60+ MB JSON payload at Gemini.
        """
        pdf_b64 = _make_synthetic_pdf_b64(4.0)  # above 3 MB ceiling

        upload_route = respx.post(url__regex=_FILES_UPLOAD_URL_PATTERN).mock(
            return_value=httpx.Response(500)
        )
        generate_route = respx.post(_GEMINI_URL).mock(
            return_value=httpx.Response(500)
        )

        client = GeminiResponsesClient()
        with pytest.raises(GeminiExtractionError, match="exceeds"):
            await client.extract_focus_ceo_data(prompt="t", pdf_bytes_base64=pdf_b64)

        assert upload_route.call_count == 0
        assert generate_route.call_count == 0

    def test_api_key_shape_guard_intact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: the Files API change must not loosen the key-shape
        check. A trailing CRLF (the original 2026-04 incident shape) must
        still raise ``GeminiConfigurationError`` at __init__ time.
        """
        bad_key = _VALID_KEY + "\r\n"
        monkeypatch.setattr(settings, "gemini_api_key", bad_key)
        with pytest.raises(GeminiConfigurationError, match="invalid shape"):
            GeminiResponsesClient()
