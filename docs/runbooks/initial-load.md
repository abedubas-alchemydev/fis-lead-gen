# Runbook: weekly full initial-load re-bootstrap

How the full FINRA + SEC EDGAR re-bootstrap of the `broker_dealers` table runs
automatically — as a **Cloud Run Job**, not the old synchronous HTTP endpoint
that timed out every week.

## TL;DR

`scripts/initial_load.py` performs the **full re-bootstrap**: harvest every
active broker-dealer from FINRA BrokerCheck, enrich with Form BD detail, resolve
SEC/EDGAR records, merge with QA, **destructively replace** the `broker_dealers`
table (`replace_dataset`), classify, run FOCUS import + filing monitor, and
refresh lead scores. It runs as a **Cloud Run Job** in two environments —
`fis-initial-load-staging` and `fis-initial-load-prod` — reusing the backend
image with a CMD override.

- **Why a Job (not the HTTP endpoint):** this was previously triggered by a
  Cloud Scheduler cron hitting `POST /api/v1/pipeline/run/initial-load` on the
  `fis-backend` **service**, synchronously. The 15–30 min harvest can't fit
  inside Cloud Run's 60-minute request ceiling, so every run returned **504**
  (`DEADLINE_EXCEEDED`) and the load **never completed**. A Cloud Run Job has no
  request timeout, so it runs to completion. This mirrors the other four batch
  pipelines (extract-new-bds, refresh-registrations, bd-gap-fill,
  bank-charter-watch).
- **Schedule:** `0 2 * * 6` (Saturday 02:00 `America/New_York`), preserving the
  old `initial-load-weekly` cadence — but the new cron
  (`fis-initial-load-prod-weekly`) triggers the **Job**, not the endpoint.
- **DESTRUCTIVE:** the prod run TRUNCATEs and reloads the entire
  `broker_dealers` table each time (`repository.replace_dataset`,
  `scripts/initial_load.py:134`). It is guarded by a **≥500-row safety floor**
  (`minimum_initial_load_records`) plus edgar-only / duplicate invariants that
  **abort** the load before any write if the harvest looks wrong. Treat every run
  as a full rebuild, not an incremental update.
- **Manual-first:** the prod cron ships **paused**. Validate on staging, run the
  prod Job manually once, confirm row counts, then resume the cron.

## What the job does

Per `scripts/initial_load.py` (env-driven; **no CLI flags** — behaviour comes
from `settings.*`, and `initial_load_limit=None` means a **full** harvest):

1. **Harvest** active BDs — `FinraService().fetch_broker_dealers(limit=settings.initial_load_limit)`.
2. **Enrich** with Form BD detail (business types, owners, officers) — `enrich_with_detail`.
3. **Resolve** SEC/EDGAR records for each firm's SEC file number — `EdgarService().fetch_records_for_sec_numbers(...)`.
4. **Merge** FINRA + EDGAR with a QA report — `BrokerDealerMergeService().merge(...)`.
5. **Validate** — abort if the verified set is `< minimum_initial_load_records` (default 500), or if any edgar-only / duplicate invariant is violated.
6. **Replace** the dataset — `repository.replace_dataset(db, merged)` (destructive full reload), then apply classification.
7. **Optional passes** (config-gated): FOCUS financial metrics import (`run_focus_import_on_initial_load=True`), clearing pipeline (`run_clearing_pipeline_on_initial_load=False`), filing monitor (`run_filing_monitor_on_initial_load=True`).
8. **Refresh** lead scores. Logs finish with `INITIAL LOAD COMPLETE`.

## Architecture

```
 Cloud Scheduler (cron, America/New_York)
   fis-initial-load-prod-weekly  ── POST …/jobs/fis-initial-load-prod:run
        │ (OAuth, runtime SA)
        ▼
 Cloud Run Job  fis-initial-load-{staging,prod}   (backend image, CMD override)
        │  python scripts/initial_load.py
        ▼
 FINRA BrokerCheck + SEC EDGAR (public)  ──►  Neon DB  (destructive replace)
   staging job → DATABASE_URL_BACKEND_STAGING
   prod job    → DATABASE_URL_BACKEND
```

The job **reuses the backend image** — the Dockerfile copies repo-root `scripts/`
to `/app/scripts/` (`backend/Dockerfile:36-37`) so jobs can `python scripts/...`.
Only the container command differs from the live backend service.

## What's codified vs. one-time

| Piece | Where | Cadence |
|---|---|---|
| Staging job deploy (`fis-initial-load-staging`) | `.github/workflows/test.yml` → "Deploy initial-load Cloud Run Job (staging)", gated `env.ENV == 'staging'` | Re-run on every push to `develop`. |
| Production job deploy (`fis-initial-load-prod`) | `.github/workflows/test.yml` → "Deploy initial-load Cloud Run Job (production)", gated `env.ENV == 'production'` | Re-run on every push to `main`. |
| Cloud Scheduler trigger (prod) | **Not in the repo** — created once with the gcloud commands below. Points at the job by name, so it survives image redeploys. | One-time. |

