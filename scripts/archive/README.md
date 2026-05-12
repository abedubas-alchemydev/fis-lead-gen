# Archived scripts

These scripts are kept here for git-history visibility but are no longer
recommended for new use. Each was superseded by `scripts/gap_fill_broker_dealers.py`,
which runs the same work per-firm via the `run_refresh_all` orchestrator
(`backend/app/services/refresh_all_orchestrator.py`).

| File | Original purpose | What replaces it |
|---|---|---|
| `backfill_master_list_top.py` | Refresh-all over the top-N master-list rows (filled grid columns minus contacts). | `gap_fill_broker_dealers.py` runs the same orchestrator over **every** broker-dealer in priority order, with cooldown + auto-rescore. |
| `load_financials.py` | Standalone wrapper around `FocusReportService.load_financial_metrics_for_broker_dealer`. | `gap_fill_broker_dealers.py` → `SUB_REFRESH_FINANCIALS` sub-pipeline calls the same service per-firm. |
| `run_focus_ceo_extraction.py` | Batch FOCUS Report + CEO extraction (X-17A-5 → Gemini). | `gap_fill_broker_dealers.py` → `SUB_REFRESH_FINANCIALS` covers the FOCUS extraction; `SUB_ENRICH` (Apollo) covers contact discovery. |

If you need to re-run any of these (e.g., the orchestrator-based path
fails on a specific batch and you want the legacy code path as a
fallback), they remain importable from `scripts.archive`. Don't add new
features to them — extend the orchestrator's sub-pipelines instead.

Active alternatives (not archived):
- `gap_fill_broker_dealers.py` — primary per-row gap-fill runner.
- `inspect_broker_dealer_gaps.py` — read-only scope report.
- `backfill_websites.py` — website-targeted resolver chain (still
  actively used; the gap-fill bulk runner excludes website by policy).
- `run_clearing_pipeline.py` — bulk clearing extraction (still useful
  for one-off clearing-only re-runs against the standalone path).
- `run_scoring.py` — standalone re-score (gap-fill auto-scores at end,
  but you can run this any time after weight tweaks).
