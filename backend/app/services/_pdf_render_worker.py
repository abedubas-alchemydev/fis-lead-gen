"""Isolated PDF page renderer — runs as a throwaway child process.

Why this file exists. ``focus_ceo_extraction._render_pdf_pages_to_images``
renders X-17A-5 PDF pages to JPEGs via ``pypdfium2`` (a binding around
Google's PDFium **C++** library) before handing them to Gemini vision. PDFium
is native code: a sufficiently malformed PDF — the ones pdfminer flags with
"Data-loss while decompressing corrupted data" — can make it walk off into a
segmentation fault (SIGSEGV / signal 11) *inside* ``page.render()``. A SIGSEGV
is **not** a Python exception, so no ``try/except`` in the calling process can
catch it: the whole worker dies. In the bulk gap-fill job (``maxRetries=0``)
that means one poison PDF kills a 300-firm run after a handful of successes —
exactly the ``Container terminated on signal 11`` crash we saw on staging.

Containment. This module does the render in a **separate OS process**. The
parent (``focus_ceo_extraction._render_pdf_pages_to_images``) launches it with
``subprocess.run(..., timeout=...)``. If PDFium segfaults, only *this* child
dies; the parent sees a negative return code and treats it as a parse-miss
(empty image list), so the surrounding sub-pipeline degrades to Gemini's
raw-PDF path instead of taking down the batch. A hang is bounded by the
parent's timeout.

Contract:
    argv[1] = input PDF path (already on disk, e.g. the downloader tempfile)
    argv[2] = output JSON path (the parent reads this on a clean exit)
    argv[3] = render DPI (float)

    On a clean exit (return code 0) the output file holds a JSON list of
    ``{"mime_type": "image/jpeg", "data": "<base64>"}`` dicts — possibly empty
    when the PDF is corrupt-but-openable (PDFium raised a *catchable*
    ``PdfiumError``). A non-zero / negative return code means the child died
    (native crash or was killed); the parent discards it as a parse-miss.

Import hygiene. This module imports **nothing from ``app.*``** — only stdlib
plus ``pypdfium2`` / ``PIL`` (loaded lazily inside :func:`_render`). That keeps
the child cheap to spawn and free of import-time side effects (no settings, DB
engine, or provider SDK construction). It is invoked by absolute path
(``python /…/_pdf_render_worker.py``), so it never needs to resolve as a
package.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import sys

# Keep the render loop byte-for-byte in step with the pre-isolation in-process
# implementation so extraction quality is unchanged — only the *process
# boundary* is new.
_MAX_VISION_PAYLOAD_BYTES = 4 * 1024 * 1024  # 4MB total image payload for Gemini
_JPEG_QUALITY = 80


def _select_page_indices(total_pages: int) -> list[int]:
    """Pages 1-8 (facing sheet + financial statements + net-capital schedule)
    plus the last 3 (supplemental schedules). Identical selection to the
    original in-process renderer."""
    if total_pages <= 12:
        return list(range(total_pages))
    page_indices = list(range(min(8, total_pages)))
    last_start = max(8, total_pages - 3)
    page_indices.extend(range(last_start, total_pages))
    return sorted(set(page_indices))


def _render(pdf_path: str, dpi: float) -> list[dict[str, str]]:
    """Render the selected pages of ``pdf_path`` to base64 JPEGs.

    Mirrors the original ``_render_pdf_pages_to_images`` body: same page
    selection, same JPEG quality, same 4MB payload cap with the "keep going
    until we have at least 3 images" rule. A *catchable* PDFium failure
    (``PdfiumError`` on a corrupt-but-parseable document) propagates to
    :func:`main`, which turns it into an empty list — same as the legacy path.
    An *uncatchable* native crash never reaches here in a recoverable form; it
    kills the process and the parent contains it.
    """
    import pypdfium2 as pdfium  # lazy: the native lib loads only in the child

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        total_pages = len(pdf)
        images: list[dict[str, str]] = []
        total_bytes = 0

        for page_idx in _select_page_indices(total_pages):
            page = pdf[page_idx]
            bitmap = page.render(scale=dpi / 72)
            pil_image = bitmap.to_pil()

            buf = io.BytesIO()
            if pil_image.mode == "RGBA":
                pil_image = pil_image.convert("RGB")
            pil_image.save(buf, format="JPEG", quality=_JPEG_QUALITY)
            img_bytes = buf.getvalue()

            # Stop once the payload is full, but only after we have a few pages.
            if (
                total_bytes + len(img_bytes) > _MAX_VISION_PAYLOAD_BYTES
                and len(images) >= 3
            ):
                break

            images.append(
                {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(img_bytes).decode("utf-8"),
                }
            )
            total_bytes += len(img_bytes)

        return images
    finally:
        pdf.close()


def main(argv: list[str]) -> int:
    # pypdf / pypdfium2 chatter is noise in the child; match the parent's mute.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    logging.getLogger("pypdfium2").setLevel(logging.ERROR)

    # Fault-injection hook (tests only). When this env var is set the child
    # dies by signal *before* touching PDFium, giving the regression suite a
    # faithful, deterministic stand-in for a native SIGSEGV without needing a
    # magic PDF that reliably crashes a specific PDFium build. Never set in
    # production. ``os.abort()`` raises SIGABRT → the parent sees a negative
    # return code, exactly as it would for SIGSEGV.
    if os.environ.get("_FIS_PDF_RENDER_ABORT") == "1":
        os.abort()

    if len(argv) < 4:
        return 2
    pdf_path, out_path, dpi_raw = argv[1], argv[2], argv[3]
    try:
        dpi = float(dpi_raw)
    except (TypeError, ValueError):
        dpi = 150.0

    try:
        images = _render(pdf_path, dpi)
    except Exception:
        # Corrupt-but-catchable (PdfiumError, decode errors, etc.) → parse-miss.
        # Identical outcome to the legacy in-process ``except Exception``.
        images = []

    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(images, fh)
    except OSError:
        # Could not write results — signal failure so the parent parse-misses
        # rather than reading a stale/partial file.
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