Constants: project `fis-lead-gen`, region `us-central1`, runtime service account
`136029935063-compute@developer.gserviceaccount.com`.

## One-time setup — staging (validate here first)

The `fis-initial-load-staging` job is created by the next staging deploy after
this PR merges to `develop`. Validate it before touching prod:

```bash
# Run the job now against the staging DB (destructive replace of staging data).
gcloud run jobs execute fis-initial-load-staging --region=us-central1 --project=fis-lead-gen --wait

# Confirm it finished and check the logs end with "INITIAL LOAD COMPLETE".
gcloud run jobs executions list --job=fis-initial-load-staging --region=us-central1 --project=fis-lead-gen --limit=1
```

If the execution fails with exit code **137**, it OOM'd — bump `--memory` to
`8Gi` in the workflow step and redeploy.

## One-time setup — production (manual-first)

The `fis-initial-load-prod` job is created by the next production deploy (push to
`main`). Then wire the trigger, but keep it **paused** until a manual run proves
out:

```bash
# 1. Let the scheduler's identity invoke the PROD job.
gcloud run jobs add-iam-policy-binding fis-initial-load-prod \
  --project=fis-lead-gen --region=us-central1 \
  --member="serviceAccount:136029935063-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"

# 2. Pause the OLD broken HTTP-endpoint cron so it stops 504-ing.
gcloud scheduler jobs pause initial-load-weekly --location=us-central1 --project=fis-lead-gen

# 3. Create the new job-triggering weekly cron, then PAUSE it (manual-first).
gcloud scheduler jobs create http fis-initial-load-prod-weekly \
  --project=fis-lead-gen --location=us-central1 \
  --schedule="0 2 * * 6" --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fis-lead-gen/jobs/fis-initial-load-prod:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com"
gcloud scheduler jobs pause fis-initial-load-prod-weekly --location=us-central1 --project=fis-lead-gen

# 4. Validate: run the PROD job manually once and confirm sane row counts.
gcloud run jobs execute fis-initial-load-prod --region=us-central1 --project=fis-lead-gen --wait
gcloud run jobs executions list --job=fis-initial-load-prod --region=us-central1 --project=fis-lead-gen --limit=1

# 5. Once verified, ENABLE the weekly cron.
gcloud scheduler jobs resume fis-initial-load-prod-weekly --location=us-central1 --project=fis-lead-gen

# 6. Optional: delete the old cron once the new one is proven.
gcloud scheduler jobs delete initial-load-weekly --location=us-central1 --project=fis-lead-gen
```

Verify: `gcloud scheduler jobs describe fis-initial-load-prod-weekly --location=us-central1 --project=fis-lead-gen --format="value(state,schedule,httpTarget.uri)"`.

## Manual operations

```bash
# Run the job now (writes to that env's DB — destructive full replace).
gcloud run jobs execute fis-initial-load-prod --region=us-central1 --project=fis-lead-gen --wait

# Trigger via the scheduler (tests the whole cron path).
gcloud scheduler jobs run fis-initial-load-prod-weekly --location=us-central1 --project=fis-lead-gen

# Watch executions + logs.
gcloud run jobs executions list --job=fis-initial-load-prod --region=us-central1 --project=fis-lead-gen
gcloud run jobs executions logs read <EXECUTION_ID> --region=us-central1 --project=fis-lead-gen

# Pause / resume the weekly cron without deleting.
gcloud scheduler jobs pause  fis-initial-load-prod-weekly --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs resume fis-initial-load-prod-weekly --location=us-central1 --project=fis-lead-gen
```

## Notes & gotchas

- **Destructive by design.** `replace_dataset` TRUNCATEs + reloads the whole
  table. The ≥500-row floor + invariants abort before any write if the harvest
  is short/malformed, so a bad FINRA/EDGAR day can't wipe the table to empty —
  it fails the run instead. Manual edits or non-FINRA/EDGAR enrichment on
  `broker_dealers` do **not** survive a run.
- **Memory.** The script does a single-pass in-memory merge that peaked ~2.1 GiB
  in prod (it OOM-killed the old 2 GiB HTTP instance). The Job runs at **4 GiB**;
  bump to 8 GiB if you see exit 137. (The memory-safe *chunked* merge currently
  lives only in the HTTP handler `_run_initial_load_background`; porting it into
  the script is a future hardening.)
- **Secrets.** Only `DATABASE_URL` (per env). `GEMINI_API_KEY` is **not** wired —
  add it only if you turn on `run_clearing_pipeline_on_initial_load` (clearing
  PDF extraction). FOCUS import + filing monitor use free FINRA/SEC endpoints.
- **The HTTP endpoint still exists.** `POST /api/v1/pipeline/run/initial-load`
  remains for manual admin triggering (admin cookie auth), but it is no longer
  scheduler-driven, so it won't 504 in production. Future option: have it enqueue
  the Job and return `202`, or deprecate it.
- **Staging vs. prod:** separate job resources with separate DB secrets and a
  single prod scheduler. Promoting a code change to prod is a normal `main`
  deploy; the prod job is not a side effect of a staging run.
```
