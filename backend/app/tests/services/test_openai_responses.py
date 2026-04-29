"""Tests for OpenAI Responses client — Files API parity (ADR-0001 phase 2).

Mocking strategy mirrors test_gemini_responses.py: respx intercepts every
outbound HTTP call so we never hit the real OpenAI API. The tests cover
the new ``extract_clearing_data_from_path`` entry point that uploads via
``POST /v1/files`` (purpose=user_data) and references the returned
``file_id`` from ``responses.create`` instead of inlining base64.

The existing inline-base64 path (``extract_clearing_data``) is also covered
here so we have a regression guard on the default-off behavior.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.openai_responses import (
    OpenAIExtractionError,
    OpenAIResponsesClient,
    _FILE_ID_CACHE,
    _FILE_ID_CACHE_MAX_ENTRIES,
    _file_id_cache_clear_for_tests,
    _file_id_cache_get,
    _file_id_cache_put,
)


_OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
_OPENAI_FILES_URL = "https://api.openai.com/v1/files"


@pytest.fixture
def patch_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-fake-test-key")
    monkeypatch.setattr(settings, "openai_api_base", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "openai_pdf_model", "gpt-4o")
    monkeypatch.setattr(settings, "openai_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "openai_request_max_retries", 2)


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.openai_responses.asyncio.sleep", _instant_sleep)


@pytest.fixture
def clear_files_api_lru():
    _file_id_cache_clear_for_tests()
    yield
    _file_id_cache_clear_for_tests()


def _make_synthetic_pdf_on_disk(tmp_path, size_kb: int = 8) -> Path:
    target = tmp_path / "synthetic.pdf"
    target.write_bytes(b"%PDF-1.4\n" + (b"x" * (size_kb * 1024 - 9)))
    return Path(target)


_CLEARING_RESPONSE_BODY = {
    "output_text": (
        '{"clearing_partner": "Apex Clearing Corporation", '
        '"clearing_type": "fully_disclosed", '
        '"agreement_date": null, '
        '"confidence_score": 0.88, '
        '"rationale": "synthetic test response"}'
    )
}


# ─────────────────── Default-off (legacy) path regression guard ───────────────────


class TestExtractClearingDataInlineBase64:
    """Default-off path: ``extract_clearing_data`` keeps the inline-base64
    contract identical to today. Locks in the regression invariant required
    by Hard Rule 5 of the cc-cli-01 brief."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_inline_path_unchanged(
        self, patch_openai: None, no_backoff_sleep: None
    ) -> None:
        captured: dict[str, bytes] = {}

        def capture_and_respond(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return httpx.Response(200, json=_CLEARING_RESPONSE_BODY)

        respx.post(_OPENAI_RESPONSES_URL).mock(side_effect=capture_and_respond)

        client = OpenAIResponsesClient()
        result = await client.extract_clearing_data(
            pdf_bytes_base64="JVBERi0xLjQKZmFrZQ==",
            filename="firm.pdf",
            prompt="extract",
        )

        assert result.clearing_partner == "Apex Clearing Corporation"
        body = captured["body"]
        # The inline path stuffs the base64 PDF into a data URL.
        assert b"data:application/pdf;base64," in body
        # And does NOT reference file_id (that's the Files API path).
        assert b'"file_id"' not in body


# ─────────────────── Files API path (flag-on) ───────────────────


class TestExtractClearingDataFromPath:
    """``extract_clearing_data_from_path`` is the new flag-on entry point."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_uploads_via_files_api_and_references_file_id(
        self,
        patch_openai: None,
        no_backoff_sleep: None,
        clear_files_api_lru,
        tmp_path: Path,
    ) -> None:
        """First call uploads the PDF to ``/v1/files`` with
        ``purpose=user_data`` and references the returned ``file_id`` in the
        downstream ``/v1/responses`` call. Inline base64 must NOT appear on
        the wire."""
        local_path = _make_synthetic_pdf_on_disk(tmp_path)

        upload_route = respx.post(_OPENAI_FILES_URL).mock(
            return_value=httpx.Response(200, json={
                "id": "file-abc123", "object": "file", "purpose": "user_data",
            })
        )

        captured: dict[str, bytes] = {}

        def capture_and_respond(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return httpx.Response(200, json=_CLEARING_RESPONSE_BODY)

        responses_route = respx.post(_OPENAI_RESPONSES_URL).mock(
            side_effect=capture_and_respond
        )

        client = OpenAIResponsesClient()
        result = await client.extract_clearing_data_from_path(
            local_path=local_path,
            accession_number="0001234567-25-000001",
            filename="firm.pdf",
            prompt="extract",
        )

        assert result.clearing_partner == "Apex Clearing Corporation"
        assert upload_route.call_count == 1
        assert responses_route.call_count == 1
        body = captured["body"]
        assert b'"file_id"' in body
        assert b"file-abc123" in body
        assert b"data:application/pdf;base64," not in body

    @respx.mock
    @pytest.mark.asyncio
    async def test_lru_reuses_file_id_within_ttl(
        self,
        patch_openai: None,
        no_backoff_sleep: None,
        clear_files_api_lru,
        tmp_path: Path,
    ) -> None:
        """Second call on the same accession reuses the cached file_id —
        one upload, two responses calls."""
        local_path = _make_synthetic_pdf_on_disk(tmp_path)

        upload_route = respx.post(_OPENAI_FILES_URL).mock(
            return_value=httpx.Response(200, json={"id": "file-abc123"})
        )
        responses_route = respx.post(_OPENAI_RESPONSES_URL).mock(
            return_value=httpx.Response(200, json=_CLEARING_RESPONSE_BODY)
        )

        client = OpenAIResponsesClient()
        await client.extract_clearing_data_from_path(
            local_path=local_path, accession_number="0001234567-25-000001",
            filename="firm.pdf", prompt="extract",
        )
        await client.extract_clearing_data_from_path(
            local_path=local_path, accession_number="0001234567-25-000001",
            filename="firm.pdf", prompt="extract",
        )

        assert upload_route.call_count == 1
        assert responses_route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_expired_file_id_evicts_and_retries_once(
        self,
        patch_openai: None,
        no_backoff_sleep: None,
        clear_files_api_lru,
        tmp_path: Path,
    ) -> None:
        """Stale file_id surfaces as 404 ``file_not_found``; the client
        evicts the cache entry, re-uploads, retries the responses call once."""
        local_path = _make_synthetic_pdf_on_disk(tmp_path)

        # Pre-seed the cache so the first request hits a stale file_id.
        await _file_id_cache_put("0001234567-25-000001", "file-stale")

        upload_route = respx.post(_OPENAI_FILES_URL).mock(
            return_value=httpx.Response(200, json={"id": "file-fresh"})
        )

        responses_iter = iter([
            httpx.Response(404, json={"error": {
                "code": "file_not_found",
                "message": "No such File: file-stale",
            }}),
            httpx.Response(200, json=_CLEARING_RESPONSE_BODY),
        ])
        responses_route = respx.post(_OPENAI_RESPONSES_URL).mock(
            side_effect=lambda request: next(responses_iter)
        )

        client = OpenAIResponsesClient()
        result = await client.extract_clearing_data_from_path(
            local_path=local_path,
            accession_number="0001234567-25-000001",
            filename="firm.pdf",
            prompt="extract",
        )

        assert result.clearing_partner == "Apex Clearing Corporation"
        assert upload_route.call_count == 1, "single re-upload after eviction"
        assert responses_route.call_count == 2, "stale call + retry call"

    @respx.mock
    @pytest.mark.asyncio
    async def test_files_api_upload_5xx_surfaces_extraction_error(
        self,
        patch_openai: None,
        no_backoff_sleep: None,
        clear_files_api_lru,
        tmp_path: Path,
    ) -> None:
        """A 5xx from the upload endpoint surfaces as
        ``OpenAIExtractionError`` — caller treats it the same as any other
        provider error (review-queue semantics)."""
        local_path = _make_synthetic_pdf_on_disk(tmp_path)

        respx.post(_OPENAI_FILES_URL).mock(return_value=httpx.Response(503))

        client = OpenAIResponsesClient()
        with pytest.raises(OpenAIExtractionError, match="status 503"):
            await client.extract_clearing_data_from_path(
                local_path=local_path,
                accession_number="0001234567-25-000001",
                filename="firm.pdf",
                prompt="extract",
            )


# ─────────────────── LRU semantics ───────────────────


class TestOpenAIFilesApiLru:
    """OpenAI's LRU mirrors the Gemini one — cap + TTL eviction."""

    @pytest.mark.asyncio
    async def test_lru_caps_at_max_entries_evicts_oldest(
        self, clear_files_api_lru
    ) -> None:
        for i in range(_FILE_ID_CACHE_MAX_ENTRIES + 1):
            await _file_id_cache_put(f"acc-{i:06d}", f"file-{i}")

        assert len(_FILE_ID_CACHE) == _FILE_ID_CACHE_MAX_ENTRIES
        assert await _file_id_cache_get("acc-000000") is None
        assert await _file_id_cache_get(f"acc-{_FILE_ID_CACHE_MAX_ENTRIES:06d}") is not None
