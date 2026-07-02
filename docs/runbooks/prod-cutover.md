# Runbook: production automation cutover

Cut the paid nightly automation **over to production** and **off on staging**, so
prod gets fresh broker-dealers + registration refreshes + profile gap-fill every
night and we don't double-spend running the same paid jobs on staging.

> **STOP — Jarvis gates all prod changes.** Every command in this runbook is a
> production mutation (creates prod schedulers, pauses staging schedulers). **None
> of it has been run.** Do not execute anything here until the prod cutover is
> explicitly greenlit. Run the commands top-to-bottom, verifying at each gate.
> All commands are **dry-safe to read** — they only take effect when you run them.

## What this cutover does

| | Before | After |
|---|---|---|
| **Prod nightly jobs** | only `fis-extract-new-bds-prod` job exists (no scheduler) | 3 prod jobs, each on a nightly cron |
| **Staging paid crons** | 4 paid staging schedulers ENABLED (double-spend once prod is on) | PAUSED |

Prod schedulers created (all `America/New_York`, pointing at the `-prod` Cloud
Run **jobs** via the `run.googleapis.com` `:run` httpTarget — same shape as the
existing `fis-extract-new-bds-nightly`):

| Scheduler | Cron | Target job |
|---|---|---|
| `fis-extract-new-bds-prod-nightly` | `0 4 * * *` | `fis-extract-new-bds-prod` |
| `fis-refresh-registrations-prod-nightly` | `0 5 * * *` | `fis-refresh-registrations-prod` |
| `fis-bd-gap-fill-prod-nightly` | `0 6 * * *` | `fis-bd-gap-fill-prod` |

Staging paid schedulers paused (they trigger the paid staging batch **jobs**):

| Scheduler | Cron | Currently targets |
|---|---|---|
| `fis-extract-new-bds-nightly` | `0 4 * * *` | `fis-extract-new-bds-staging:run` |
| `fis-refresh-registrations-nightly` | `0 5 * * *` | `fis-refresh-registrations-staging:run` |
| `fis-ia-other-names-backfill-nightly` | `0 20 * * *` | `fis-ia-other-names-backfill-staging:run` |
| `fis-advisor-gap-fill-nightly-staging` | `0 2 * * *` | `fis-advisor-gap-fill-staging:run` |

The cadence deliberately staggers prod: extract new BDs at 04:00, refresh
registrations at 05:00, then gap-fill profiles at 06:00 — so BDs ingested /
newly-approved overnight are enriched the same morning.

## Constants

```
Project:  fis-lead-gen
Region:   us-central1
Runtime SA (scheduler OAuth identity + job runtime):
          136029935063-compute@developer.gserviceaccount.com
Timezone: America/New_York   (Eastern; auto-tracks EST↔EDT)
```

## Preconditions (do these first, in order)

1. **The CI PR is merged and prod is deployed.** The three `-prod` job resources
   are created/updated by the production deploy step in
   `.github/workflows/test.yml` (gated `env.ENV == 'production'`, i.e. a push to
   `main`). `fis-refresh-registrations-prod` and `fis-bd-gap-fill-prod` are added
   by the prod-cutover PR; `fis-extract-new-bds-prod` already exists. **Merge that
   PR and let a `main` deploy run before creating any scheduler below.**

2. **Confirm all three prod jobs exist** (a scheduler pointing at a missing job
   fails silently at fire time):

   ```bash
   for J in fis-extract-new-bds-prod fis-refresh-registrations-prod fis-bd-gap-fill-prod; do
     echo "== $J =="
     gcloud run jobs describe "$J" --region=us-central1 --project=fis-lead-gen \
       --format="value(metadata.name)" || echo "  MISSING — deploy prod first"
   done
   ```

   If a job is MISSING, re-run the prod deploy (push to `main`) or run that job's
   `gcloud run jobs deploy …` block from `.github/workflows/test.yml` manually.

