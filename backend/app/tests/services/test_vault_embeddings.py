"""Unit tests for the Gemini gemini-embedding-001 client.

Mirrors the pattern in ``test_outreach.py``: respx intercepts the
outbound HTTP, monkeypatch installs a syntactically-valid Gemini key
+ silences the retry-backoff sleep so failure-mode tests are fast.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.vault_embeddings import (
    VaultEmbeddingConfigurationError,
    VaultEmbeddingError,
    embed_chunks,
    embed_query,
)


_VALID_KEY = "AIzaSy" + "a" * 33  # 39 chars, matches ^AIzaSy[A-Za-z0-9_\-]{33}$
_BASE = "https://generativelanguage.googleapis.com/v1beta"
_SINGLE_URL = f"{_BASE}/models/gemini-embedding-001:embedContent"
_BATCH_URL = f"{_BASE}/models/gemini-embedding-001:batchEmbedContents"


def _vec(value: float) -> list[float]:
    """Build a 768-float vector — content irrelevant for these tests."""
    return [value] * 768


@pytest.fixture
def patch_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", _VALID_KEY)
    monkeypatch.setattr(settings, "gemini_api_base", _BASE)


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.vault_embeddings.asyncio.sleep", _instant_sleep)


# ── Configuration errors ────────────────────────────────────────────────────


async def test_missing_api_key_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", None)
    with pytest.raises(VaultEmbeddingConfigurationError):
        await embed_query("anything")


async def test_malformed_api_key_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "bad-key")
    with pytest.raises(VaultEmbeddingConfigurationError):
        await embed_query("anything")


# ── Happy paths ─────────────────────────────────────────────────────────────


@respx.mock
async def test_embed_query_returns_768_floats(patch_gemini) -> None:
    respx.post(_SINGLE_URL).mock(
        return_value=httpx.Response(200, json={"embedding": {"values": _vec(0.5)}})
    )

    out = await embed_query("hello")

    assert len(out) == 768
    assert all(v == 0.5 for v in out)


@respx.mock
async def test_embed_chunks_batches_under_100(patch_gemini) -> None:
    """120 inputs should fan out into 2 batch requests."""
    route = respx.post(_BATCH_URL)

    def _resp_for_request(request: httpx.Request) -> httpx.Response:
        body = request.read().decode("utf-8")
        # Count "text" entries to mirror the request size.
        n = body.count('"text"')
        return httpx.Response(
            200, json={"embeddings": [{"values": _vec(float(n))} for _ in range(n)]}
        )

    route.side_effect = _resp_for_request

    inputs = [f"chunk-{i}" for i in range(120)]
    out = await embed_chunks(inputs)

    assert len(out) == 120
    assert route.call_count == 2  # 100 + 20


@respx.mock
async def test_embed_chunks_empty_list_short_circuits(patch_gemini) -> None:
    """Empty input must NOT make a network call."""
    route = respx.post(_BATCH_URL).mock(return_value=httpx.Response(500))
    out = await embed_chunks([])
    assert out == []
    assert route.call_count == 0


# ── Provider-level errors ───────────────────────────────────────────────────


@respx.mock
async def test_500_after_retries_raises_embedding_error(
    patch_gemini, no_backoff_sleep
) -> None:
    respx.post(_SINGLE_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(VaultEmbeddingError):
        await embed_query("anything")


@respx.mock
async def test_403_raises_embedding_error_immediately(
    patch_gemini, no_backoff_sleep
) -> None:
    route = respx.post(_SINGLE_URL).mock(return_value=httpx.Response(403))
    with pytest.raises(VaultEmbeddingError):
        await embed_query("anything")
    assert route.call_count == 1  # non-transient — no retry


@respx.mock
async def test_unexpected_dim_raises_embedding_error(patch_gemini) -> None:
    respx.post(_SINGLE_URL).mock(
        return_value=httpx.Response(200, json={"embedding": {"values": [1, 2, 3]}})
    )
    with pytest.raises(VaultEmbeddingError) as excinfo:
        await embed_query("anything")
    assert "dim" in str(excinfo.value)


@respx.mock
async def test_missing_embedding_field_raises_embedding_error(patch_gemini) -> None:
    respx.post(_SINGLE_URL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(VaultEmbeddingError):
        await embed_query("anything")
