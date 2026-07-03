"""Isolated PDF text extractor — runs as a throwaway child process.

Why this file exists. ``bank_contact_extraction`` pulls people out of OCC
charter-application public-portion PDFs by extracting per-page text with
``pdfplumber`` (pdfminer.six underneath). The sibling
:mod:`app.services._pdf_render_worker` isolates PDFium because a corrupt PDF
can make native code **segfault**; pdfminer is pure Python so a SIGSEGV is
unlikely, but the same corrupt-filing class can still make it spin (pathological
xref chains / decompression bombs) or exhaust memory — either of which would
wedge or OOM-kill the whole watcher run. Per the repo's containment policy
("a corrupt PDF must never kill the run"), the extraction therefore runs in a
**separate OS process**: the parent launches it with
``subprocess.run(..., timeout=...)``; a hang is bounded by the timeout, a
crash/OOM-kill takes only the child, and the parent treats any abnormal exit
as a parse-miss (empty page list) so the surrounding batch keeps going.

Contract:
    argv[1] = input PDF path (already on disk)
    argv[2] = output JSON path (the parent reads this on a clean exit)
    argv[3] = max pages to extract (int; 0 or negative = all pages)

    On a clean exit (return code 0) the output file holds a JSON list of
    ``{"page": <1-based int>, "text": "<extracted text>"}`` dicts — possibly
    empty when the PDF is corrupt-but-openable (pdfplumber raised a catchable
    error). A non-zero / negative return code means the child died; the
    parent discards the run as a parse-miss.

Import hygiene. This module imports **nothing from ``app.*``** — only stdlib
plus ``pdfplumber`` (loaded lazily inside :func:`_extract`). It is invoked by
absolute path (``python /…/_pdf_text_worker.py``), so it never needs to
resolve as a package.
"""

from __future__ import annotations

import json
import logging
import os
import sys


def _extract(pdf_path: str, max_pages: int) -> list[dict[str, object]]:
    """Extract per-page text from ``pdf_path``.

    A *catchable* pdfplumber/pdfminer failure propagates to :func:`main`,
    which turns it into an empty list — the parse-miss contract. A per-page
    failure skips just that page (one mangled page must not discard the
    others).
    """
    import pdfplumber  # lazy: heavy import happens only in the child

    pages: list[dict[str, object]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages):
            if max_pages > 0 and index >= max_pages:
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
            if text.strip():
                pages.append({"page": index + 1, "text": text})
    return pages


def main(argv: list[str]) -> int:
    # pdfminer chatter ("Data-loss while decompressing corrupted data", CMap
    # warnings) is noise in the child; match the render worker's mute.
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    logging.getLogger("pdfplumber").setLevel(logging.ERROR)

    # Fault-injection hook (tests only) — identical convention to
    # ``_pdf_render_worker._FIS_PDF_RENDER_ABORT``: the child dies by signal
    # before touching the PDF, giving the suite a deterministic stand-in for
    # a native crash / OOM kill. Never set in production.
    if os.environ.get("_FIS_PDF_TEXT_ABORT") == "1":
        os.abort()

    if len(argv) < 4:
        return 2
    pdf_path, out_path, max_pages_raw = argv[1], argv[2], argv[3]
    try:
        max_pages = int(max_pages_raw)
    except (TypeError, ValueError):
        max_pages = 0

    try:
        pages = _extract(pdf_path, max_pages)
    except Exception:
        # Corrupt-but-catchable → parse-miss, same contract as the renderer.
        pages = []

    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(pages, fh)
    except OSError:
        # Could not write results — signal failure so the parent parse-misses
        # rather than reading a stale/partial file.
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
