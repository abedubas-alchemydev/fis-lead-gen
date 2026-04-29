# ADR-0001 — Replace 9 GB PDF cache with streaming FINRA + LLM Files API ingestion

- **Status:** Proposed
- **Date:** 2026-04-29
- **Authors:** Arvin B. Edubas
- **Supersedes:** —
- **Superseded by:** —

## Context

The fis-lead-gen pipeline ingests broker-dealer financial PDFs from SEC
EDGAR (X-17A-5 / FOCUS reports), parses them with Gemini, and writes
extracted clearing arrangements + financial metrics to Postgres. Until
Sprint 2 the path looked like this:

1. Resolve the latest filing from `submissions.json`.
2. Download the PDF from `www.sec.gov/Archives/...` into
   `settings.pdf_cache_dir` (`PDF_CACHE_DIR`), keyed by SEC URL.
3. Read it back from disk on every extraction.
4. Send the bytes inline (base64) to Gemini's `generateContent`.

Two problems shaped the 2026-04-27 client meeting (Bucket 6, "Stream FINRA
filings via API instead of bulk PDF download"):

- **Storage**. The cache grew from 0 → ~9 GB over Sprint 2. Cloud Run
  revisions either shipped the cache in the image (huge image, slow cold
  start) or shipped without it (every cold start re-downloads from SEC).
- **Staleness**. Cache entries were never invalidated. When SEC re-filed
  a corrected document under the same URL, our cache returned the stale
  PDF and the pipeline extracted from the wrong revision. Deshorn flagged
  one such case at the meeting and asked for live API streaming so the
  extracted data tracks SEC.

Sprint 2 / cli01 work landed an interim fix on `develop`:
**per-extraction tempdir** (`pdf_tempdir()` in
`backend/app/services/pdf_downloader.py:23-42`). Each pipeline call
creates a `tempfile.TemporaryDirectory`, writes the PDF, parses, then the
`with` block deletes it. `settings.pdf_cache_dir` is now an optional
parent-dir hint for local debug; in production it is unset and tempdirs
land in the system temp. The persistent 9 GB footprint is gone on
`develop`. This ADR records that decision and lays out the remaining work
to reach the "live API streaming" target.

A current-state audit (`reports/streaming-ingestion-audit-2026-04-29.md`,
local to the author's working tree — `reports/` is gitignored) confirmed
that on `develop` Phase 1 has shipped, Gemini's Files API path is
threshold-gated for large PDFs only, the SEC fetch still uses
`response.content` rather than `httpx.stream`, OpenAI has no Files API
integration, and `DownloadedPdfRecord.bytes_base64` still carries the
whole PDF through the service layer.

## Decision

Move the ingestion path from "download whole PDFs into memory, write to
disk, base64-encode, send inline to LLM" to **stream the bytes from SEC to
the LLM provider's Files API**, treating the LLM provider as the
authoritative document store for the duration of an extraction.

Concretely:

1. **Per-extraction tempdir as the only on-disk surface** (already
   shipped in Phase 1). No persistent cache. No
   `PDF_CACHE_DIR`-as-cache. The tempdir is purely scratch space for the
   case where the parser needs a `Path` (some PDF libraries do).
2. **`httpx.AsyncClient.stream("GET", url)` + `aiter_bytes()`** in
   `pdf_downloader._download_bytes_with_retries`. The PDF lands on disk
   chunk-by-chunk; we never hold the whole filing in process memory.
3. **LLM Files API as the default upload path**, not just a
   threshold-gated branch. Gemini's Files API today has a 24-hour TTL
   and addressable `file_id` semantics; OpenAI's Files API is the
   counterpart. Send the LLM call with `file_uri` references, not
   `inline_data`.
4. **Cache key = SEC accession number + document timestamp**, not URL.
   Re-files get a new accession number, so a re-file forces a fresh
   upload by construction — staleness collapses out of the design.
5. **In-process LRU of `(accession_number → file_id)`** scoped to one
   Cloud Run revision, capped by the 24-hour Files API TTL. Lets retries
   within a single batch hit the provider-side cache instead of paying
   re-upload cost.
6. **Drop `bytes_base64` from `DownloadedPdfRecord`** in favor of a
   `file_id` (and `Path` only when a downstream parser needs disk
   access). The base64 buffer is the largest single allocation in the
   pipeline today; eliminating it keeps memory proportional to chunk
   size, not filing size.

## Alternatives considered

### A. Keep the disk cache, add invalidation by accession number

Half-measure. Solves staleness but keeps the 9 GB-class footprint and the
cold-start re-download tradeoff. Already obsolete vs. Phase 1.

### B. Stream raw bytes inline on every call (no Files API)

Works for small filings but pays the LLM ingress cost on every retry, has
no reuse window inside a logical batch, and doesn't extend cleanly to the
60+ MB FOCUS PDFs that already exceed Gemini's `inline_data` ceiling.
Files API is required for the long tail regardless.

### C. Stream to a CDN we own (Cloud Storage)

Adds a third storage layer with no correctness or latency benefit over
the provider's own Files API. Useful only if we needed durable archival
of extracted filings (we don't — Postgres already stores extracted
fields). Rejected.

### D. Switch entirely to SEC's structured-data endpoints (no PDF)

SEC's company-facts JSON covers some financial concepts but not the
clearing-arrangement language we extract from X-17A-5 narrative
sections. Today there is no SEC API parity for the FOCUS narrative
content. Deferred to Phase 3 — re-evaluate when SEC ships parity.

## Consequences

**Positive**

- Persistent disk footprint stays at 0 GB on Cloud Run.
- Cold-start image size drops to whatever the runtime weighs without the
  cache layer (already true in Phase 1; this ADR locks it in).
- Re-files automatically force fresh extraction. No invalidation logic
  to maintain.
- Memory ceiling per worker becomes O(chunk size), not O(filing size).
- The Gemini and OpenAI codepaths converge on a single Files-API-shaped
  contract.

**Negative / accepted tradeoffs**

- Every cold start incurs an SEC re-fetch (~20–40 s for a 50 MB FOCUS
  PDF, dominated by SEC's egress). Mitigation: in-process file_id LRU so
  hot revisions hit the provider cache.
- 24-hour Files API TTL means retry/queue logic must handle "expired
  `file_id`" → re-upload. This is a new error class to code for.
- Gemini Files API has per-key rate limits. Current quota is comfortable
  for the catalog (~3 000 BDs, weekly cadence) but spikes during a full
  refill could throttle. Add throttle-aware retry on `429` from the
  upload endpoint.

**Risks**

- Provider TTL changes: if Gemini shortens the 24-hour TTL, the LRU's
  effective lifespan shrinks and retries cost more. Track via cost
  metric, not config (re-evaluate if cost/run rises >2x).
- OpenAI Files API parity is a new code path; `LLM_PROVIDER=openai` has
  no Files API integration today. The Phase 2 work must add it or accept
  that OpenAI runs are inline-only and capped at
  `openai_max_pdf_size_mb`.

## Implementation phases

### Phase 1 — Per-extraction tempdir — **DONE on `develop`**

- `pdf_tempdir()` context manager in `pdf_downloader.py`.
- `pipeline.py` wraps each BD in `with pdf_tempdir(...) as tmp_dir:`.
- `settings.pdf_cache_dir` reduced to optional parent-dir hint.
- Persistent 9 GB cache no longer present in production.

This phase is recorded here for traceability — no further work.

### Phase 2 — Streaming download + Files API as default — **PROPOSED**

- `pdf_downloader._download_bytes_with_retries`: replace
  `response = await client.get(url); response.content` with
  `async with client.stream("GET", url) as r: async for chunk in r.aiter_bytes(...): f.write(chunk)`.
  Bytes never aggregate in memory.
- `gemini_responses._call_pdf`: drop the
  `gemini_files_api_threshold_mb` branch — every call goes through
  `_upload_pdf_to_files_api`. Inline base64 path stays only as a
  fallback for the ≤2 MB case if any (TBD during implementation).
- `gemini_responses`: chunked upload (multipart/related stays, but the
  body source is a streaming reader, not the whole `pdf_bytes` buffer).
- `openai_responses`: add Files API parity (`POST /v1/files`,
  `purpose="user_data"`, then reference `file_id` in the
  `responses.create` call) so `LLM_PROVIDER=openai` reaches feature
  parity with Gemini.
- `service_models.DownloadedPdfRecord`: drop `bytes_base64`. Replace
  with `file_id: str` (provider-scoped) and keep
  `local_document_path` only where a downstream parser needs a `Path`.
- Add `accession_number → file_id` LRU keyed per process. TTL ≤ 24 h
  (matches Gemini Files API). Cap size to bound memory.
- Feature flag: `LLM_USE_FILES_API` (default `false` in staging,
  flip on after a 48 h soak, then flip in prod).
- Migration story: ship behind the flag; rollback = flag flip back to
  `false`. The in-memory LRU drains on the next deploy regardless.

### Phase 3 — Drop the SEC PDF dependency (deferred)

- Evaluate whether SEC's structured-data endpoints cover the
  clearing-arrangement narrative we currently parse from FOCUS PDFs.
- If yes, retire the PDF path entirely and read from SEC JSON.
- Re-evaluate annually or whenever SEC announces an API expansion.

## Rollout & rollback

- **Rollout (Phase 2):** ship behind `LLM_USE_FILES_API=false`. Flip
  on in `fis-backend-staging` first, soak 48 h with the
  filing-monitor and clearing-extraction pipelines exercised end to
  end. Flip on in `fis-backend` once staging burns clean.
- **Rollback:** flag flip to `false`. The LRU has no persistent state
  and drains on the next deploy. Cloud Run revisions are immutable per
  `CLAUDE.md`, so the standard `gcloud run services update-traffic
  --to-revisions=<LAST_GOOD_REV>=100` rollback applies if the flag flip
  alone isn't sufficient.

## Open questions

1. **Files API persistence across Cloud Run revisions.** Confirmed
   per-API-key, so a `file_id` uploaded by revision N is reachable from
   revision N+1 — the LRU is the only thing that resets on deploy, the
   provider-side resource survives.
2. **Cost comparison.** Today: $0 for the persistent cache (storage was
   inside the Cloud Run image), $X for SEC egress (free), $Y for Gemini
   ingress (per-token). After Phase 2: $0 storage, $X SEC egress (same),
   $Y Gemini ingress (same — Files API doesn't change tokens), plus
   negligible Files API request quota. Net cost: roughly flat. Confirm
   with the Gemini billing dashboard once Phase 2 has run for a week.
3. **Per-file size limit on the Gemini Files API.** Current spec is
   2 GB. The largest FOCUS PDF in our catalog is well under 100 MB;
   headroom is comfortable. Re-check during implementation.
4. **OpenAI Files API quota.** Not yet measured for our access tier.
   Surveyed during Phase 2 implementation — if it can't sustain a full
   catalog refill, leave OpenAI on inline-only and document the
   limitation.

## References

- 2026-04-27 client meeting notes (Bucket 6).
- Phase 1 implementation:
  - `backend/app/services/pdf_downloader.py:23-42` — `pdf_tempdir()`.
  - `backend/app/services/pdf_downloader.py:78-85` — service docstring.
  - `backend/app/services/pipeline.py:70` — call site.
- Existing Files API code (Gemini-only, threshold-gated):
  - `backend/app/services/gemini_responses.py:414` —
    `_build_files_api_payload`.
  - `backend/app/services/gemini_responses.py:469` — `_call_pdf` routing.
  - `backend/app/services/gemini_responses.py:523` —
    `_upload_pdf_to_files_api`.
  - `backend/app/services/gemini_responses.py:608` —
    `_delete_files_api_file`.
- Settings:
  - `backend/app/core/config.py:45` — `pdf_cache_dir`.
  - `backend/app/core/config.py:105` — `gemini_inline_pdf_max_size_mb`.
  - `backend/app/core/config.py:109` — `gemini_files_api_threshold_mb`.
  - `backend/app/core/config.py:115` — `openai_max_pdf_size_mb`.
