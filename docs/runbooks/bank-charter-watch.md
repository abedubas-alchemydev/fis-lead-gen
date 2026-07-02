# Runbook: nightly bank-charter watch

How new banking charters (national + state) land in the **Banks** vertical
automatically, so the team sees fresh charters and pending applications each
morning — the banks sibling of `extract-new-bds`.

## TL;DR

`scripts/watch_bank_charters.py` ingests a trailing **30-day window** from the
official public sources, upserts idempotently into `banks` /
`bank_application_events`, reconciles opened OCC applications onto their FDIC
identity, and tags digital-assets applications. It runs as a **Cloud Run Job**
in two environments — `fis-bank-charter-watch-staging` (staging Neon DB) and
`fis-bank-charter-watch-prod` (production Neon DB) — each fired nightly by its
own **Cloud Scheduler** cron (created **out-of-band**, commands below; until
then the deployed jobs are inert). The CI workflow redeploys each job on that
env's deploy so it always runs the latest image.

- **Suggested schedule:** `30 4 * * *`, time zone `America/New_York` — after
  extract-new-bds (04:00), before bd-gap-fill (06:00). Any slot works; the
  sources are keyless public APIs.
- **Targets:** staging job → `DATABASE_URL_BACKEND_STAGING`; production job →
  `DATABASE_URL_BACKEND`. `DATABASE_URL` is the **only** secret — FDIC and OCC
  need no keys, and the job spends no paid-API budget.
- **Idempotent by construction:** upserts key on `fdic_cert` /
  `occ_control_number` / the `(bank_id, action, action_date)` event key,
  charter-status transitions are forward-only, the digital-assets tag is
  sticky, and the nightly window deliberately overlaps. Re-running any window
  is safe.

## Sources (all official / public — no gray-area methods)

| # | Source | What it contributes |
|---|--------|---------------------|
| 1 | **FDIC BankFind** `GET api.fdic.gov/banks/institutions` filtered on `ESTYMD:[start TO end]` | Newly **opened** insured institutions (both OCC and STATE charter authorities): identifiers, address, charter agent/regulator/class, established/insured dates, asset/deposit/office figures. |
| 2 | **FDIC BankFind** `GET api.fdic.gov/banks/history` filtered on `CHANGECODE:110` ("New Institution") | Corroboration stream — a brand-new cert occasionally appears here a day before the institutions index rebuild; the watcher fetches those certs directly. |
| 3 | **OCC Corporate Applications Search** `GET apps.occ.gov/CAS/api/search?fromDte&toDte&filingTypes=2` | "New Bank Charter" **applications** and every action on them (Receipt / Approved / Consummated-Effective / Withdrawn / rescissions) — the pending pipeline the FDIC can't see. Official but undocumented, so parsing is schema-tolerant and each item's raw JSON is kept on the event row. |
| 4 | **OCC national-banks directory** `occ.gov/.../financial-institution-lists/national-by-name.xlsx` | Charter number ↔ FDIC CERT ↔ RSSD mapping used to reconcile an opened application to its FDIC row. Links only on a **unique** exact normalized-name (+state) match. |
| 5 | **OCC Digital Assets Licensing Applications page** `occ.gov/.../digital-assets-licensing-applications/index-digital-assets-licensing-applications.html` | Digital-asset charter applications and conversions. Matching banks get `digital_assets=true` + the public-portion application **PDF URLs** (PDFs are never fetched or rendered). |

## What the job does

1. **FDIC phase** — fetch institutions with `ESTYMD` in the window, union in
   any `/history` 110 ("New Institution") certs the index hasn't picked up yet, upsert on
   `fdic_cert`. An FDIC-certificated institution is by definition `opened`.
2. **OCC phase** — fetch New Bank Charter actions with `actDte` in the
   window; group by control number (`cn`, e.g. `2026-Charter-344521`); upsert
   the bank row and one `bank_application_events` row per action. The row's
   `charter_status` advances forward-only (`pending → approved → opened`,
   `withdrawn`/`rescinded` terminal-ish), `application_received_date` pins to
   the earliest Receipt, `last_action_date` to the newest action.
