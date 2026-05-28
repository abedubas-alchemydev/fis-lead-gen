"""Tests for the centralized retry helper ``_request_with_retries`` and
its reuse at the Files API upload + status-poll call sites.

Pre-2026-05-28, only the generate-content path retried on transient
errors; the Files API upload was single-attempt. A single TLS blip
during a multi-MB Form ADV upload aborted the entire owners pipeline,
which then stamped the 30-day cooldown and locked the advisor out of
re-attempt. The retry helper extraction lifts retry semantics into a
single place and applies them to every Gemini HTTP call. See
``plans/be-gemini-files-api-retry-2026-05-28.md`` for context (Barings
and AQR failures during the 2026-05-28 gap-fill smoke).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.gemini_responses import (
    GeminiExtractionError,
    GeminiResponsesClient,
)


_VALID_KEY = "AIzaSy" + "a" * 33
_FILES_UPLOAD_URL = (
    "https://generativelanguage.googleapis.com/upload/v1beta/files?uploadType=multipart"
)


@pytest.fixture
def patch_valid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", _VALID_KEY)
    monkeypatch.setattr(
        settings,
        "gemini_api_base",
        "https://generativelanguage.googleapis.com/v1beta",
    )
    monkeypatch.setattr(settings, "gemini_pdf_model", "gemini-2.5-pro")
    monkeypatch.setattr(settings, "gemini_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "gemini_request_max_retries", 3)


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.gemini_responses.asyncio.sleep", _instant_sleep)


class TestFilesApiUploadRetry:
    """The 2026-05-28 production failure mode: a transient network error
    during the multipart upload aborted the owners pipeline. Post-fix,
    the helper retries up to ``max_retries`` before raising the original
    operator-facing message."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_upload_succeeds_on_retry_after_connect_error(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        """ConnectError on attempt 1 → success on attempt 2 → caller gets
        the normal (name, uri) tuple back, no exception. Locks in the fix
        for the Barings/AQR failure mode."""
        route = respx.post(_FILES_UPLOAD_URL).mock(
            side_effect=[
                httpx.ConnectError("tls handshake aborted"),
                httpx.Response(
                    200,
                    json={
                        "file": {
                            "name": "files/abc123",
                            "uri": "https://generativelanguage.googleapis.com/v1beta/files/abc123",
                            "state": "ACTIVE",
                        }
                    },
                ),
            ]
        )
        client = GeminiResponsesClient()

        name, uri = await client._upload_pdf_to_files_api(b"%PDF-1.4\nfake")

        assert name == "files/abc123"
        assert uri.endswith("/files/abc123")
        assert route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_upload_exhausts_retries_then_raises_network_message(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        """Sustained network errors → raises with the existing operator-
        facing message after ``max_retries`` attempts."""
        route = respx.post(_FILES_UPLOAD_URL).mock(
            side_effect=httpx.ConnectError("tls handshake aborted")
        )
        client = GeminiResponsesClient()

        with pytest.raises(
            GeminiExtractionError,
            match="Files API upload failed due to a network error.",
        ):
            await client._upload_pdf_to_files_api(b"%PDF-1.4\nfake")

        assert route.call_count == 3  # max_retries=3

    @respx.mock
    @pytest.mark.asyncio
    async def test_upload_4xx_non_retryable_fails_immediately(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        """A 4xx other than 408/409/429 is not in the retryable set;
        bubbles through the helper and surfaces with the status code +
        body in the message. One attempt only."""
        route = respx.post(_FILES_UPLOAD_URL).mock(
            return_value=httpx.Response(
                400, json={"error": {"message": "invalid file"}}
            )
        )
        client = GeminiResponsesClient()

        with pytest.raises(
            GeminiExtractionError, match="Files API upload failed with status 400"
        ):
            await client._upload_pdf_to_files_api(b"%PDF-1.4\nfake")

        assert route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_upload_503_retries_then_succeeds(
        self, patch_valid_key: None, no_backoff_sleep: None
    ) -> None:
        """503 is in the retryable set — a transient unavailable followed
        by a 200 should succeed without raising."""
        route = respx.post(_FILES_UPLOAD_URL).mock(
            side_effect=[
                httpx.Response(503, json={"error": "try later"}),
                httpx.Response(
                    200,
                    json={
                        "file": {
                            "name": "files/xyz789",
                            "uri": "https://generativelanguage.googleapis.com/v1beta/files/xyz789",
                            "state": "ACTIVE",
                        }
                    },
                ),
            ]
        )
        client = GeminiResponsesClient()

        name, _ = await client._upload_pdf_to_files_api(b"%PDF-1.4\nfake")

        assert name == "files/xyz789"
        assert route.call_count == 2
