"""Tests for the Cloud Vision OCR pre-pass (services/ocr.py).

Covers the BE-2 OCR layer for scanned-image X-17A-5 PDFs:

* Happy path — Vision returns text → ``ocr_pdf`` surfaces it.
* Cache hit — same SHA-256 short-circuits to the cached value, no
  second SDK call.
* Vision SDK exception → re-raised as ``VisionOcrError`` so
  ``pdf_processor`` can map to ``provider_error``.
* Empty payload — Vision returns no text → ``ocr_pdf`` returns ''
  (the ``< 50`` char gate in the caller maps that to
  ``pipeline_error`` / ``pdf_unparseable``).

The Vision SDK is fully mocked — the test never makes a real HTTP
call. Auth is exercised via the lazy-init path
(:meth:`VisionOCR._get_client`) being monkeypatched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.ocr import VisionOCR, VisionOcrError


def _build_response(*page_texts: str) -> SimpleNamespace:
    """Construct a Vision ``BatchAnnotateFilesResponse``-like object.

    Mirrors the SDK shape that ``VisionOCR._extract_text`` reads:
    ``response.responses[0].responses[i].full_text_annotation.text``.
    """
    inner_pages = [
        SimpleNamespace(full_text_annotation=SimpleNamespace(text=text))
        for text in page_texts
    ]
    return SimpleNamespace(
        responses=[SimpleNamespace(responses=inner_pages)]
    )


@pytest.fixture
def mock_vision_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch ``VisionOCR._get_client`` to return a configurable mock."""
    client = MagicMock()
    monkeypatch.setattr(VisionOCR, "_get_client", lambda self: client)
    return client


# ─────────────────────── happy path + caching ─────────────────────────


def test_ocr_pdf_returns_text_from_vision(mock_vision_client: MagicMock) -> None:
    mock_vision_client.batch_annotate_files.return_value = _build_response(
        "Hello world", "Page two text"
    )

    ocr = VisionOCR()
    result = ocr.ocr_pdf(b"fake-pdf-bytes")

    assert result == "Hello world\n\nPage two text"
    assert mock_vision_client.batch_annotate_files.call_count == 1


def test_ocr_pdf_caches_by_sha256(mock_vision_client: MagicMock) -> None:
    mock_vision_client.batch_annotate_files.return_value = _build_response("cached")

    ocr = VisionOCR()
    first = ocr.ocr_pdf(b"identical-bytes")
    second = ocr.ocr_pdf(b"identical-bytes")

    assert first == second == "cached"
    # Second call hits the SHA-256 cache; SDK is invoked exactly once.
    assert mock_vision_client.batch_annotate_files.call_count == 1


def test_ocr_pdf_distinct_bytes_bypass_cache(mock_vision_client: MagicMock) -> None:
    mock_vision_client.batch_annotate_files.side_effect = [
        _build_response("first"),
        _build_response("second"),
    ]

    ocr = VisionOCR()
    a = ocr.ocr_pdf(b"first-pdf")
    b = ocr.ocr_pdf(b"second-pdf")

    assert a == "first"
    assert b == "second"
    assert mock_vision_client.batch_annotate_files.call_count == 2


# ───────────────────────── error handling ─────────────────────────────


def test_ocr_pdf_5xx_raises_vision_ocr_error(mock_vision_client: MagicMock) -> None:
    mock_vision_client.batch_annotate_files.side_effect = RuntimeError(
        "503 Service Unavailable"
    )

    ocr = VisionOCR()
    with pytest.raises(VisionOcrError, match="batch_annotate_files failed"):
        ocr.ocr_pdf(b"some-bytes")


def test_ocr_pdf_failed_call_does_not_poison_cache(
    mock_vision_client: MagicMock,
) -> None:
    """A failed call must not write a cache entry, otherwise the next
    invocation would silently return the stale failure result."""
    mock_vision_client.batch_annotate_files.side_effect = [
        RuntimeError("boom"),
        _build_response("recovered"),
    ]

    ocr = VisionOCR()
    with pytest.raises(VisionOcrError):
        ocr.ocr_pdf(b"retry-bytes")

    # Same bytes — cache must NOT have been populated by the failed call.
    result = ocr.ocr_pdf(b"retry-bytes")
    assert result == "recovered"
    assert mock_vision_client.batch_annotate_files.call_count == 2


def test_ocr_pdf_empty_response_returns_empty_string(
    mock_vision_client: MagicMock,
) -> None:
    """Vision can return a structurally valid response with no
    ``full_text_annotation`` payload (truly blank PDF). Surface ''
    so the caller's < 50 char gate can map it to pipeline_error."""
    mock_vision_client.batch_annotate_files.return_value = SimpleNamespace(
        responses=[SimpleNamespace(responses=[])]
    )

    ocr = VisionOCR()
    assert ocr.ocr_pdf(b"blank-pdf") == ""


def test_ocr_pdf_empty_bytes_short_circuits(mock_vision_client: MagicMock) -> None:
    """Empty input bytes never reach Vision — saves a wasted API call."""
    ocr = VisionOCR()
    assert ocr.ocr_pdf(b"") == ""
    assert mock_vision_client.batch_annotate_files.call_count == 0
