# Runbook: nightly net-new broker-dealer extraction

How net-new broker-dealers land in the master list automatically every night,
so the team has fresh BDs each morning.

## TL;DR

`scripts/standalone_extract_new_bds.py` probes FINRA's public BrokerCheck
endpoints starting at `MAX(crd_number) + 1` and INSERTs each new BD it finds.
It runs as a **Cloud Run Job** (`fis-extract-new-bds-staging`) that a **Cloud
Scheduler** cron fires once nightly at **04:00 ET** (`America/New_York`). The
CI workflow redeploys the job on every staging deploy (so it always runs the
latest image); the scheduler is a **one-time** setup documented below.

- **Schedule:** `0 4 * * *`, time zone `America/New_York` (Eastern; auto-tracks
  EST↔EDT). The client asked for Eastern. If the team is in Houston and you
  actually want Central, change the time zone to `America/Chicago` — see
  [Change the schedule](#change-the-schedule).
- **Target:** staging Neon DB only (`DATABASE_URL_BACKEND_STAGING`). There is
  no prod job resource by design — a push to `main` cannot run this on prod.
- **Idempotent / self-limiting:** probes from `MAX(crd_number)+1`, dedups
  against existing rows, and stops after `--max-misses` consecutive non-BD
  CRDs. One run per night catches up the gap; running more often just re-hits
  FINRA for nothing.

## What the job does

Per the script docstring (`scripts/standalone_extract_new_bds.py`):

1. Reads `MAX(CAST(crd_number AS INTEGER))` from `broker_dealers` and probes
   upward from there (FINRA assigns CRDs sequentially).
2. For each CRD: `GET api.brokercheck.finra.org/search/firm/{crd}` → skip if no
   hit or no `bcScope` (IA-only / terminated / never approved).
3. On a hit: pulls `firmName`, status, SEC #, city/state; fetches the Form BD
   PDF from `files.brokercheck.finra.org` and regexes out `registration_date`
   and `formation_date` using the same patterns as
   `backend/app/services/brokercheck_pdf.py`.
4. INSERTs a minimal row (`matched_source = 'finra_only'`). Everything else
   (owners, officers, website, clearing, lead_score, financials, …) stays at
   column defaults and gets filled later by the per-firm refresh-all path.
5. Stops after `--max-misses` consecutive misses (default 50) or `--probe-limit`
   total CRDs (default 500).

The freshly-inserted rows feed the "New BDs / 30 days" KPI (`stats.py`,
`new-bds-modal.tsx`) and the master list immediately.

## Architecture

```
 Cloud Scheduler (cron, America/New_York)
   fis-extract-new-bds-nightly  ── POST …/jobs/fis-extract-new-bds-staging:run
        │ (OAuth, runtime SA)
        ▼
 Cloud Run Job  fis-extract-new-bds-staging   (backend image, CMD override)
        │  python scripts/standalone_extract_new_bds.py --apply
        ▼
 FINRA BrokerCheck (public)  ──►  staging Neon DB  (DATABASE_URL_BACKEND_STAGING)
```

The job **reuses the backend image** — the Dockerfile copies repo-root
`scripts/` to `/app/scripts/` (`backend/Dockerfile:36-37`) exactly so jobs can
`python scripts/...`. The only difference from the live backend service is the
container command.

## What's codified vs. one-time

| Piece | Where | Cadence |
|---|---|---|
| Job deploy (`fis-extract-new-bds-staging`) | `.github/workflows/test.yml` → "Deploy extract-new-bds Cloud Run Job (staging)" | Re-run on every push to `develop` (staging deploy), so the job always uses the latest image. |
| Cloud Scheduler trigger | **Not in the repo** — created once with the gcloud command below. Points at the job by name, so it survives image redeploys. | One-time. |

## One-time setup

Constants: project `fis-lead-gen`, region `us-central1`, runtime service
account `136029935063-compute@developer.gserviceaccount.com`.

The job itself is created by the next staging deploy after this PR merges. If
you want it to exist *before* then, run the same `gcloud run jobs deploy …`
block from the workflow step manually.

```bash
# 1. Enable the Scheduler API (no-op if already enabled).
gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen

# 2. Let the scheduler's identity invoke the job.
gcloud run jobs add-iam-policy-binding fis-extract-new-bds-staging \
  --project=fis-lead-gen \
  --region=us-central1 \
  --member="serviceAccount:136029935063-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"

# 3. Create the nightly cron (04:00 Eastern, auto-handles EST/EDT).
gcloud scheduler jobs create http fis-extract-new-bds-nightly \
  --project=fis-lead-gen \
  --location=us-central1 \
  --schedule="0 4 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fis-lead-gen/jobs/fis-extract-new-bds-staging:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com"
```

Verify: `gcloud scheduler jobs describe fis-extract-new-bds-nightly --location=us-central1 --project=fis-lead-gen`.

## Manual operations

```bash
# Run the job now (uses the deployed --apply args — writes to the staging DB).
gcloud run jobs execute fis-extract-new-bds-staging --region=us-central1 --project=fis-lead-gen

# Trigger via the scheduler (tests the whole cron path, not just the job).
gcloud scheduler jobs run fis-extract-new-bds-nightly --location=us-central1 --project=fis-lead-gen

# Watch executions + logs.
gcloud run jobs executions list --job=fis-extract-new-bds-staging --region=us-central1 --project=fis-lead-gen
gcloud run jobs executions logs read <EXECUTION_ID> --region=us-central1 --project=fis-lead-gen
```

### Dry run / first-run backlog catch-up

The Cloud Run Job is deployed with `--apply`, so every execution writes. For a
**dry run** (probe + report, no writes) or a **large one-time backlog
catch-up** with custom flags, run the script locally against the staging DB —
the script is dry-run by default and only `--apply` writes:

```bash
# Fetch the staging DB URL from Secret Manager.
DB=$(gcloud secrets versions access latest --secret=DATABASE_URL_BACKEND_STAGING --project=fis-lead-gen)

# Dry run: probe and report what WOULD be inserted (no --apply).
python scripts/standalone_extract_new_bds.py --db-url "$DB"

# Backlog catch-up: widen the probe window, then write.
python scripts/standalone_extract_new_bds.py --db-url "$DB" --apply --probe-limit 2000 --max-misses 100

# Smoke-test a single known CRD.
python scripts/standalone_extract_new_bds.py --db-url "$DB" --crd-start 339697 --probe-limit 1
```

> If the master list is far behind FINRA's current CRD frontier, the nightly
> job (default `--probe-limit 500`) catches up ~500 CRDs per night. To clear a
> big backlog in one shot, use the widened local run above once, then let the
> nightly job maintain it.

## Change the schedule

```bash
# Switch to Central (Houston) time, or any cron expression:
gcloud scheduler jobs update http fis-extract-new-bds-nightly \
  --location=us-central1 --project=fis-lead-gen \
  --schedule="0 4 * * *" --time-zone="America/Chicago"

# Pause / resume without deleting:
gcloud scheduler jobs pause  fis-extract-new-bds-nightly --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs resume fis-extract-new-bds-nightly --location=us-central1 --project=fis-lead-gen
```

Hourly across an 8 PM–5 AM window (if you ever want maximal freshness over a
single nightly run) would be `--schedule="0 20-23,0-5 * * *"` — but note every
run after the first each night usually finds nothing new and just re-hits
FINRA, which is why the nightly single run is the default.

## Notes & gotchas

- **Tuning knobs:** `--max-misses` (stop after N consecutive non-BD CRDs,
  default 50), `--probe-limit` (hard cap per run, default 500), `--crd-start`
  (override the probe start). Bump `--max-misses` if FINRA has a long stretch
  of IA-only CRDs at the frontier that stops the probe early.
- **No extra secrets:** the script talks only to FINRA's public endpoints + the
  DB, so the job wires `DATABASE_URL` and nothing else (unlike the gap-fill /
  backfill jobs, which need Gemini/Apollo/etc.).
- **Provenance:** new rows get `matched_source = 'finra_only'` (no EDGAR match
  at insert time) — the same value the in-tree FINRA merge writes.
- **Staging-only:** there is no `fis-extract-new-bds-prod` job; promoting to
  prod is a deliberate, separately-named step, not a side effect of merging to
  `main`.