3. **Reconcile phase** — for OCC application rows still lacking an FDIC
   identity, look them up in the national-banks directory. On a unique match:
   stamp `occ_charter_number`/`fed_rssd`; when the directory carries a CERT,
   either fold the application row into the already-ingested FDIC row (events
   move with it) or stamp the cert and pull the FDIC record to enrich in
   place. Uninsured trust banks (no CERT — common for digital-asset trust
   charters) still get their charter number/RSSD.
4. **Digital-assets phase** — parse the page's `Date received | Applicant |
   Application` table; on a unique normalized-name match, set
   `digital_assets=true`, merge the PDF URL into `digital_asset_pdfs`
   (deduped), and backfill `application_received_date` on OCC rows that lack
   one. Zero or ambiguous matches are **logged, never guessed** (conversions
   of long-established banks won't be in the vertical, by design).

Each phase commits independently, and each run logs a
`summary: fdic_records=… occ_filings=… occ_reconciled=… digital_assets_tagged=…`
line.

## Architecture

```
 Cloud Scheduler (cron, America/New_York — created out-of-band)
   fis-bank-charter-watch-nightly       ── POST …/jobs/fis-bank-charter-watch-staging:run
   fis-bank-charter-watch-prod-nightly  ── POST …/jobs/fis-bank-charter-watch-prod:run
        │ (OAuth, runtime SA)
        ▼
 Cloud Run Job  fis-bank-charter-watch-{staging,prod}   (backend image, CMD override)
        │  python scripts/watch_bank_charters.py --apply
        ▼
 FDIC BankFind + OCC CAS / occ.gov (public, keyless)  ──►  Neon DB (banks, bank_application_events)
```

The job **reuses the backend image** — the Dockerfile copies repo-root
`scripts/` to `/app/scripts/` exactly so jobs can `python scripts/...`. Rows
are read back by `GET /api/v1/banks*` (feature-gated on the `banks`
permission).

## What's codified vs. one-time

| Piece | Where | Cadence |
|---|---|---|
| Staging job deploy (`fis-bank-charter-watch-staging`) | `.github/workflows/test.yml` → "Deploy bank-charter-watch Cloud Run Job (staging)", gated `env.ENV == 'staging'` | Re-run on every push to `develop`. |
| Production job deploy (`fis-bank-charter-watch-prod`) | same file → "(production)", gated `env.ENV == 'production'` | Re-run on every push to `main`. |
| Alembic migration (`20260702_0001` — `banks` + `bank_application_events`) | applied automatically by the deploy pipeline's "Apply Alembic migrations" step | Once per env. |
| Cloud Scheduler triggers (staging + prod) | **Not in the repo** — created once with the gcloud commands below. The deployed jobs are inert until then. | One-time. |

## One-time setup — staging

Constants: project `fis-lead-gen`, region `us-central1`, runtime service
account `136029935063-compute@developer.gserviceaccount.com`.

```bash
# 1. Enable the Scheduler API (no-op if already enabled).
gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen

# 2. Let the scheduler's identity invoke the job.
gcloud run jobs add-iam-policy-binding fis-bank-charter-watch-staging \
  --project=fis-lead-gen \
  --region=us-central1 \
  --member="serviceAccount:136029935063-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"

# 3. Create the nightly cron (04:30 Eastern, auto-handles EST/EDT).
gcloud scheduler jobs create http fis-bank-charter-watch-nightly \
  --project=fis-lead-gen \
  --location=us-central1 \
  --schedule="30 4 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fis-lead-gen/jobs/fis-bank-charter-watch-staging:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com"