3. **(Recommended) Size the prod gap-fill before enabling its nightly cron.**
   `fis-bd-gap-fill-prod` spends paid budget (Apollo / SerpAPI / Gemini /
   Hunter/Snov) against the **production** book. Do a read-only cost preview first
   — this temporarily overrides the deployed `--apply` args with `--scan-only`
   (no writes, **no API calls**), then restores them:

   ```bash
   # Preview only — no writes, no paid calls.
   # NOTE: `jobs update` (not `deploy`) — deploy requires --image; update
   # patches args in place on the existing job (verified 2026-07-02).
   gcloud run jobs update fis-bd-gap-fill-prod --region=us-central1 --project=fis-lead-gen \
     --args=scripts/gap_fill_broker_dealers.py,--scan-only
   gcloud run jobs execute fis-bd-gap-fill-prod --region=us-central1 --project=fis-lead-gen --wait
   # Read the scan summary in the logs, then RESTORE the applied args:
   gcloud run jobs update fis-bd-gap-fill-prod --region=us-central1 --project=fis-lead-gen \
     --args=scripts/gap_fill_broker_dealers.py,--apply,--newest-first,--limit,300
   ```

   (`fis-extract-new-bds-prod` and `fis-refresh-registrations-prod` are FINRA-only
   / free, so no sizing pass is needed for those.)

---

## Section A — Create the prod schedulers

Each job gets (1) an explicit `run.invoker` binding for the scheduler's OAuth
identity (idempotent — a no-op if already bound) and (2) an HTTP scheduler that
POSTs the job's `:run` endpoint. This mirrors exactly how
`fis-extract-new-bds-nightly` is built (verified with `gcloud scheduler jobs
describe`: `httpMethod: POST`, `oauthToken.serviceAccountEmail` = the compute SA,
`uri` = the `…:run` endpoint; attempt deadline + retry config are left at gcloud
defaults, matching the existing job).

```bash
# Scheduler API (no-op if already enabled).
gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen
```

### A1 — extract-new-bds (04:00 ET)

```bash
gcloud run jobs add-iam-policy-binding fis-extract-new-bds-prod \
  --project=fis-lead-gen --region=us-central1 \
  --member="serviceAccount:136029935063-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http fis-extract-new-bds-prod-nightly \
  --project=fis-lead-gen \
  --location=us-central1 \
  --schedule="0 4 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fis-lead-gen/jobs/fis-extract-new-bds-prod:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com"
```

### A2 — refresh-registrations (05:00 ET)

```bash
gcloud run jobs add-iam-policy-binding fis-refresh-registrations-prod \
  --project=fis-lead-gen --region=us-central1 \
  --member="serviceAccount:136029935063-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http fis-refresh-registrations-prod-nightly \
  --project=fis-lead-gen \
  --location=us-central1 \
  --schedule="0 5 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fis-lead-gen/jobs/fis-refresh-registrations-prod:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com"
```

### A3 — bd-gap-fill (06:00 ET)

> Enabling this cron starts **paid enrichment against the production book**. Do
> the `--scan-only` sizing pass in Precondition 3 first if you haven't.

```bash
gcloud run jobs add-iam-policy-binding fis-bd-gap-fill-prod \
  --project=fis-lead-gen --region=us-central1 \
  --member="serviceAccount:136029935063-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http fis-bd-gap-fill-prod-nightly \
  --project=fis-lead-gen \
  --location=us-central1 \
  --schedule="0 6 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fis-lead-gen/jobs/fis-bd-gap-fill-prod:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com"
```

---

## Section B — Pause the staging / paid schedulers

These four schedulers each trigger a **paid staging batch job**. Once the prod
crons in Section A are live, leaving these enabled runs the same paid work twice
(staging + prod) every night. Pause them to stop the double-spend.

