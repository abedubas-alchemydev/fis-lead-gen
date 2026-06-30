# Runbook: nightly net-new broker-dealer extraction

How net-new broker-dealers land in the master list automatically every night,
so the team has fresh BDs each morning.

## TL;DR

`scripts/standalone_extract_new_bds.py` **enumerates every active broker-dealer**
from FINRA BrokerCheck and **diffs** that set against the `broker_dealers` table,
ingesting the firms we don't already have. It runs as a **Cloud Run Job** in two
environments — `fis-extract-new-bds-staging` (staging Neon DB) and
`fis-extract-new-bds-prod` (production Neon DB) — each fired once nightly at
**04:00 ET** (`America/New_York`) by its own **Cloud Scheduler** cron. The CI
workflow redeploys each job on that env's deploy (so it always runs the latest
image); the schedulers are **one-time** setups documented below.

- **Schedule:** `0 4 * * *`, time zone `America/New_York` (Eastern; auto-tracks
  EST↔EDT). The client asked for Eastern. To use Central instead, change the
  time zone to `America/Chicago` — see [Change the schedule](#change-the-schedule).
- **Targets:** staging job → `DATABASE_URL_BACKEND_STAGING`; production job →
  `DATABASE_URL_BACKEND`. The two are separate job resources with separate
  schedulers, so neither can write the other's DB.
- **Idempotent / self-limiting:** the diff only surfaces CRDs not already in the
  DB, and the write path is an **upsert** (`upsert_many`), so re-running is safe —
  a second run the same night finds nothing new and just re-hits FINRA for
  nothing.

## Why enumerate-and-diff (and not a CRD probe)

FINRA assigns CRDs to **every** registrant — broker-dealers, investment
advisers, and individuals — from one sequential pool, and there is **no FINRA
"recently-registered" endpoint** to date-query. The previous version of this
script probed CRDs sequentially upward from `MAX(crd_number)` and stopped after
N consecutive "misses". In production it found **nothing**: the CRDs just above
our watermark are almost always IA-only firms or individuals (misses), so the
probe quit ~50 CRDs up and never reached a genuinely new broker-dealer whose CRD
sits hundreds or thousands higher.

Enumerate-and-diff sidesteps the watermark entirely: it lists **all** active
broker-dealers and diffs against the DB, so a net-new BD is found wherever its
CRD lands.

## What the job does

Per the script docstring (`scripts/standalone_extract_new_bds.py`):

1. **Enumerate** all active broker-dealers via
   `FinraService().fetch_broker_dealers()` — the same keyword + A-Z/0-9 Solr
   enumeration `scripts/initial_load.py` uses (paginated, deduped by CRD,
   `active=true`, with 429 / Retry-After backoff). Cheap: one search payload per
   page, no per-firm detail.
2. **Load** existing CRDs: `SELECT crd_number FROM broker_dealers` into a set.
3. **Diff** (the pure, unit-tested `select_new_bds`): keep enumerated records
   whose `crd_number` is truthy and not already in the DB, deduped by CRD.
4. **For the net-new set only** (so the expensive per-firm work stays bounded):
   - `FinraService().enrich_with_detail(new)` — Form BD PDF detail
     (types_of_business, officers, `registration_date` / `formation_date`, …).
   - `EdgarService().fetch_records_for_sec_numbers(...)` for their SEC numbers.
   - `BrokerDealerMergeService().merge(edgar_records, new)` — EDGAR first; the
     same arg-order contract the initial-load pipeline uses.
   - `BrokerDealerRepository().upsert_many(db, merged)` — idempotent upsert.
5. **Dry-run is the default** and is cheap: it enumerates, diffs, and logs
   exactly which firms WOULD be ingested (no PDF fetches, no EDGAR, no writes).
   `--apply` runs the full enrich → EDGAR → merge → upsert path.

Each run logs **enumerated / existing / net_new / inserted** counts. The
freshly-upserted rows feed the "New BDs / 30 days" KPI (`stats.py`,
`new-bds-modal.tsx`) and the master list immediately.

## Architecture

```
 Cloud Scheduler (cron, America/New_York)
   fis-extract-new-bds-nightly       ── POST …/jobs/fis-extract-new-bds-staging:run
   fis-extract-new-bds-prod-nightly  ── POST …/jobs/fis-extract-new-bds-prod:run
        │ (OAuth, runtime SA)
        ▼
 Cloud Run Job  fis-extract-new-bds-{staging,prod}   (backend image, CMD override)
        │  python scripts/standalone_extract_new_bds.py --apply
        ▼
 FINRA BrokerCheck + SEC EDGAR (public)  ──►  Neon DB
   staging job → DATABASE_URL_BACKEND_STAGING
   prod job    → DATABASE_URL_BACKEND
```

The job **reuses the backend image** — the Dockerfile copies repo-root
`scripts/` to `/app/scripts/` (`backend/Dockerfile:36-37`) exactly so jobs can
`python scripts/...`. The only difference from the live backend service is the
container command.

## What's codified vs. one-time

| Piece | Where | Cadence |
|---|---|---|
| Staging job deploy (`fis-extract-new-bds-staging`) | `.github/workflows/test.yml` → "Deploy extract-new-bds Cloud Run Job (staging)", gated `env.ENV == 'staging'` | Re-run on every push to `develop` (staging deploy). |
| Production job deploy (`fis-extract-new-bds-prod`) | `.github/workflows/test.yml` → "Deploy extract-new-bds Cloud Run Job (production)", gated `env.ENV == 'production'` | Re-run on every push to `main` (production deploy). |
| Cloud Scheduler triggers (staging + prod) | **Not in the repo** — created once with the gcloud commands below. Point at the jobs by name, so they survive image redeploys. | One-time. |

## One-time setup — staging

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

## One-time setup — production

Same project/region/SA as staging; only the **job name** and **scheduler name**
differ. The `fis-extract-new-bds-prod` job is created by the next production
deploy (push to `main`) once this PR merges; run the prod `gcloud run jobs
deploy …` block from the workflow step manually if you need it sooner. The prod
job already targets the production DB via `DATABASE_URL_BACKEND` (wired in the
workflow), so the scheduler just needs invoke permission + the cron:

```bash
# 1. Scheduler API is already enabled from the staging setup (no-op).
gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen

# 2. Let the scheduler's identity invoke the PROD job.
gcloud run jobs add-iam-policy-binding fis-extract-new-bds-prod \
  --project=fis-lead-gen \
  --region=us-central1 \
  --member="serviceAccount:136029935063-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"

# 3. Create the prod nightly cron (04:00 Eastern, auto-handles EST/EDT).
gcloud scheduler jobs create http fis-extract-new-bds-prod-nightly \
  --project=fis-lead-gen \
  --location=us-central1 \
  --schedule="0 4 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fis-lead-gen/jobs/fis-extract-new-bds-prod:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com"
```

Verify: `gcloud scheduler jobs describe fis-extract-new-bds-prod-nightly --location=us-central1 --project=fis-lead-gen`.

> Both crons fire at 04:00 ET. Staging and prod hit FINRA's public endpoints
> independently; if you'd rather not double the morning load, stagger them (e.g.
> prod at `0 4 * * *`, staging at `30 4 * * *`).

