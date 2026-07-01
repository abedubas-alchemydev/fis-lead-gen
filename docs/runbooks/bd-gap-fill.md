# Runbook: nightly broker-dealer profile gap-fill

How newly-ingested broker-dealers get their PROFILE enriched (website,
financials, clearing, contacts, FOCUS filing-contact, …) automatically every
morning, right after they land in the master list.

## TL;DR

`scripts/gap_fill_broker_dealers.py` walks broker-dealers whose profile fields
are still NULL / sentinel and fires the per-firm `run_refresh_all` orchestrator
(FINRA financials + health + clearing + FOCUS, SerpAPI/Apollo website
resolution, Apollo contact enrichment, Hunter/Snov emails) to fill them,
stamping a **30-day cooldown** (`last_gap_fill_attempt_at`) per row so reruns
auto-resume. It runs as a **Cloud Run Job** — `fis-bd-gap-fill-staging` (staging
Neon DB) — fired once nightly at **06:00 ET** (`America/New_York`) by a **Cloud
Scheduler** cron. The CI workflow redeploys the job on every staging deploy (so
it always runs the latest image); the scheduler is a **one-time** setup
documented below. The **production** sibling job (`fis-bd-gap-fill-prod`) is now
wired in CI; its nightly scheduler is created as part of the production
automation cutover — see [`docs/runbooks/prod-cutover.md`](./prod-cutover.md) and
[One-time setup — production](#one-time-setup--production).

- **Schedule:** `0 6 * * *`, time zone `America/New_York`. Deliberately **after**
  `fis-extract-new-bds-*` (04:00) and `fis-refresh-registrations-*` (05:00), so
  the BDs those two jobs ingest / newly-approve overnight are enriched the same
  morning.
- **Target:** staging job → `DATABASE_URL_BACKEND_STAGING`; production job →
  `DATABASE_URL_BACKEND` (wired in CI; nightly scheduler created at the cutover).
- **Bounded cost:** `--limit 300` caps each run to the first 300 eligible BDs.
  This spends paid-API budget (Apollo, SerpAPI, Gemini, Hunter/Snov) — see
  [Cost & guardrails](#cost--guardrails).

## Why `--newest-first` is required here (the ordering caveat)

The gap-fill's **default** order is highest-value first: priority bucket
(`hot` → `warm` → `cold`) → `lead_score` desc → `id`. That's right for a
value-first pass, but it is exactly **wrong** for "enrich last night's new BDs":

> A freshly-ingested BD has no website / financials / contacts yet, so it scores
> **`cold`** — the *lowest* bucket. Under the default order it sorts **behind the
> entire hot + warm + cold backlog**, and a bounded `--limit` never reaches it.

Measured on staging (2026-07-01): all 278 BDs ingested that day were `cold`
(score 7.5–22.5) and occupied ordering positions **1888–3060 of 3071 eligible**.
`--limit 300` would have enriched the top-300 (all `hot`) firms and **zero** of
the new BDs.

The nightly job therefore passes **`--newest-first`**, which orders
**never-gap-filled + most-recently-created first**
(`last_gap_fill_attempt_at ASC NULLS FIRST, created_at DESC, id DESC`). New BDs
(NULL cooldown stamp, newest `created_at`) sort to the very front, so a bounded
`--limit` always clears the fresh arrivals before working the cooldown-expired
tail. Manual **value-first** passes should omit the flag.

## What the job does

Per the script docstring (`scripts/gap_fill_broker_dealers.py`):

1. **Phase 1 — read-only scan:** walk every eligible BD (cooldown expired,
   never attempted, or a retryable transient clearing failure), tally per-column
   gaps by priority bucket, and print a summary + estimated sub-pipeline fires.
   `--scan-only` stops here (no mutations, **no API calls** — safe cost preview).
2. **Phase 2 — fill:** for each BD, `decide_pipelines` gates which of the seven
   sub-pipelines have work (financials, health-check, clearing, contacts,
   filings, website, FOCUS-contact), then `run_refresh_all` fires them
   concurrently. DB commits land row-by-row; the per-BD cooldown stamp makes the
   next invocation auto-resume. `--apply` skips the interactive confirm (for
   cron).
3. **Re-score:** after the pass, `score_broker_dealers` recomputes every BD's
   `lead_score` / `lead_priority` from the newly-filled data (free, idempotent).

`--limit N` caps the eligible walk to the first N; `--reset-cooldown` ignores
the 30-day stamp; `--strict` uses IS-NULL-only predicates (no sentinel widening).

## Architecture

```
 Cloud Scheduler (cron, America/New_York, 06:00)
   fis-bd-gap-fill-nightly-staging ── POST …/jobs/fis-bd-gap-fill-staging:run
   fis-bd-gap-fill-prod-nightly    ── POST …/jobs/fis-bd-gap-fill-prod:run  (created at cutover)
        │ (OAuth, runtime SA)
        ▼
 Cloud Run Job  fis-bd-gap-fill-staging   (backend image, CMD override)
        │  python scripts/gap_fill_broker_dealers.py --apply --newest-first --limit 300
        ▼
 FINRA + SEC EDGAR (free) · Apollo · SerpAPI · Gemini · Hunter/Snov  ──►  Neon DB
   staging job → DATABASE_URL_BACKEND_STAGING
   prod job    → DATABASE_URL_BACKEND
```

The job **reuses the backend image** — the Dockerfile copies repo-root
`scripts/` into the image so jobs can `python scripts/...`. The only difference
from the live backend service is the container command and the enrichment env
(below).

## What's codified vs. one-time

| Piece | Where | Cadence |
|---|---|---|
| Staging job deploy (`fis-bd-gap-fill-staging`) | `.github/workflows/test.yml` → "Deploy BD gap-fill Cloud Run Job (staging)", gated `env.ENV == 'staging'` | Re-run on every push to `develop`. |
| Production job deploy (`fis-bd-gap-fill-prod`) | `.github/workflows/test.yml` → "Deploy BD gap-fill Cloud Run Job (production)", gated `env.ENV == 'production'` | Re-run on every push to `main`. |
| Cloud Scheduler trigger (staging + prod) | **Not in the repo** — created once with the gcloud commands below (prod: via `docs/runbooks/prod-cutover.md`). Points at the job by name, so it survives image redeploys. | One-time. |

## Enrichment env (differs from the advisor gap-fill job)

Both gap-fill jobs share the same secret set (`DATABASE_URL`, `GEMINI_API_KEY`,
`APOLLO_API_KEY`, `SERPAPI_API_KEY`, `HUNTER_API_KEY`, `SNOV_CLIENT_ID`,
`SNOV_CLIENT_SECRET`). The BD job additionally sets three **non-secret env vars**
to mirror the backend service, because the BD contacts sub-pipeline needs them:

- `CONTACT_ENRICHMENT_PROVIDER=apollo` — **required.** `services/contacts.py`
  raises `ContactEnrichmentUnavailableError` ("Enrichment unavailable") on the
  default `disabled`, failing the contacts sub-pipeline on every BD.
- `WEB_FALLBACK_ENABLED=true`, `WEB_FALLBACK_PHONES_ENABLED=true` — enable the
  free web-scrape email/phone fallback in the enrichment chain.

**PDL is deliberately NOT bound.** `PDL_API_KEY` is left unset so every PDL code
path is inert (it defaults to `None` and is gated on the key). Do **not** add it
to these jobs.

## One-time setup — staging

Constants: project `fis-lead-gen`, region `us-central1`, runtime service account
`136029935063-compute@developer.gserviceaccount.com`.

The job itself is created by the next staging deploy after this PR merges. If you
want it to exist *before* then, run the same `gcloud run jobs deploy …` block
from the workflow step manually.

```bash
# 1. Enable the Scheduler API (no-op if already enabled).
gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen

# 2. Let the scheduler's identity invoke the job.
gcloud run jobs add-iam-policy-binding fis-bd-gap-fill-staging \
  --project=fis-lead-gen \
  --region=us-central1 \
  --member="serviceAccount:136029935063-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"

# 3. Create the nightly cron (06:00 Eastern, auto-handles EST/EDT).
gcloud scheduler jobs create http fis-bd-gap-fill-nightly-staging \
  --project=fis-lead-gen \
  --location=us-central1 \
  --schedule="0 6 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fis-lead-gen/jobs/fis-bd-gap-fill-staging:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com"
```

Verify: `gcloud scheduler jobs describe fis-bd-gap-fill-nightly-staging --location=us-central1 --project=fis-lead-gen`.

## One-time setup — production

> **Prod is gated — enabling this cron starts paid enrichment against the
> PRODUCTION book.** The production job (`fis-bd-gap-fill-prod`) is wired in
> `.github/workflows/test.yml` (gated `env.ENV == 'production'`) and created /
> updated by each `main` deploy, but it stays **inert** until its scheduler
> exists. Create the scheduler only as part of the approved production cutover.

The cutover — create all three prod schedulers **and** pause the paid staging
ones so they don't double-spend — is driven by
**[`docs/runbooks/prod-cutover.md`](./prod-cutover.md)**, the single source of
truth for the sequence and verification. Do a first manual `--scan-only`
execution against prod to size the gap before enabling the nightly cron (see the
cutover runbook's sizing step). The bind + create for this job specifically:

```bash
# Run as part of the prod cutover — see docs/runbooks/prod-cutover.md.
gcloud run jobs add-iam-policy-binding fis-bd-gap-fill-prod \
  --project=fis-lead-gen --region=us-central1 \
  --member="serviceAccount:136029935063-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http fis-bd-gap-fill-prod-nightly \
  --project=fis-lead-gen --location=us-central1 \
  --schedule="0 6 * * *" --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fis-lead-gen/jobs/fis-bd-gap-fill-prod:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com"
```

## Manual operations

Commands below use the staging resources; swap `-staging` for `-prod` (and the
`fis-bd-gap-fill-nightly-staging` scheduler for `fis-bd-gap-fill-prod-nightly`) to
operate the production resources once the cutover has created them.

```bash
# Cost preview — read-only scan, NO writes, NO API calls (recommended first run).
# Requires temporarily overriding the deployed --apply args:
gcloud run jobs deploy fis-bd-gap-fill-staging --region=us-central1 --project=fis-lead-gen \
  --args=scripts/gap_fill_broker_dealers.py,--scan-only   # (then execute; re-deploy to restore --apply)

# Run the job now (uses the deployed --apply --newest-first --limit 300 args).
gcloud run jobs execute fis-bd-gap-fill-staging --region=us-central1 --project=fis-lead-gen

# Trigger via the scheduler (tests the whole cron path).
gcloud scheduler jobs run fis-bd-gap-fill-nightly-staging --location=us-central1 --project=fis-lead-gen

# Watch executions + logs.
gcloud run jobs executions list --job=fis-bd-gap-fill-staging --region=us-central1 --project=fis-lead-gen
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="fis-bd-gap-fill-staging"' \
  --project=fis-lead-gen --limit=200 --freshness=2h --format="value(timestamp,textPayload)" | sort
```

## Change the schedule

```bash
gcloud scheduler jobs update http fis-bd-gap-fill-nightly-staging \
  --location=us-central1 --project=fis-lead-gen \
  --schedule="0 6 * * *" --time-zone="America/New_York"

# Pause / resume without deleting:
gcloud scheduler jobs pause  fis-bd-gap-fill-nightly-staging --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs resume fis-bd-gap-fill-nightly-staging --location=us-central1 --project=fis-lead-gen
```

## Cost & guardrails

- **Paid providers:** each filled BD may call Apollo (website + contacts),
  SerpAPI (website), Gemini (financials / FOCUS PDF extraction), and Hunter/Snov
  (emails). FINRA + EDGAR are free. `--limit 300` bounds the spend per run.
- **Sizing `--limit`:** 300 clears a typical night's new-BD volume plus modest
  backlog. On a large cold-start backlog (e.g. the ~3k un-enriched BDs measured
  on staging), the job whittles it down 300/night, newest-first, without ever
  starving fresh arrivals. Raise the limit for a faster catch-up, lower it to
  cut spend.
- **PDL stays off:** never bind `PDL_API_KEY` to these jobs.
- **Idempotent:** the 30-day cooldown stamp means a same-day rerun skips
  already-attempted firms; `--reset-cooldown` forces a re-attempt after a code
  fix that should change extraction outcomes.

## Notes & gotchas

- **`--newest-first` vs. default:** the nightly job uses `--newest-first`
  (fresh-first). A one-off "enrich our best leads" pass should omit it to get the
  value-first order.
- **Contacts need the provider env:** without `CONTACT_ENRICHMENT_PROVIDER=apollo`
  the contacts sub-pipeline fails per-BD with "Enrichment unavailable" (website /
  financials / clearing still fill). The workflow step sets it; keep it.
- **Staging vs. prod:** the `-staging` and `-prod` jobs are separate resources
  with separate DB secrets (`DATABASE_URL_BACKEND_STAGING` vs
  `DATABASE_URL_BACKEND`) and separate schedulers — a prod run is never a side
  effect of a staging run. The prod job is wired in `test.yml`; its scheduler is
  created at the cutover (`docs/runbooks/prod-cutover.md`).
```
