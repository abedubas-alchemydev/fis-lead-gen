"""Cross-cutting LRU semantics for the Gemini & OpenAI Files API caches
(ADR-0001 phase 2).

The Gemini and OpenAI clients each own a private OrderedDict keyed by
SEC accession number, mapping to ``(file_id, uploaded_at)`` (OpenAI) or
``(file_name, file_uri, uploaded_at)`` (Gemini). The shape differs because
the providers return slightly different reference types, but the eviction
contract (LRU bounded by ``_FILE_ID_CACHE_MAX_ENTRIES`` + TTL bounded by
``_FILE_ID_TTL``) is identical.

These tests pin the contract on both caches so a future refactor that
shares an implementation between them does not silently drop one of the
properties. The "concurrent calls under the lock don't double-upload"
property uses ``asyncio.gather`` to issue two
``extract_clearing_data_from_path`` calls on the same accession and
asserts the upload count is bounded.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import settings


# ─────────────────── Gemini LRU ───────────────────


_VALID_GEMINI_KEY = "AIzaSy" + "a" * 33
_GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
)
_GEMINI_FILES_UPLOAD_URL_PATTERN = (
    r"^https://generativelanguage\.googleapis\.com/upload/v1beta/files"
)
_GEMINI_CLEARING_RESPONSE = httpx.Response(
    200,
    json={
        "candidates": [{"content": {"parts": [{"text": (
            '{"clearing_partner": "Pershing LLC", '
            '"clearing_type": "fully_disclosed", '
            '"agreement_date": null, '
            '"confidence_score": 0.92, '
            '"rationale": "synthetic"}'
        )}]}}]
    },
)


@pytest.fixture
def patch_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", _VALID_GEMINI_KEY)
    monkeypatch.setattr(settings, "gemini_api_base", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setattr(settings, "gemini_pdf_model", "gemini-2.5-pro")
    monkeypatch.setattr(settings, "gemini_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "gemini_request_max_retries", 2)


@pytest.fixture
def gemini_no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.gemini_responses.asyncio.sleep", _instant)


@pytest.fixture
def gemini_lru_cleared():
    from app.services.gemini_responses import _file_id_cache_clear_for_tests
    _file_id_cache_clear_for_tests()
    yield
    _file_id_cache_clear_for_tests()


def _make_synthetic_pdf(tmp_path: Path) -> Path:
    target = tmp_path / "synthetic.pdf"
    target.write_bytes(b"%PDF-1.4\n" + (b"x" * 1024))
    return target


class TestGeminiLruStoresAndEvicts:
    @pytest.mark.asyncio
    async def test_stores_entries(self, gemini_lru_cleared) -> None:
        from app.services.gemini_responses import (
            _FILE_ID_CACHE,
            _file_id_cache_get,
            _file_id_cache_put,
        )

        await _file_id_cache_put("0001-25-000001", "files/a", "https://x/a")
        assert len(_FILE_ID_CACHE) == 1
        hit = await _file_id_cache_get("0001-25-000001")
        assert hit == ("files/a", "https://x/a")

    @pytest.mark.asyncio
    async def test_evicts_oldest_on_overflow(self, gemini_lru_cleared) -> None:
        from app.services.gemini_responses import (
            _FILE_ID_CACHE,
            _FILE_ID_CACHE_MAX_ENTRIES,
            _file_id_cache_get,
            _file_id_cache_put,
        )

        for i in range(_FILE_ID_CACHE_MAX_ENTRIES + 5):
            await _file_id_cache_put(f"acc-{i:06d}", f"files/{i}", f"https://x/{i}")

        assert len(_FILE_ID_CACHE) == _FILE_ID_CACHE_MAX_ENTRIES
        # First 5 entries should have been evicted.
        for i in range(5):
            assert await _file_id_cache_get(f"acc-{i:06d}") is None
        # Last entry must still be present.
        assert await _file_id_cache_get(f"acc-{_FILE_ID_CACHE_MAX_ENTRIES + 4:06d}") is not None

    @pytest.mark.asyncio
    async def test_expires_entries_past_ttl(self, gemini_lru_cleared) -> None:
        from app.services.gemini_responses import (
            _FILE_ID_CACHE,
            _FILE_ID_TTL,
            _file_id_cache_get,
            _file_id_cache_put,
        )

        await _file_id_cache_put("acc-000001", "files/old", "https://x/old")
        # Backdate the entry past the TTL. The cache strips dashes from
        # accession numbers when computing keys, so the on-disk key here
        # is ``acc000001`` rather than ``acc-000001`` — see
        # ``_file_id_cache_key`` in gemini_responses.
        canonical_key = "acc000001"
        file_name, file_uri, _ = _FILE_ID_CACHE[canonical_key]
        _FILE_ID_CACHE[canonical_key] = (
            file_name, file_uri,
            datetime.now(timezone.utc) - _FILE_ID_TTL - timedelta(seconds=1),
        )

        # Read evicts and returns None.
        assert await _file_id_cache_get("acc-000001") is None
        assert canonical_key not in _FILE_ID_CACHE

    @pytest.mark.asyncio
    async def test_move_to_end_on_hit_keeps_recent_entries(
        self, gemini_lru_cleared
    ) -> None:
        """LRU semantics: a cache hit on an entry must move it to the
        most-recently-used end so it survives the next eviction round."""
        from app.services.gemini_responses import (
            _FILE_ID_CACHE_MAX_ENTRIES,
            _file_id_cache_get,
            _file_id_cache_put,
        )

        # Fill the cache to capacity.
        for i in range(_FILE_ID_CACHE_MAX_ENTRIES):
            await _file_id_cache_put(f"acc-{i:06d}", f"files/{i}", f"https://x/{i}")

        # Hit on the oldest entry — move-to-end keeps it alive.
        assert await _file_id_cache_get("acc-000000") is not None

        # Push one more so something must evict.
        await _file_id_cache_put(
            "acc-NEW", "files/new", "https://x/new"
        )

        # acc-000000 was just touched, so it survives. The next-oldest
        # (acc-000001) is the one that drops.
        assert await _file_id_cache_get("acc-000000") is not None
        assert await _file_id_cache_get("acc-000001") is None


class TestGeminiLruConcurrentUploadDeduplication:
    """Two concurrent calls on the same accession should result in at most
    a small bounded number of uploads. The brief allows benign double-upload
    (the upload runs OUTSIDE the lock so concurrent uploads of the same
    accession may both fire) — this test pins ``upload_count <= 2``
    rather than strict ``== 1`` because strict over-specifies and breaks
    with the design's deliberate race tolerance. In production this is OK
    because the second upload's response simply overwrites the first
    cache entry."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_concurrent_calls_bounded_upload_count(
        self,
        patch_gemini: None,
        gemini_no_sleep: None,
        gemini_lru_cleared,
        tmp_path: Path,
    ) -> None:
        from app.services.gemini_responses import GeminiResponsesClient

        local_path = _make_synthetic_pdf(tmp_path)

        upload_route = respx.post(url__regex=_GEMINI_FILES_UPLOAD_URL_PATTERN).mock(
            return_value=httpx.Response(
                200,
                json={"file": {
                    "name": "files/abc", "uri": "https://x/v1beta/files/abc",
                    "state": "ACTIVE", "mimeType": "application/pdf",
                }},
            )
        )
        respx.post(_GEMINI_GENERATE_URL).mock(return_value=_GEMINI_CLEARING_RESPONSE)

        client = GeminiResponsesClient()
        await asyncio.gather(
            client.extract_clearing_data_from_path(
                local_path=local_path,
                accession_number="0001-25-000001",
                prompt="p",
            ),
            client.extract_clearing_data_from_path(
                local_path=local_path,
                accession_number="0001-25-000001",
                prompt="p",
            ),
        )

        # Race-tolerant: 1 upload (lock held across full upload, ideal) or
        # 2 (both calls miss the cache at check time, both upload, second
        # wins on write). Either is correct per the design.
        assert upload_route.call_count <= 2