## Manual operations

Swap `-staging` for `-prod` (and `fis-extract-new-bds-nightly` for
`fis-extract-new-bds-prod-nightly`) to operate the production resources.

```bash
# Run the job now (uses the deployed --apply args — writes to that env's DB).
gcloud run jobs execute fis-extract-new-bds-staging --region=us-central1 --project=fis-lead-gen

# Trigger via the scheduler (tests the whole cron path, not just the job).
gcloud scheduler jobs run fis-extract-new-bds-nightly --location=us-central1 --project=fis-lead-gen

# Watch executions + logs.
gcloud run jobs executions list --job=fis-extract-new-bds-staging --region=us-central1 --project=fis-lead-gen
gcloud run jobs executions logs read <EXECUTION_ID> --region=us-central1 --project=fis-lead-gen
```

### Dry run

The Cloud Run Jobs are deployed with `--apply`, so every scheduled execution
writes. For a **dry run** (enumerate + diff + report, no writes), run the script
locally against the target DB — it is dry-run by default and only `--apply`
writes:

```bash
# Fetch the staging DB URL from Secret Manager (use DATABASE_URL_BACKEND for prod).
DB=$(gcloud secrets versions access latest --secret=DATABASE_URL_BACKEND_STAGING --project=fis-lead-gen)

# Dry run: enumerate, diff, and report exactly what WOULD be ingested (no --apply).
python scripts/standalone_extract_new_bds.py --db-url "$DB"

# Apply: enrich + merge + upsert the net-new firms.
python scripts/standalone_extract_new_bds.py --db-url "$DB" --apply
```

> No backlog "window" to widen anymore — the diff sees the entire active-BD
> universe every run, so a single execution catches up however far behind the
> master list is (bounded only by how many net-new firms it then has to enrich).
> If the DB is *very* far behind (thousands of net-new firms), the per-firm Form
> BD enrichment can outrun the 1-hour task timeout; in that case run the script
> locally once (no timeout) to seed the catch-up, then let the nightly job
> maintain it.

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

## Notes & gotchas

- **No tuning knobs:** the rewrite removed `--max-misses`, `--probe-limit`, and
  `--crd-start` — there's no probe to tune. The only flags are `--apply` and
  `--db-url`.
- **Secrets:** the job wires `DATABASE_URL` (per env) **and** `GEMINI_API_KEY`.
  The Gemini key powers the post-apply Doxie embed hook that indexes the new BDs
  into `chatbot_firm_embedding`; without it the hook logs a skip and the semantic
  index drifts (the extractor's own result is unaffected — the hook is
  best-effort and never changes the exit code).
- **Provenance:** new rows are classified by the real merge — `both` when an
  EDGAR entity matches the firm's SEC number, otherwise `finra_only` — the same
  values the in-tree FINRA merge writes.
- **Staging vs. prod:** separate job resources (`-staging` / `-prod`) with
  separate DB secrets and separate schedulers. Promoting a code change to prod is
  a normal `main` deploy; the prod job is **not** a side effect of a staging run.
```
