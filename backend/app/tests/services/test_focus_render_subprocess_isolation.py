"""Crash-isolation regression tests for the FOCUS PDF page renderer.

Background. ``focus_ceo_extraction._render_pdf_pages_to_images`` renders
X-17A-5 PDF pages to JPEGs via ``pypdfium2`` — a binding around Google's
PDFium **C++** library. A malformed filing (the "Data-loss while decompressing
corrupted data" class) can make PDFium **segfault** *inside* the native
render. A SIGSEGV is not a Python exception, so no ``try/except`` in-process
can catch it: the whole worker dies. On the staging gap-fill job
(``maxRetries=0``) that took the form ``Container terminated on signal 11``
after ~6 of 300 firms.

The fix runs the render in a throwaway **subprocess** so a native crash kills
only the child; the parent detects the abnormal exit and returns ``[]`` (a
parse-miss). These tests pin that contract:

* a corrupt/truncated PDF yields a parse-miss, never a crash;
* a child that dies **by signal** (a faithful stand-in for the SIGSEGV, via the
  worker's env-gated ``os.abort()`` hook) is contained — the parent survives
  and parse-misses;
* timeouts, launch failures, and signal-killed return codes all parse-miss;
* a well-formed PDF still renders (the isolation didn't break the happy path).

The fact that this test module runs to completion is itself the assertion that
a corrupt PDF cannot take the process down.
"""

from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from app.services import focus_ceo_extraction
from app.services.focus_ceo_extraction import _render_pdf_pages_to_images


def _write_valid_pdf(path: Path, pages: int = 1) -> None:
    """Mint a minimal but genuinely valid multi-page PDF on disk."""
    doc = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            doc.new_page(200, 200)
        with path.open("wb") as fh:
            doc.save(fh)
    finally:
        doc.close()


# ──────────────────────── the core regression ────────────────────────


def test_valid_pdf_renders_images_through_subprocess(tmp_path: Path) -> None:
    """Happy path: the isolation boundary didn't break real rendering."""
    pdf_path = tmp_path / "valid.pdf"
    _write_valid_pdf(pdf_path, pages=2)

    images = _render_pdf_pages_to_images("", local_path=str(pdf_path))

    assert isinstance(images, list)
    assert images, "a valid PDF should render at least one page image"
    for img in images:
        assert img["mime_type"] == "image/jpeg"
        assert isinstance(img["data"], str) and img["data"]


def test_truncated_pdf_yields_parse_miss(tmp_path: Path) -> None:
    """A corrupt/truncated PDF must parse-miss (``[]``), not raise or crash."""
    bad = tmp_path / "truncated.pdf"
    bad.write_bytes(b"%PDF-1.5\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EO")

    assert _render_pdf_pages_to_images("", local_path=str(bad)) == []


def test_garbage_bytes_yield_parse_miss(tmp_path: Path) -> None:
    """Non-PDF garbage behind a .pdf name is a parse-miss too."""
    bad = tmp_path / "garbage.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not a pdf " + b"\x00\xff" * 64)

    assert _render_pdf_pages_to_images("", local_path=str(bad)) == []


def test_native_child_crash_is_contained(tmp_path: Path, monkeypatch) -> None:
    """A child that dies **by signal** (SIGABRT here, standing in for the real
    SIGSEGV) is contained: the parent gets ``[]`` and keeps running.

    This is the faithful reproduction of the staging crash — a real OS process
    terminated by a signal — without depending on a magic PDF that reliably
    segfaults a specific PDFium build. The worker's ``_FIS_PDF_RENDER_ABORT``
    hook makes the child ``os.abort()`` before it touches PDFium.
    """
    pdf_path = tmp_path / "valid.pdf"
    _write_valid_pdf(pdf_path)  # valid input: the ONLY reason for a bad exit is the abort

    monkeypatch.setenv("_FIS_PDF_RENDER_ABORT", "1")

    result = _render_pdf_pages_to_images("", local_path=str(pdf_path))

    assert result == []  # contained → parse-miss
    # Reaching this line proves the parent process was not killed by the child.


def test_signal_killed_returncode_is_contained(tmp_path: Path, monkeypatch) -> None:
    """A negative return code (killed by signal, e.g. -11 == SIGSEGV) → parse-miss.

    Hermetic: patches ``subprocess.run`` so nothing is actually spawned, pinning
    the branch that maps a signal-killed child to an empty result.
    """
    pdf_path = tmp_path / "valid.pdf"
    _write_valid_pdf(pdf_path)

    def _fake_run(*_args, **_kwargs):
        return types.SimpleNamespace(returncode=-11)

    monkeypatch.setattr(focus_ceo_extraction.subprocess, "run", _fake_run)

    assert _render_pdf_pages_to_images("", local_path=str(pdf_path)) == []


def test_render_timeout_is_contained(tmp_path: Path, monkeypatch) -> None:
    """A hung render is bounded by the timeout and parse-misses."""
    pdf_path = tmp_path / "valid.pdf"
    _write_valid_pdf(pdf_path)

    def _fake_run(*_args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="render", timeout=kwargs.get("timeout", 60))

    monkeypatch.setattr(focus_ceo_extraction.subprocess, "run", _fake_run)

    assert _render_pdf_pages_to_images("", local_path=str(pdf_path)) == []


def test_launch_failure_is_contained(tmp_path: Path, monkeypatch) -> None:
    """If the interpreter can't even be launched, still parse-miss (never let
    the isolation layer itself crash the caller)."""
    pdf_path = tmp_path / "valid.pdf"
    _write_valid_pdf(pdf_path)

    def _fake_run(*_args, **_kwargs):
        raise OSError("cannot spawn")

    monkeypatch.setattr(focus_ceo_extraction.subprocess, "run", _fake_run)

    assert _render_pdf_pages_to_images("", local_path=str(pdf_path)) == []


def test_no_source_pdf_yields_empty() -> None:
    """No local path and no base64 → nothing to render → ``[]``."""
    assert _render_pdf_pages_to_images("", local_path=None) == []
    assert _render_pdf_pages_to_images("", local_path="/nonexistent/path.pdf") == []


def test_base64_source_is_rendered(tmp_path: Path) -> None:
    """When only base64 bytes are available (no on-disk path), the wrapper
    materializes them to a tempfile and still renders."""
    import base64

    pdf_path = tmp_path / "valid.pdf"
    _write_valid_pdf(pdf_path)
    b64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")

    images = _render_pdf_pages_to_images(b64, local_path=None)

    assert images and images[0]["mime_type"] == "image/jpeg"