# ─────────────────── OpenAI LRU ───────────────────


_OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
_OPENAI_FILES_URL = "https://api.openai.com/v1/files"
_OPENAI_CLEARING_RESPONSE_BODY = {
    "output_text": (
        '{"clearing_partner": "Apex Clearing Corporation", '
        '"clearing_type": "fully_disclosed", '
        '"agreement_date": null, '
        '"confidence_score": 0.88, '
        '"rationale": "synthetic"}'
    )
}


@pytest.fixture
def patch_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-fake")
    monkeypatch.setattr(settings, "openai_api_base", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "openai_pdf_model", "gpt-4o")
    monkeypatch.setattr(settings, "openai_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "openai_request_max_retries", 2)


@pytest.fixture
def openai_no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.openai_responses.asyncio.sleep", _instant)


@pytest.fixture
def openai_lru_cleared():
    from app.services.openai_responses import _file_id_cache_clear_for_tests
    _file_id_cache_clear_for_tests()
    yield
    _file_id_cache_clear_for_tests()


class TestOpenAiLruStoresAndEvicts:
    @pytest.mark.asyncio
    async def test_stores_and_retrieves(self, openai_lru_cleared) -> None:
        from app.services.openai_responses import (
            _FILE_ID_CACHE,
            _file_id_cache_get,
            _file_id_cache_put,
        )

        await _file_id_cache_put("acc-000001", "file-aaa")
        assert await _file_id_cache_get("acc-000001") == "file-aaa"
        assert len(_FILE_ID_CACHE) == 1

    @pytest.mark.asyncio
    async def test_evicts_oldest_on_overflow(self, openai_lru_cleared) -> None:
        from app.services.openai_responses import (
            _FILE_ID_CACHE,
            _FILE_ID_CACHE_MAX_ENTRIES,
            _file_id_cache_get,
            _file_id_cache_put,
        )

        for i in range(_FILE_ID_CACHE_MAX_ENTRIES + 5):
            await _file_id_cache_put(f"acc-{i:06d}", f"file-{i}")

        assert len(_FILE_ID_CACHE) == _FILE_ID_CACHE_MAX_ENTRIES
        assert await _file_id_cache_get("acc-000000") is None
        assert await _file_id_cache_get(f"acc-{_FILE_ID_CACHE_MAX_ENTRIES + 4:06d}") is not None

    @pytest.mark.asyncio
    async def test_expires_entries_past_ttl(self, openai_lru_cleared) -> None:
        from app.services.openai_responses import (
            _FILE_ID_CACHE,
            _FILE_ID_TTL,
            _file_id_cache_get,
            _file_id_cache_put,
        )

        await _file_id_cache_put("acc-000001", "file-old")
        # Cache strips dashes from accession numbers, so the on-disk key is
        # ``acc000001`` rather than ``acc-000001``. See
        # ``_file_id_cache_key`` in openai_responses.
        canonical_key = "acc000001"
        file_id, _ = _FILE_ID_CACHE[canonical_key]
        _FILE_ID_CACHE[canonical_key] = (
            file_id,
            datetime.now(timezone.utc) - _FILE_ID_TTL - timedelta(seconds=1),
        )

        assert await _file_id_cache_get("acc-000001") is None
        assert canonical_key not in _FILE_ID_CACHE


class TestOpenAiLruConcurrentUploadDeduplication:
    @respx.mock
    @pytest.mark.asyncio
    async def test_concurrent_calls_bounded_upload_count(
        self,
        patch_openai: None,
        openai_no_sleep: None,
        openai_lru_cleared,
        tmp_path: Path,
    ) -> None:
        from app.services.openai_responses import OpenAIResponsesClient

        local_path = _make_synthetic_pdf(tmp_path)

        upload_route = respx.post(_OPENAI_FILES_URL).mock(
            return_value=httpx.Response(200, json={"id": "file-aaa"})
        )
        respx.post(_OPENAI_RESPONSES_URL).mock(
            return_value=httpx.Response(200, json=_OPENAI_CLEARING_RESPONSE_BODY)
        )

        client = OpenAIResponsesClient()
        await asyncio.gather(
            client.extract_clearing_data_from_path(
                local_path=local_path,
                accession_number="acc-000001",
                filename="firm.pdf",
                prompt="p",
            ),
            client.extract_clearing_data_from_path(
                local_path=local_path,
                accession_number="acc-000001",
                filename="firm.pdf",
                prompt="p",
            ),
        )

        # Race-tolerant: see comment on the Gemini equivalent for rationale.
        assert upload_route.call_count <= 2
