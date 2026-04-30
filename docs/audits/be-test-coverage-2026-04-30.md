# BE test-coverage audit — 2026-04-30

**Trigger:** the 2026-04-29 `user_favorite` P1 incident (PRs #172 / #173) was caused in part by missing test coverage on `services/user_lists.py` that would have caught the dropped-table case before it 500'd in prod. cli01 ran a read-only coverage audit to identify other modules with the same risk shape.

## Method

- `pytest app/tests/ --cov=app --cov-report=term-missing --cov-report=json:coverage.json` against `backend/`.
- Ran with the repo's default selection (`addopts = -m "not integration"` from `pytest.ini`), i.e. **what CI actually runs on every PR**. Integration-marked files are excluded — see "Coverage gap class #2" below.
- Per-file coverage extracted from `coverage.json` and bucketed by source-only files under `app/` (excluding `__init__.py` and `app/tests/**`).
- Snapshot point: `origin/develop` at HEAD `262e575` ("Update Tier 2 runbook with activation results (#196)").

## Overall coverage

- **Total: 65.2 %** — 4 208 / 12 078 statements missing.
- 92 source modules measured, of which:
  - **23 HIGH** ( < 50 %)
  - **8 MEDIUM** (50 – 70 %)
  - **13 LOW** (70 – 85 %)
  - **48 OK** ( ≥ 85 %)
- Tests run in default selection: **445 passed, 95 deselected** (the 95 are integration-marked).
- Branch coverage: not measured (no `--cov-branch` flag in CI today). **Recommendation:** enable in a follow-up so the audit grows teeth.

## Coverage gap class #1 — services with NO test file at all

These modules have no `test_<name>.py` anywhere under `app/tests/`. Anything that breaks here breaks silently in CI.

| Module | Coverage | Statements | Notes |
|---|---|---|---|
| `services/data_merge.py` | **0.0 %** | 0 / 102 | Tri-stream merge (FINRA + EDGAR + classifier). PRD-critical. Zero tests. |
| `services/edgar.py` | **0.0 %** | 0 / 294 | SEC EDGAR submissions JSON → X-17A-5 PDF resolver. Largest untested module. |
| `services/finra.py` | 9.4 % | 25 / 265 | FINRA BrokerCheck client. Exercised only transitively via fixture loaders. |
| `services/filing_monitor.py` | 16.8 % | 24 / 143 | Cron-driven filing alerts. Tier 2 just activated 2026-04-30 (see runbook). |
| `services/normalization.py` | 24.5 % | 13 / 53 | String/value normalization helpers used across scoring + merge. |
| `services/classification.py` | 25.9 % | 21 / 81 | Self-clearing vs introducing classifier (Stream C). PRD logic. |
| `services/settings.py` | 25.6 % | 11 / 43 | Scoring-weight settings persistence. |
| `services/pdf_processor.py` | 32.8 % | 19 / 58 | Wraps PDF → text for the LLM extractor. |
| `services/pdf_text_extractor.py` | 21.7 % | 25 / 115 | Backend of PDF processor. |
| `services/llm_parser.py` | 34.1 % | 15 / 44 | Provider dispatcher (Gemini / OpenAI / Anthropic). |
| `services/email_extractor/apollo_enrichment.py` | 20.8 % | 15 / 72 | Apollo API enrichment. |
| `services/email_extractor/verification_runner.py` | 24.0 % | 12 / 50 | Email verification batch runner. |
| `services/export_service.py` | 34.9 % | 22 / 63 | CSV export (PRD-locked: 9 cols, 100 rows, 3/day cap). Watermark + audit log live here. |
| `services/focus_ceo_extraction.py` | 46.5 % | 112 / 241 | FOCUS-report CEO contact extractor. |
| `services/focus_reports.py` | 66.2 % | 172 / 260 | FOCUS report ingest. |
| `services/contact_discovery/apollo_match.py` | 78.0 % | 64 / 82 | Apollo person-match. |
| `services/contacts.py` | 77.2 % | 105 / 136 | Contact persistence + enrichment guard. |
| `services/alerts.py` | 50.7 % | 38 / 75 | Alert generation. |
| `services/contact_discovery/orchestrator.py` | 86.8 % | 59 / 68 | Already OK; flagged only because no dedicated test file. |
| `services/email_extractor/base.py` | 100 % | 16 / 16 | Pure types — fine. |
| `services/extraction_status.py` | 100 % | 14 / 14 | Pure constants — fine. |
| `services/service_models.py` | 91.2 % | 124 / 136 | Indirectly covered. |
| `services/stats_service.py` | 73.0 % | 27 / 37 | Stats endpoint helper. |
| `api/v1/endpoints/email_extractor.py` | 34.4 % | 33 / 96 | HTTP layer for extractor. |
| `api/v1/endpoints/export.py` | 56.2 % | 18 / 32 | HTTP layer for the PRD-locked export. |
| `api/v1/endpoints/health.py` | 83.3 % | 5 / 6 | Trivial; fine. |
| `api/v1/endpoints/stats.py` | 64.9 % | 24 / 37 | Dashboard stats handler. |

**Total: 23 services + 4 endpoints with no dedicated test file.**

## Coverage gap class #2 — tests exist but the whole file is `pytest.mark.integration`

This is the gap that produced yesterday's incident. `services/user_lists.py` *has* a test file (`tests/services/test_user_lists.py`, 200 stmts), but the file is module-level marked as `pytestmark = pytest.mark.integration` and the default CI run uses `addopts = -m "not integration"` — so **none of its tests execute on PRs**. The same pattern applies to:

| Test file | Source under test | Covered by default CI? |
|---|---|---|
| `tests/services/test_user_lists.py` | `services/user_lists.py` (28.4 %) | **No** — 100 % of cases skipped |
| `tests/api/test_favorite_lists.py` | `api/v1/endpoints/favorite_lists.py` (30.2 %) | **No** |
| `tests/api/test_favorites.py` | (favorites endpoints) | **No** |
| `tests/api/test_verify_endpoint.py` | (verify endpoint) | **No** |
| `tests/api/test_visits.py` | (visits endpoints) | **No** |
| `tests/api/test_broker_dealers.py` | `api/v1/endpoints/broker_dealers.py` (33.0 %) | **No** |
| `tests/api/test_email_extractor_enrich_all.py` | `api/v1/endpoints/email_extractor.py` | **No** |
| `tests/api/test_email_extractor_scans.py` | (extractor scans) | **No** |
| `tests/services/email_extractor/test_aggregator.py` | `services/email_extractor/aggregator.py` (20.3 %) | **No** |

These tests aren't broken — they just need a live Postgres + outbound network. Today they only run if a developer manually invokes `pytest -m integration`. **CI never runs them**, so the safety net they appear to provide is not actually wired in.

This is the highest-leverage gap in the audit: lifting some of these to unit-mockable form (or running an integration job nightly against staging Postgres) would close more risk per hour of work than writing fresh test files for the 23 untested services.

## HIGH-priority gaps ( < 50 % source coverage)

Sorted by absolute missing statements — biggest exposure first.

| Module | Coverage | Missing / Total | Notes |
|---|---|---|---|
| `services/edgar.py` | **0.0 %** | 294 / 294 | SEC EDGAR client. PRD-critical Stream B. |
| `services/finra.py` | 9.4 % | 240 / 265 | FINRA BrokerCheck client. PRD-critical Stream A. |
| `api/v1/endpoints/broker_dealers.py` | 33.0 % | 183 / 273 | Largest untested HTTP handler. Tests exist but are integration-marked. |
| `services/broker_dealers.py` | 41.0 % | 148 / 251 | Service for above. |
| `services/focus_ceo_extraction.py` | 46.5 % | 129 / 241 | CEO extraction from FOCUS reports. |
| `services/filing_monitor.py` | 16.8 % | 119 / 143 | Cron-driven filing alerts (just activated for Tier 2). |
| `services/data_merge.py` | **0.0 %** | 102 / 102 | Tri-stream merge. PRD logic. |
| `api/v1/endpoints/pipeline.py` | 33.8 % | 102 / 154 | Pipeline trigger endpoint. |
| `services/email_extractor/aggregator.py` | 20.3 % | 94 / 118 | Email-extractor aggregation. Tests integration-marked. |
| `services/pdf_text_extractor.py` | 21.7 % | 90 / 115 | PDF → text. |
| `api/v1/endpoints/settings.py` | 38.0 % | 67 / 108 | Scoring-weight admin endpoint. |
| `api/v1/endpoints/favorite_lists.py` | 30.2 % | 67 / 96 | Favorite-list endpoints. Same incident class as `user_lists`. |
| `api/v1/endpoints/email_extractor.py` | 34.4 % | 63 / 96 | HTTP layer for extractor. |
| `services/classification.py` | 25.9 % | 60 / 81 | Self-clearing vs introducing. PRD logic. |
| `services/pipeline.py` | 35.1 % | 61 / 94 | Pipeline orchestrator. |
| `services/email_extractor/apollo_enrichment.py` | 20.8 % | 57 / 72 | Apollo enrichment. |
| `services/user_lists.py` | 28.4 % | 53 / 74 | **The 2026-04-29 P1 module.** Tests exist but integration-marked. |
| `services/export_service.py` | 34.9 % | 41 / 63 | PRD-locked CSV export. |
| `services/normalization.py` | 24.5 % | 40 / 53 | Normalization helpers. |
| `services/pdf_processor.py` | 32.8 % | 39 / 58 | PDF processor. |
| `services/email_extractor/verification_runner.py` | 24.0 % | 38 / 50 | Email verification runner. |
| `services/settings.py` | 25.6 % | 32 / 43 | Settings persistence. |
| `services/llm_parser.py` | 34.1 % | 29 / 44 | LLM provider dispatcher. Review-queue semantics live here — must not regress. |

## MEDIUM-priority gaps (50 – 70 %)

| Module | Coverage | Missing / Total | Notes |
|---|---|---|---|
| `services/pdf_downloader.py` | 58.0 % | 110 / 262 | SSRF allowlist already covered (`test_pdf_downloader.py` 100 %). Remaining gap is download retry / cache paths. |
| `services/focus_reports.py` | 66.2 % | 88 / 260 | FOCUS report ingest. Pipeline-run + multi-year tests cover the happy path. |
| `services/contact_discovery/snov.py` | 56.2 % | 63 / 144 | Snov.io provider. |
| `services/alerts.py` | 50.7 % | 37 / 75 | Alert generation. |
| `api/v1/endpoints/export.py` | 56.2 % | 14 / 32 | PRD-locked export endpoint. |
| `api/v1/endpoints/alerts.py` | 64.1 % | 14 / 39 | Alerts handler. |
| `api/v1/endpoints/stats.py` | 64.9 % | 13 / 37 | Stats handler. |
| `services/competitors.py` | 52.4 % | 10 / 21 | Competitor seeding. |

## Recommendations

Prioritize by **risk per hour of work**, not raw missing-line count.

1. **Unblock the integration-marked files first** (Coverage gap class #2). Either (a) split each file into a unit-only sibling that mocks Postgres + HTTP and runs in default CI, or (b) add a nightly CI job that runs `pytest -m integration` against staging Postgres. Option (a) closes faster; option (b) gives broader coverage. **Doing (a) for `test_user_lists.py` first directly closes the 2026-04-29 incident class.**

2. **Add a smoke test for `services/data_merge.py` (0 % → ≥ 60 %).** PRD-critical merge logic with zero tests is the highest-impact single addition.

3. **Add a smoke test for `services/edgar.py` (0 % → ≥ 50 %).** Mock the SEC submissions endpoint with `respx`, assert latest-X-17A-5 resolution. The pattern is already established in `test_pdf_downloader.py`.

4. **Add negative-case tests for `services/user_lists.py`.** The dropped-table case from the incident, foreign-key violation, and concurrent-rename race. Each missing test should aim to cover at least one realistic failure mode (not just happy path) — yesterday's incident proved that paths without negative-case coverage are where regressions hide.

5. **Enable branch coverage in CI** (`--cov-branch` in `pytest.ini`'s `addopts`). The 65 % line figure is optimistic; branch coverage will be lower and surfaces the untested error branches that tend to cause prod incidents.

6. **Add a coverage floor to CI** (`--cov-fail-under=60` to start, ratchet up). Prevents silent regression as new code is added without tests.

## Followup tasks (each = small PR)

1. **`tests/services/test_user_lists_unit.py`** — unit-only mocks of `pg.Pool` covering the dropped-table 500 from PR #172/#173, FK violations, and concurrent-rename. **(closes the 2026-04-29 incident class)**
2. **`tests/services/test_data_merge.py`** — table-driven cases for FINRA-only, EDGAR-only, both-streams, and conflict resolution.
3. **`tests/services/test_edgar.py`** — `respx` mocks for submissions JSON + latest X-17A-5 resolution + missing-filing fallback.
4. **`tests/services/test_classification.py`** — Self-Clearing vs Introducing decision table from `Documentation/New_Revisions/`.
5. **`tests/services/test_filing_monitor.py`** — covers the cron-driven path that just activated for Tier 2 today (per `reports/tier2-cron-jobs-runbook-2026-04-29.md`).
6. **`tests/services/test_export_service.py`** — guards the 9-column / 100-row / 3-per-day PRD lock so a future refactor can't loosen it silently.
7. **`tests/services/test_normalization.py`** — pure functions, cheap win.
8. **`pytest.ini`** — add `--cov-branch` and a graduated `--cov-fail-under` floor; consider a separate `nightly` job that runs `-m integration` against staging Postgres.

## Confirmed clean (≥ 85 % coverage)

The following modules are well-tested and were not flagged:

`services/scoring.py` (91.6 %) · `services/clearing_classifier.py` (86.4 %) · `services/email_extractor/snov.py` (88.3 %) · `services/email_extractor/hunter.py` (85.6 %) · `services/email_extractor/theharvester.py` (86.5 %) · `services/email_extractor/verification.py` (87.5 %) · `services/contact_discovery/orchestrator.py` (86.8 %) · `services/service_models.py` (91.2 %) · `api/v1/endpoints/auth.py` (85.7 %) · plus every `models/*` and most `schemas/*` (100 % from import-time exercise).

## Out-of-scope notes

- This audit is read-only. No source-code or test-code changes were made. Followup PRs (above) close gaps individually.
- Coverage figures are a snapshot of `origin/develop` at the point cli01 ran (HEAD `262e575` "Update Tier 2 runbook with activation results (#196)"), default `pytest -m "not integration"` selection.
- `coverage.json` artifact left at `backend/coverage.json` (gitignored) for re-inspection.
