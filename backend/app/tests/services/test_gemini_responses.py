"""Tests for Gemini client error handling and key-shape validation.

Covers ticket S-4: malformed-key startup validator, transport-error
surfacing, 4xx/5xx retry behavior.

Ref: .claude/focus-fix/diagnosis.md §9 ticket S-4 and the original
incident where a corrupted key produced httpx.LocalProtocolError that
surfaced as an opaque "network error" with no traceback.
"""

from __future__ import annotations

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
