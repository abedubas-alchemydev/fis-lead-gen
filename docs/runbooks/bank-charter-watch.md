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
  `DATABASE_URL_BACKEND`. Two secrets: `DATABASE_URL`, plus the **optional**
  `OCC_API_KEY` (api.data.gov key for the official OCC Institutions API — the
  reconcile phase's primary directory; shared across both envs). The job
  spends no paid-API budget, and it **still works keyless**: with the key
  unset (or the API down) the reconcile phase falls back to the public
  `national-by-name.xlsx` workbook (`reconcile_source=xlsx` in the summary).
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
| 4a | **OCC Institutions API** `GET api.occ.gov/institutions/active` (`X-Api-Key` from `OCC_API_KEY`) — **primary** | Charter number ↔ FDIC CERT ↔ RSSD mapping used to reconcile an opened application to its FDIC row, plus **LEI** and **CharterType** enrichment (`banks.lei` / `banks.charter_type`). Official *and documented*. Links only on a **unique** exact normalized-name (+state) match. |
| 4b | **OCC national-banks directory** `occ.gov/.../financial-institution-lists/national-by-name.xlsx` — **fallback** (keyless) | Same charter ↔ CERT ↔ RSSD mapping (no LEI/CharterType), used when `OCC_API_KEY` is unset or the API errors. Identical unique-match semantics. |
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
   identity, look them up in the active-institutions directory: the
   **Institutions API first** (`reconcile_source=api`); if the key is unset
   or the API fails, the **XLSX workbook** (`reconcile_source=xlsx`) — same
   unique-match / never-guess semantics either way. On a unique match: stamp
   `occ_charter_number`/`fed_rssd` (and, API path only, `lei`/`charter_type`
   — additive, never overwriting); when the directory carries a CERT, either
   fold the application row into the already-ingested FDIC row (events and
   the enrichment move with it) or stamp the cert and pull the FDIC record
   to enrich in place. Uninsured trust banks (no CERT — common for
   digital-asset trust charters) still get their charter number/RSSD.
4. **Digital-assets phase** — parse the page's `Date received | Applicant |
   Application` table; on a unique normalized-name match, set
   `digital_assets=true`, merge the PDF URL into `digital_asset_pdfs`
   (deduped), and backfill `application_received_date` on OCC rows that lack
   one. Zero or ambiguous matches are **logged, never guessed** (conversions
   of long-established banks won't be in the vertical, by design). After the
   page rows, the phase applies the curated `KNOWN_DIGITAL_ASSET_APPLICANTS`
   seed — see "Manually tagging known digital-asset banks" below.

Each phase commits independently, and each run logs a
`summary: fdic_records=… occ_filings=… occ_reconciled=… reconcile_source=api|xlsx digital_assets_tagged=…`
line (`reconcile_source` appears whenever the reconcile phase had rows to
work on).

## OCC Institutions API (`OCC_API_KEY`)

The reconcile directory's primary source is the OCC's official, documented
API at **api.occ.gov**. Portal notes: the developer-portal SPA is
JS-rendered (a plain fetch of the docs page shows nothing — the doc content
lives in the SPA's `assets/data/institution.json`), and it hosts three APIs:
**Institutions** (used here), **CRA evaluations**, and **EnforcementActions**
— the latter two are natural future candidates for this vertical (e.g. an
enforcement-actions signal on the bank detail page).

- **Endpoint:** `GET https://api.occ.gov/institutions/active` → ~990 records
  (verified live 2026-07-02) with `CharterNumber`, `BankName`, `BankCity`,
  `BankStateCode`, `BankAddress`, `ZipCode`, `InstNationalTrustCompanyInd`,
  `CharterType` (e.g. `TrustCo-National`), `CharterDate`,
  `FDICCertificateNumber`, `FDICInsuranceStatus`, `FRBRSSDNumber`,
  `LegalEntityIdentifier`, `RCON`.
- **Auth:** an api.data.gov key sent as `X-Api-Key` (the client deliberately
  uses the header, not `?api_key=`, so the key can't leak into URLs/logs).
  Stored in GCP Secret Manager as **`OCC_API_KEY`** and injected into both
  Cloud Run Jobs as the `OCC_API_KEY` env var by the deploy steps.
- **Budget:** rate limit ~1000 requests/hour; the watcher makes **one** call
  per run.
- **Keyless still works:** with `OCC_API_KEY` unset (local runs, a revoked
  key, an API outage) the client logs a warning, returns nothing, and the
  reconcile phase falls back to the `national-by-name.xlsx` workbook —
  identical matching, just without the LEI/CharterType enrichment. Watch for
  `reconcile_source=xlsx` in a keyed environment: that means the API call
  failed and the log's warning line says why.
- `CharterType` is **descriptive only** — deliberately never used to infer
  `digital_assets` (plenty of `TrustCo-National` institutions, e.g. Citicorp
  Trust Delaware, are not digital-asset businesses; the digital-assets tag
  comes exclusively from OCC's own digital-assets page + curated seed).

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
 FDIC BankFind + OCC CAS / occ.gov (public, keyless)
   + api.occ.gov Institutions API (OCC_API_KEY; keyless XLSX fallback)  ──►  Neon DB (banks, bank_application_events)
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
| Alembic migrations (`20260702_0001` — `banks` + `bank_application_events`; `20260702_0002` — additive `banks.lei` + `banks.charter_type`; `20260702_0003` — `bank_contacts`) | applied automatically by the deploy pipeline's "Apply Alembic migrations" step | Once per env. |
| `OCC_API_KEY` secret (api.data.gov key for the Institutions API) | GCP Secret Manager (exists, v1); wired into both jobs via the deploy steps' `--set-secrets` | One-time (rotate by adding a secret version). |
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

## Weekly full-directory sync (production)

The nightly above only covers the trailing-30-day *new-charter* window. To keep
the **full ~4,400-institution directory** fresh (name / asset / status changes
across the whole FDIC+OCC list, not just brand-new charters), a separate
**weekly** cron re-runs the watcher's full-directory phase. It reuses the same
prod job with an **args override** on the **v2** `:run` endpoint — the v1
Knative `:run` the nightly uses does not carry container overrides — running
`--full-sync --skip-fdic --skip-occ --apply`: FDIC all-active fetch + OCC
directory upsert only (the daily windowed FDIC-history / OCC-CAS phases stay on
the nightly). Free (FDIC BankFind + OCC public APIs — no Apollo/Gemini) and
fully idempotent (upsert, no deletes), so any overlap with the nightly is a
no-op.

```bash
gcloud scheduler jobs create http fis-bank-charter-full-sync-prod-weekly \
  --project=fis-lead-gen \
  --location=us-central1 \
  --schedule="0 3 * * 0" \
  --time-zone="America/New_York" \
  --uri="https://run.googleapis.com/v2/projects/fis-lead-gen/locations/us-central1/jobs/fis-bank-charter-watch-prod:run" \
  --http-method=POST \
  --oauth-service-account-email="136029935063-compute@developer.gserviceaccount.com" \
  --headers="Content-Type=application/json" \
  --message-body='{"overrides":{"containerOverrides":[{"args":["scripts/watch_bank_charters.py","--full-sync","--skip-fdic","--skip-occ","--apply"]}]}}'
```

Runs Sundays 03:00 ET (distinct from the 04:30 daily). Verify or trigger on
demand:
`gcloud scheduler jobs run fis-bank-charter-full-sync-prod-weekly --location=us-central1 --project=fis-lead-gen`.
First populated both envs 2026-07-03 (59 → ~4,412 prod / 4,411 staging). The
`--full-sync` flag stays **out** of the deployed job args on purpose (Bruce's
design); only this weekly cron supplies it, via the override above.

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

## Manually tagging known digital-asset banks

The OCC digital-assets page lists only **current** applications — decided
ones are pruned — so a publicly-known digital-asset charter that rolled off
the page before our first scrape can never be tagged from the page alone
(e.g. **Erebor Bank, N.A.**, OH, charter 25357, opened Feb 2026). For those,
the digital-assets phase also applies a curated seed:
`KNOWN_DIGITAL_ASSET_APPLICANTS` in `scripts/watch_bank_charters.py`.

To tag another bank, add **one line** — `(name, state, occ_charter_number or
None)` — and only for banks whose digital-asset status is client-confirmed
public knowledge (OCC news release / press coverage):

```python
KNOWN_DIGITAL_ASSET_APPLICANTS: tuple[tuple[str, str, str | None], ...] = (
    ("Erebor Bank, N.A.", "OH", "25357"),  # opened 2026-02; off the page pre-scrape
    ("Example Digital Bank, N.A.", "NY", None),  # <- new entry, charter unknown
)
```

Behavior (same never-guess policy as the page matcher):

- Matches by `occ_charter_number` when the entry carries one (strong key);
  otherwise — including when no row has that charter number stamped yet — by
  a **unique** normalized-name match narrowed by the entry's state.
- On a match the sticky `digital_assets=true` flips and the run logs
  `digital-assets: seed-tagged '<name>'`. Seed entries carry **no PDFs** and
  no received-date — nothing else on the row changes.
- Zero or ambiguous matches are logged and skipped, never guessed. An entry
  that keeps logging `0 match(es)` usually means the bank isn't in the
  vertical yet (widen the backfill window) or the name needs the exact legal
  spelling.
- Idempotent: entries stay in the list forever; re-runs are no-ops
  (`digital_assets_seed_tagged=0` in the summary once tagged).

Deploying the change is enough — the next nightly run applies it. To apply
immediately, run just the digital-assets phase (see above):
`python scripts/watch_bank_charters.py --apply --skip-fdic --skip-occ`.

### Historical backfill from the Wayback archive

The seed list covers banks someone *remembers*; the data-driven complement is
the **one-off** `--backfill-digital-assets-history` mode, which reconstructs
every applicant the digital-assets page has EVER listed. The content is OCC's
own official page — just served from the public Internet Archive
(web.archive.org), which holds ~monthly 200-OK captures of it:

1. Query the archive's **CDX index** for one 200-OK capture per month
   (`collapse=timestamp:6`).
2. Fetch each capture via the raw **`id_` URL** (original page bytes, no
   archive chrome) with a ~1 s polite delay. A failed capture logs a warning
   and is skipped; the run continues.
3. Parse with the **same parser** as the live page. Any PDF href the archive
   rewrote onto `web.archive.org` is normalized back to its original occ.gov
   URL *before* the occ.gov host allowlist runs; anything that doesn't
   normalize cleanly is dropped. PDFs are never fetched or rendered — URLs
   only, same as the nightly phase.
4. **Union** the rows across captures — deduped by normalized name + received
   date, keeping the newest non-empty PDF URL set per applicant — and feed
   the union through the **same sticky, unique-match-or-skip tagging** as
   live-page rows.

One-off commands (DB URL via the environment, exactly like the backfill
above — never on the command line; dry-run first):

```bash
# Staging
export DATABASE_URL=$(gcloud secrets versions access latest --secret=DATABASE_URL_BACKEND_STAGING --project=fis-lead-gen)
python scripts/watch_bank_charters.py --skip-fdic --skip-occ --backfill-digital-assets-history          # dry-run
python scripts/watch_bank_charters.py --skip-fdic --skip-occ --backfill-digital-assets-history --apply  # write

# Production
export DATABASE_URL=$(gcloud secrets versions access latest --secret=DATABASE_URL_BACKEND --project=fis-lead-gen)
python scripts/watch_bank_charters.py --skip-fdic --skip-occ --backfill-digital-assets-history          # dry-run
python scripts/watch_bank_charters.py --skip-fdic --skip-occ --backfill-digital-assets-history --apply  # write
```

`--skip-fdic --skip-occ` leaves the normal digital-assets phase (live page +
seed) running alongside the history union — all idempotent, so that's the
recommended shape. Add `--skip-digital-assets` to run the history union
alone: the backfill flag implies the digital-assets machinery, so it still
runs. The summary line reports
`digital_assets_history_snapshots / _rows / _tagged / _unmatched`.

Expectations: re-running is safe (same sticky/idempotent tagging — a second
pass reports `digital_assets_history_tagged=0`); a healthy `_unmatched`
count is normal (decided applications for banks never ingested — e.g. old
conversions — match nothing, by design); recovered PDF links point at
occ.gov and may themselves have rolled off (stored as references, never
fetched). The archive 503s freely under load (observed live: 6 of 13
captures on one pass) — each failure is a logged warning, not an abort, and
because the union tolerates gaps and tagging is sticky, simply re-running
until `_snapshots` stops growing converges on the full history.

## Extracting contacts from application PDFs

The client asked for "contacts, everything we can grab" from the
public-portion charter-application PDFs. The opt-in
**`--extract-contacts`** phase (phase 5, after the digital-assets/history
phases) is the banks sibling of the FOCUS "PERSON TO CONTACT" extraction on
the BD side: for every bank whose `digital_asset_pdfs` carries PDF links, it
downloads each PDF and conservatively extracts the PEOPLE the filing names
into **`bank_contacts`**, surfaced on `GET /api/v1/banks/{id}` as the
`contacts` array.

What is extracted (`role_context` on each row):

| `role_context` | Source pattern in the PDF |
|---|---|
| `contact_person` | The "Contact person" / "Primary contact" / "Person to contact" block, or a "please contact Jane Doe at …" sentence — plus any phone/email printed alongside. |
| `organizer` | Names under an "Organizers" / "Organizing group" heading, or an "the organizers are …" sentence. |
| `proposed_officer` | "proposed President / CEO / director / Chief \* Officer" sentences; the title is stored verbatim. |
| `counsel` | Counsel-of-record ("Counsel: Jane Doe", "Attorney for the applicant: …", "Jane Doe, counsel to the organizers"). |

**Conservative-extraction policy** (same never-guess rule as every matcher
in this watcher): a person is stored ONLY when the pattern around the hit is
unambiguous AND the candidate passes strict person-name validation (2-4
title-cased words, no digits, no organization/form vocabulary). Everything
else — a law firm where a person was expected, an ALL-CAPS header, a "TBD"
— is **logged and counted, never written**. Every stored row keeps its
receipt: `page_number` + `context_snippet` + `source_url` (the occ.gov PDF).

Safety rails: PDF URLs are re-verified against the **https occ.gov
allowlist** before fetching (and again after redirects), downloads are
capped at **20MB** with a bounded timeout, and text extraction runs in a
**crash-isolated subprocess** (`_pdf_text_worker.py`, the pdfminer sibling
of `_pdf_render_worker.py`) — a corrupt PDF degrades to a parse-miss and can
never kill the run. No paid APIs; occ.gov only.

**Idempotent:** upserts key on `(bank_id, name, coalesce(title,''), source)`
(`uq_bank_contacts_dedupe`), so re-running over the same PDFs is a no-op —
safe to compose with any other phase, any night.

### LLM-assisted extraction (Gemini recall pass)

The regex extractor's strictness costs recall (its first production sweep
stored 40 people while refusing ~290 ambiguous candidates), so the phase
also sends each PDF's per-page text to **Gemini**
(`services/bank_contact_llm.py`, the repo's structured-output client) and
unions the results. The never-fabricate policy is enforced by **grounding
validation**, not by trusting the model:

- **Grounding rule** — an LLM-returned person is kept ONLY when their name
  appears **verbatim in the source page text** (case-insensitive,
  whitespace-flexible: name tokens in order, separated only by whitespace,
  word-boundary guarded). Email/phone/title values pass the same check;
  an ungrounded value is nulled, never stored. The stored `page_number` +
  `context_snippet` are cut from where the name *actually* matched in the
  source text. Everything the gate refuses is logged with its reason and
  counted (`bank_contacts_llm_dropped_ungrounded`) — a dropped
  hallucination can never reach the DB.
- **Merge** — regex + Gemini results union per bank, deduped on the same
  `(name, title)` key the upsert uses; **regex rows win on conflict** (they
  carry the pattern-anchored snippet). LLM-only people are tagged
  `source='application_pdf_llm'` so they stay distinguishable in the DB and
  on the detail card; a later regex hit for the same person supersedes
  (deletes) its LLM twin at upsert time.
- **GEMINI_API_KEY is optional** — without it (or on any API error /
  response-shape surprise) the service logs a warning and the phase behaves
  **exactly like the regex-only extractor**: the watcher can never fail
  because Gemini is down. No new flags; setting the key is the switch.

One-off commands (DB URL via the environment, exactly like the other
backfills — never on the command line; dry-run first):

```bash
# Staging
export DATABASE_URL=$(gcloud secrets versions access latest --secret=DATABASE_URL_BACKEND_STAGING --project=fis-lead-gen)
python scripts/watch_bank_charters.py --skip-fdic --skip-occ --extract-contacts          # dry-run: logs what it WOULD write
python scripts/watch_bank_charters.py --skip-fdic --skip-occ --extract-contacts --apply  # write

# Production
export DATABASE_URL=$(gcloud secrets versions access latest --secret=DATABASE_URL_BACKEND --project=fis-lead-gen)
python scripts/watch_bank_charters.py --skip-fdic --skip-occ --extract-contacts          # dry-run
python scripts/watch_bank_charters.py --skip-fdic --skip-occ --extract-contacts --apply  # write
```

`--skip-fdic --skip-occ` leaves the digital-assets phase running alongside
(so a PDF link tagged in the same run is extracted in the same run); add
`--skip-digital-assets` to run contact extraction alone.

**Nightly:** the phase is **opt-in by design** (the deployed jobs run plain
`--apply` and are unaffected). To run it nightly, add `--extract-contacts`
to the Cloud Run Job args in `.github/workflows/test.yml` (both deploy
steps' `--args=scripts/watch_bank_charters.py,--apply,--extract-contacts`)
— idempotency makes the nightly re-scan free; only new PDFs/people produce
writes.

The summary line reports five keys:

- `bank_contacts_pdfs_fetched` — PDFs actually downloaded (allowlist-passed,
  HTTP 200, under the size cap) across all banks;
- `bank_contacts_extracted` — people in the final merged set (regex +
  grounded Gemini, deduped per bank). Dry-run: the would-write set. Apply:
  the upserted set — a re-run reports the same number with zero DB changes
  (the per-bank log line breaks out `inserted=` vs `updated=`);
- `bank_contacts_skipped_ambiguous` — pattern hits refused by the
  conservative regex validators (logged with page + reason, never written).
  A non-zero count is NORMAL — redacted public portions and law-firm counsel
  lines land here by design;
- `bank_contacts_llm_extracted` — people the Gemini recall pass ADDED beyond
  regex (grounded + novel, `source='application_pdf_llm'`; the recall win).
  Zero when GEMINI_API_KEY is unset;
- `bank_contacts_llm_dropped_ungrounded` — Gemini-returned records refused
  by the grounding gate (name/value not printed verbatim on the source
  pages, org vocabulary, unmappable role — logged with reason, never
  written). Non-zero is the gate WORKING, not a failure.

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