```

Verify: `gcloud scheduler jobs describe fis-bank-charter-watch-nightly --location=us-central1 --project=fis-lead-gen`.

## One-time setup — production

Same as staging with the prod names; the prod job already targets the
production DB via `DATABASE_URL_BACKEND` (wired in the workflow):

```bash
gcloud run jobs add-iam-policy-binding fis-bank-charter-watch-prod \
  --project=fis-lead-gen \
  --region=us-central1 \
  --member="serviceAccount:136029935063-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http fis-bank-charter-watch-prod-nightly \
  --project=fis-lead-gen \
  --location=us-central1 \
  --schedule="30 4 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fis-lead-gen/jobs/fis-bank-charter-watch-prod:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com"
```

## One-time backfill

The nightly window only covers the trailing 30 days. To seed history, run the
watcher once per env with a wide `--from-date` (it is the same idempotent
code path — a later nightly run re-covering any of it is a no-op):

```bash
# Fetch the env's DB URL from Secret Manager (DATABASE_URL_BACKEND for prod)
# into the ENVIRONMENT — never pass the DSN as a CLI argument (argv leaks
# into shell history and `ps` output). The script reads DATABASE_URL itself.
export DATABASE_URL=$(gcloud secrets versions access latest --secret=DATABASE_URL_BACKEND_STAGING --project=fis-lead-gen)

# Dry-run first: fetch + log everything the backfill WOULD write.
python scripts/watch_bank_charters.py --from-date 2024-01-01

# Apply.
python scripts/watch_bank_charters.py --from-date 2024-01-01 --apply
```

Pick the start date for how much history the client wants on day one
(2024-01-01 gives a two-and-a-half-year book of new charters and captures the
still-pending OCC applications, which mostly date from 2025-2026). Volumes
are small — the FDIC sees a few dozen new charters a year and the OCC a few
dozen applications — so even a multi-year backfill is one quick run.

## Manual operations

```bash
# Run the job now (uses the deployed --apply args — writes to that env's DB).
gcloud run jobs execute fis-bank-charter-watch-staging --region=us-central1 --project=fis-lead-gen

# Watch executions + logs.
gcloud run jobs executions list --job=fis-bank-charter-watch-staging --region=us-central1 --project=fis-lead-gen
gcloud run jobs executions logs read <EXECUTION_ID> --region=us-central1 --project=fis-lead-gen

# Dry-run locally against an env DB (default is dry — only --apply writes).
# DATABASE_URL comes from the environment (see "One-time backfill" for the
# export) — never put the DSN on the command line.
python scripts/watch_bank_charters.py

# Phase toggles, e.g. re-run just the digital-assets tagging:
python scripts/watch_bank_charters.py --apply --skip-fdic --skip-occ
```

## Notes & gotchas

- **CAS is official but undocumented.** Parsing is schema-tolerant: unknown
  `act` vocabulary is stored verbatim on the event and maps to **no** status
  change; placeholder address junk (`TBD`, `To be confirmed`, `XXXXX` zips)
  is nulled at parse time; each item's raw JSON rides along on the event row.
  If OCC reshapes the response, the run logs loudly and skips rather than
  corrupting rows.
- **Status can never regress.** A window that re-sees `Receipt` after
  `Approved` already landed cannot demote the row (rank-guarded in both the
  ORM path and the SQL `ON CONFLICT` path).
- **State charters** never pass through the OCC — they arrive FDIC-only with
  `charter_authority='STATE'`. That is the expected shape, not a
  reconciliation failure.
- **Digital-assets matching is name-based** (the page publishes no control
  number or cert), so it requires a unique normalized-name match and
  otherwise logs. Conversions of long-established banks (e.g. a 30-year-old
  state bank converting) won't match anything in the vertical — expected;
  they show up in the log line `digital-assets: … 0 match(es)`.
- **The two FDIC date formats differ per endpoint** (`MM/DD/YYYY` on
  /institutions, ISO on /history) — both handled; don't "fix" one to match
  the other.
- **Schedulers are out-of-band on purpose** (same convention as every other
  nightly job): deploying this PR creates/updates the job resources only.
  Nothing runs until the crons above are created.
