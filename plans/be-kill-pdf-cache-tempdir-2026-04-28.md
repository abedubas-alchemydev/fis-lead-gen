# Kill the persistent PDF cache — Sprint 2 task #20 Phase 1

**Date:** 2026-04-28
**Branch:** `feature/be-kill-pdf-cache-tempdir`
**Auto-promote:** YES (develop → main, no migrations)

## Why

In the 2026-04-27 client meeting Deshorn flagged that the system "feels heavy
and outdated." The proximate cause is `PDF_CACHE_DIR`, the persistent disk
cache that has accumulated **~9 GB** of FOCUS PDFs on the `fis-backend`
container. The cache existed to short-circuit re-downloads on repeat
extractions, but extracted values are persisted to the DB so a re-run is the
exception, not the norm. The cache earns its keep poorly.

PDFs aren't truly streamable (random-access xref tables), but a per-extraction
`tempfile.TemporaryDirectory()` gets us 95 % of the way there:

1. Create a tempdir.
2. Download the FOCUS PDF into it.
3. Read the PDF (pdfplumber / pypdfium2 / Gemini Files API upload).
4. Persist extracted values to the DB.
5. The `with` block exits — the tempdir and the PDF inside it disappear.

Local container footprint goes from **9 GB → < 100 MB at any moment**. Re-
extraction re-downloads (~3-5 s extra latency on user click — acceptable UX
because it happens rarely and only by explicit user action).

Phase 2 (promote Gemini Files API path to default — task #23) waits for this
to soak.

## Cache surface (mapped 2026-04-28)

### Writers
1. `backend/app/services/pdf_downloader.py`
   - `PdfDownloaderService.__init__` — eagerly creates `self.cache_dir` from
     `settings.pdf_cache_dir`.
   - `_download_filing_pdf` — `pdf_path = self.cache_dir / f"{cik}-{accession}.pdf"`
     write + cache-hit fast path.
   - `_download_live_pdf` — same pattern.
2. `backend/app/services/finra_pdf_service.py`
   - `fetch_and_cache_brokercheck_pdf` — writes
     `Path(settings.pdf_cache_dir) / "finra" / f"{crd}.pdf"` with cache-hit
     fast path.

### Readers (consumers of `local_document_path`)
1. `services/focus_ceo_extraction._render_pdf_pages_to_images` —
   `pdfium.PdfDocument(local_path)`.
2. `services/focus_ceo_extraction.extract` (line 222) —
   `extract_from_pdf(pdf_record.local_document_path)` (pdfplumber).
3. `services/focus_ceo_extraction._extract_without_db` (line 393) — same.
4. `api/v1/endpoints/broker_dealers.py:152` — `download_focus_report_pdf`
   returns `FileResponse(path=record.local_document_path)`.
5. `api/v1/endpoints/broker_dealers.py:195` — `download_brokercheck_pdf`
   returns `FileResponse(path=str(cache_path))`.

### Critical wrinkle — FastAPI `FileResponse` streaming
`FileResponse` opens its file at write-time, AFTER the endpoint function has
returned. Wrapping the download in a naive `with TemporaryDirectory()` would
delete the file before the response stream began, so the on-demand
`/focus-report.pdf` and `/brokercheck.pdf` endpoints need a different
treatment: serve from the in-memory bytes (`record.bytes_base64` and the
already-bytes-returning `fetch_brokercheck_pdf`) via `Response(content=…)`.

## New boundary

### `pdf_downloader.py`
- New module-level `pdf_tempdir(prefix=…)` context manager — yields a
  per-call `Path`. Honors `settings.pdf_cache_dir` as the *parent* directory
  when set (local-debug override); otherwise falls back to system temp.
- `PdfDownloaderService.__init__` — no more eager `cache_dir.mkdir(...)`. The
  service is a thin client; storage is the caller's concern.
- `download_latest_x17a5_pdf(broker_dealer, dest_dir: Path)` — new required
  param.
- `download_recent_x17a5_pdfs(broker_dealer, dest_dir: Path, count=2)` — new
  required param.
- Both internal `_download_*_pdf` methods drop the cache-hit fast paths and
  always download fresh, writing to `dest_dir / f"{cik}-{accession}.pdf"`.

### `finra_pdf_service.py`
- Drop `fetch_and_cache_brokercheck_pdf`. Keep `fetch_brokercheck_pdf` (which
  already returns `bytes`). The `/brokercheck.pdf` endpoint takes the bytes
  directly — no disk involved.

### Caller wrappers (each adds `with pdf_tempdir(...)`)
- `services/pipeline.py` `ClearingPipelineService.run` — wrap each iteration's
  download + processor call.
- `services/focus_reports.py` `_extract_live_records_from_pdfs` — wrap each
  BD's download + Gemini call.
- `services/focus_ceo_extraction.py` `extract` and `_extract_without_db` —
  wrap entire flow (pdfplumber + Gemini all happen inside the same `with`).
- `api/v1/endpoints/broker_dealers.py` `download_focus_report_pdf` — wrap
  download in `pdf_tempdir`, `base64.b64decode(record.bytes_base64)`, return
  `Response(content=pdf_bytes, media_type="application/pdf", headers={...})`.
- `api/v1/endpoints/broker_dealers.py` `download_brokercheck_pdf` — call
  `fetch_brokercheck_pdf(crd)` directly (returns bytes), return `Response(...)`
  — no tempdir needed because nothing touches disk.

### `core/config.py`
- `pdf_cache_dir: str = ".tmp/pdf-cache"` → `pdf_cache_dir: str | None = None`
  with a docstring noting it's now an *optional* parent for tempdirs (local
  debug override). Default uses system temp.

### `pdf_processor.py`, `gemini_responses.py`
- No changes. Both already operate on in-memory `bytes_base64`; neither reads
  from disk.

## Tests

### Updated
- `test_pdf_downloader.py` — keep all SSRF / retry tests (they exercise the
  validator and `_get_*_with_retries` directly, no cache touch). Add:
  - `test_download_writes_to_supplied_dest_dir` — mock the SEC fetch, assert
    the file lands at `dest_dir / "{cik}-{accession}.pdf"`.
  - `test_pdf_tempdir_cleans_up_on_exit` — call `pdf_tempdir`, write a file
    inside, assert the file path is gone after the `with` block exits.
  - `test_pdf_tempdir_honors_settings_pdf_cache_dir` — when
    `settings.pdf_cache_dir` is set to a tmp_path, the yielded path lives
    inside it.
- `test_finra_pdf_service.py` — drop the two `fetch_and_cache_*` tests
  (function removed). Keep the three `fetch_brokercheck_pdf` tests
  (200 / 404 / non-PDF body).

### Untouched (pass through unchanged)
- `test_focus_reports_multi_year.py`, `test_financial_extraction_status.py`,
  `test_focus_ceo_persists_net_capital.py` — they mock
  `service.downloader.download_*_x17a5_pdfs` via `AsyncMock`. The mock
  absorbs any new positional/keyword args silently; the prepared
  `DownloadedPdfRecord` fixture flows through unchanged.

## Review-queue invariants preserved
The cache change is purely a storage refactor. None of the
`extraction_status="needs_review"` paths in `focus_reports.py`,
`focus_ceo_extraction.py`, or the LLM helpers touch the file system.
Confidence-classifier branches stay byte-identical.

## Migrations
None. No model files touched.

## Rollback
`gcloud run services update-traffic fis-backend --region=us-central1 \
  --project=fis-lead-gen --to-revisions=<LAST_GOOD_REV>=100`