First, **confirm what each one currently targets** before you pause it (so it's
unambiguous which resource you're stopping):

```bash
for S in fis-extract-new-bds-nightly \
         fis-refresh-registrations-nightly \
         fis-ia-other-names-backfill-nightly \
         fis-advisor-gap-fill-nightly-staging; do
  echo "== $S =="
  gcloud scheduler jobs describe "$S" --location=us-central1 --project=fis-lead-gen \
    --format="value(state, schedule, httpTarget.uri)"
done
```

Expected (each must show a `…-staging:run` target and `ENABLED` before you pause):

```
fis-extract-new-bds-nightly            ENABLED  0 4 * * *   …/jobs/fis-extract-new-bds-staging:run
fis-refresh-registrations-nightly      ENABLED  0 5 * * *   …/jobs/fis-refresh-registrations-staging:run
fis-ia-other-names-backfill-nightly    ENABLED  0 20 * * *  …/jobs/fis-ia-other-names-backfill-staging:run
fis-advisor-gap-fill-nightly-staging   ENABLED  0 2 * * *   …/jobs/fis-advisor-gap-fill-staging:run
```

Then pause all four (pause, not delete — trivially reversible, see Rollback):

```bash
gcloud scheduler jobs pause fis-extract-new-bds-nightly           --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs pause fis-refresh-registrations-nightly     --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs pause fis-ia-other-names-backfill-nightly   --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs pause fis-advisor-gap-fill-nightly-staging  --location=us-central1 --project=fis-lead-gen
```

### Explicitly NOT touched (informational — no action)

These are out of scope for this cutover; do **not** pause them here:

- `populate-all-weekly`, `form4-watcher-daily`, `filing-monitor-hourly` — target
  the **prod backend service HTTP endpoints** (`/api/v1/pipeline/run/…`), not
  paid Cloud Run jobs. Free internal pipeline triggers; leave running.
- `initial-load-daily` — already `PAUSED`.
- `*-staging` backend-endpoint crons (`form4-watcher-daily-staging`,
  `registration-monitor-daily-staging`, `filing-monitor-hourly-staging`,
  `initial-load-daily-staging`) — hit the staging backend service, not paid batch
  jobs. Leave for staging QA (pausing them is a separate decision, not part of the
  paid-automation cutover).

---

## Section C — Verify the cutover

```bash
# 1. All three prod schedulers exist, ENABLED, correct cron + target.
for S in fis-extract-new-bds-prod-nightly \
         fis-refresh-registrations-prod-nightly \
         fis-bd-gap-fill-prod-nightly; do
  echo "== $S =="
  gcloud scheduler jobs describe "$S" --location=us-central1 --project=fis-lead-gen \
    --format="value(state, schedule, timeZone, httpTarget.uri)"
done
# Expect: ENABLED · (0 4|0 5|0 6 * * *) · America/New_York · …/jobs/fis-*-prod:run

# 2. All four staging paid schedulers are PAUSED.
for S in fis-extract-new-bds-nightly \
         fis-refresh-registrations-nightly \
         fis-ia-other-names-backfill-nightly \
         fis-advisor-gap-fill-nightly-staging; do
  echo "== $S =="
  gcloud scheduler jobs describe "$S" --location=us-central1 --project=fis-lead-gen \
    --format="value(state)"
done
# Expect: PAUSED (x4)

# 3. Full picture — everything at a glance.
gcloud scheduler jobs list --location=us-central1 --project=fis-lead-gen \
  --format="table(name.basename(), schedule, state, httpTarget.uri)"
```

**(Optional) Prove the prod cron path end-to-end** without waiting for the
overnight fire. Start with a FINRA-only (free) job:

```bash
gcloud scheduler jobs run fis-extract-new-bds-prod-nightly --location=us-central1 --project=fis-lead-gen
gcloud run jobs executions list --job=fis-extract-new-bds-prod --region=us-central1 --project=fis-lead-gen --limit=1
```

---

## Rollback

Fully reversible — no resource is deleted.

```bash
# Resume the staging paid schedulers (undo Section B).
gcloud scheduler jobs resume fis-extract-new-bds-nightly           --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs resume fis-refresh-registrations-nightly     --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs resume fis-ia-other-names-backfill-nightly   --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs resume fis-advisor-gap-fill-nightly-staging  --location=us-central1 --project=fis-lead-gen

# Stop the prod crons (undo Section A) — pause first; delete only if abandoning.
gcloud scheduler jobs pause fis-extract-new-bds-prod-nightly       --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs pause fis-refresh-registrations-prod-nightly --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs pause fis-bd-gap-fill-prod-nightly           --location=us-central1 --project=fis-lead-gen
# gcloud scheduler jobs delete <name> --location=us-central1 --project=fis-lead-gen   # only to remove entirely
```

To pause a single prod job that misbehaves (e.g. gap-fill cost) without touching
the others, `pause` just that one scheduler — the prod `-prod` job resources stay
in place and inert (redeployed on each `main` deploy), so resuming later needs no
rebuild.

## Related runbooks

- `docs/runbooks/extract-new-bds.md` — the extract-new-bds job internals + its own
  staging/prod scheduler notes.
- `docs/runbooks/refresh-registrations` behavior is documented inline in the
  extract-new-bds runbook and the CI step comments.
- `docs/runbooks/bd-gap-fill.md` — the BD gap-fill job internals, cost model, and
  the `--newest-first` ordering caveat.
</content>
</invoke>
