# Tier 2 — Cloud Scheduler cron jobs (provisioned)

**Authored:** 2026-04-29
**Activated:** 2026-04-30 (~08:25 PHT / 00:25 UTC)
**Project:** `fis-lead-gen`
**Region:** `us-central1`
**Invoker SA:** `136029935063-compute@developer.gserviceaccount.com` (same SA the frontend uses to invoke `fis-backend` — has `roles/run.invoker` on the backend service).
**Audience (matches backend allowlist):** `https://fis-backend-136029935063.us-central1.run.app`

> **Status:** *Provisioned 2026-04-30.* Both blockers cleared: BE PR #189 shipped the OIDC-aware `/api/v1/pipeline/run/{kind}` endpoints on 2026-04-29, and ops enabled the Cloud Scheduler API and provisioned the three jobs on 2026-04-30. `filing-monitor-hourly` smoked successfully end-to-end (`HTTP 200`, ~12 min wall, audience match, SA-OIDC accepted).

## Activation log

- **2026-04-30T00:14:45Z** (~08:14 PHT): Enabled Cloud Scheduler API on `fis-lead-gen` via `gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen`. Operation `acf.p2-136029935063-ae10396c-3fb5-4e08-b2ef-318977b0c892` finished successfully.
- **2026-04-30T00:14:55Z – 00:15:23Z**: Created three scheduler jobs in `us-central1`:
  - `filing-monitor-hourly` — `0 * * * *` UTC
  - `populate-all-weekly` — `0 2 * * 0` UTC
  - `initial-load-weekly` — `0 6 * * 0` UTC
- **First smoke (00:18:34Z) failed `403 PERMISSION_DENIED`.** Backend log: `Rejected OIDC token on pipeline endpoint: Token has wrong audience https://fis-backend-saxzdkn5nq-uc.a.run.app, expected one of ['https://fis-backend-136029935063.us-central1.run.app']`. Root cause: `gcloud run services describe fis-backend --format='value(status.url)'` returns the hash-form URL (`fis-backend-saxzdkn5nq-uc.a.run.app`), but the backend's `settings.backend_audience` allowlist is hardcoded to the project-number-form URL (`fis-backend-136029935063.us-central1.run.app`). The two URLs route to the same Cloud Run service, but only the latter passes the audience check in `_ensure_admin_or_scheduler_sa`.
- **2026-04-30T00:20:13Z – 00:20:33Z**: Updated all three jobs' `--uri` and `--oidc-token-audience` to the project-number-form URL.
- **Second smoke (00:21:22Z) failed `URL_REJECTED-REJECTED_DEADLINE_EXCEEDED` after 180s.** Root cause: `filing-monitor` is **synchronous** (per `reports/be-pipeline-endpoints-tier2-2026-04-29.md`, ~5–15 min wall time), but Cloud Scheduler's default attempt-deadline is 180s.
- **2026-04-30T00:27:26Z**: Updated `filing-monitor-hourly` with `--attempt-deadline=1800s` (max). The two async jobs (`populate-all-weekly`, `initial-load-weekly`) kept the 180s default — they return immediately via FastAPI `BackgroundTasks`.
- **Third smoke (00:27:54Z) — SUCCESS at 00:39:35Z.** Cloud Scheduler reported `URL_CRAWLED. Original HTTP response code number = 200`. Backend log shows `INFO: 169.254.169.126 - "POST /api/v1/pipeline/run/filing-monitor HTTP/1.1" 200 OK` at 00:39:26Z. End-to-end wall time ≈ 11m 41s (cold start + sync filing-monitor pipeline + 200 response).
- **All three jobs `state=ENABLED`** as of 00:27:26Z. SA-OIDC delivery confirmed end-to-end via the smoke result above (the audience-mismatch rejection log earlier proved token delivery; the 200 on a corrected audience proves the full auth path resolves).
- **No code, no IAM, no secrets, no Cloud Run service changes.** All ops were `gcloud services enable`, `gcloud scheduler jobs create http`, and `gcloud scheduler jobs update http` against the existing runtime SA.

## Why this exists

Pre-2026-04-29, the data pipeline only ran when someone manually triggered `python -m scripts.populate_all_data` from a workstation. Result: prod data drifted 1-2 months stale, flagged by Deshorn on 2026-04-27. Tier 1 (manual cache-bust + re-run) restored fresh data for that complaint. Tier 2 (this runbook) is the structural fix — scheduled jobs that run the pipeline on a fixed cadence with no human in the loop.

## Provisioned jobs

| Name | Schedule (UTC) | Cadence rationale | Target endpoint | Mode | Attempt deadline |
|---|---|---|---|---|---|
| `filing-monitor-hourly` | `0 * * * *` | Catch new SEC filings the same business day they post → drives `/alerts` freshness | `POST /api/v1/pipeline/run/filing-monitor` | sync (~5–15 min wall) | **1800s** |
| `populate-all-weekly` | `0 2 * * 0` | Weekly full enrichment refresh — Sunday 02:00 UTC (lowest-traffic window for Neon) | `POST /api/v1/pipeline/run/populate-all` | async (FastAPI `BackgroundTasks`, returns 202 immediately) | 180s default |
| `initial-load-weekly` | `0 6 * * 0` | Weekly catch-up of newly-registered broker-dealers from FINRA — runs after `populate-all` so new BDs land before the next weekday | `POST /api/v1/pipeline/run/initial-load` | async (FastAPI `BackgroundTasks`, returns 202 immediately) | 180s default |

## Blocker A — Cloud Scheduler API not enabled — RESOLVED 2026-04-30

`gcloud scheduler jobs list --location=us-central1 --project=fis-lead-gen` returns:

```
ERROR: (gcloud.scheduler.jobs.list) PERMISSION_DENIED: Cloud Scheduler API has not
been used in project fis-lead-gen before or it is disabled.
```

This is a one-time, project-level enablement. To clear:

```bash
gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen
```

Project-level API enablement is treated as an infra change here — not auto-applied during runbook authoring. Owner: Arvin or whoever holds project owner / `serviceusage.services.enable`.

## Blocker B — Admin endpoints are session-cookie-gated, Scheduler can't supply one — RESOLVED 2026-04-29

> **Resolution:** BE PR #189 (squash-merged at `f1e4f660`, promoted to main via PR #190 at `aeba7fa2`) shipped a new dual-auth dependency `_ensure_admin_or_scheduler_sa` and three pipeline endpoints on `fis-backend-00089-l7l`. They accept either an admin BetterAuth session cookie or a `Authorization: Bearer <id_token>` whose `aud` matches `settings.backend_audience` and `email` matches `settings.cloud_scheduler_sa_email` (defaulting to the runtime SA). See `reports/be-pipeline-endpoints-tier2-2026-04-29.md` for the full design + tests. The original Blocker B writeup is preserved below for audit trail.

The two-layer auth model documented in `CLAUDE.md` ("Service-to-service auth on Cloud Run") is what blocks us:

- **Layer 1 (infra) — Google OIDC**: ✓ Cloud Scheduler with `--oidc-service-account-email` + `--oidc-token-audience` *can* clear `roles/run.invoker` on `fis-backend`. The runtime SA already holds that role (verified 2026-04-29).
- **Layer 2 (app) — BetterAuth session cookie + admin role**: ✗ Every pipeline-trigger endpoint runs through `get_current_user` in `backend/app/services/auth.py`, which reads `better-auth.session_token` (or `__Secure-better-auth.session_token` in prod) from `request.cookies` and HMAC-validates it against `settings.auth_secret`. Then `_ensure_admin` requires `current_user.role == "admin"`. Cloud Scheduler has no way to mint a BetterAuth session cookie.

Net effect: a Cloud Scheduler job hitting `POST /api/v1/alerts/monitor/run` today would return `401 "Authentication required."` from `get_current_user` — never even reaching the admin role check.

### Endpoint inventory (verified 2026-04-29)

What actually exists in `backend/app/api/v1/endpoints/`:

| Slot | Endpoint | Auth gate | Notes |
|---|---|---|---|
| filing monitor | `POST /api/v1/alerts/monitor/run` | session cookie + admin | exists, blocked by Layer 2 |
| clearing pipeline | `POST /api/v1/pipeline/clearing/run` | session cookie + admin | exists, blocked by Layer 2 |
| clearing+filing combo | `POST /api/v1/settings/refresh-data` | session cookie + admin | exists, blocked by Layer 2 |
| populate-all (full pipeline) | *(none)* | n/a | only `python -m scripts.populate_all_data` |
| initial-load (FINRA bootstrap) | *(none)* | n/a | only `python -m scripts.initial_load` |

The "populate-all" and "initial-load" slots have **no HTTP entrypoint at all** — they're scripts run from the repo root. Even if the auth gap closes, those two would still need new endpoints before Cloud Scheduler can invoke them.

### Required BE follow-up

A backend PR that adds an OIDC-token-gated entrypoint family — for example `POST /api/v1/internal/scheduled/{kind}` — that:

1. Authenticates via `Authorization: Bearer <google-oidc-token>` instead of session cookie.
2. Validates the token's `iss` is `https://accounts.google.com`, `aud` matches the backend URL, and `email` matches the runtime SA `136029935063-compute@developer.gserviceaccount.com`. Reject anything else with 401.
3. Maps `{kind}` to the existing services without going through `_ensure_admin`:
   - `filing-monitor` → `filing_monitor_service.run(db, trigger_source="scheduled")`
   - `clearing-pipeline` → `pipeline_service.run(db, trigger_source="scheduled")`
   - `populate-all` → factor `scripts/populate_all_data.py` into a service callable from the request handler, **or** keep it as a script and have this endpoint trigger a Cloud Run Job (preferred — long-running pipeline shouldn't run inside the request lifecycle of a Cloud Run service with a 60-min ceiling).
   - `initial-load` → same pattern as `populate-all`.

For the long-running variants the cleaner shape is **Cloud Run Jobs + Cloud Scheduler triggering the job execution** (rather than POSTing to the service). Jobs can run for up to 24h, are designed for batch workloads, and use the same OIDC auth pattern. Decision deferred to the BE PR.

Until that PR ships and is deployed, **no Cloud Scheduler job here will succeed**, so creating them now would just result in 401s in `gcloud scheduler` execution logs and silently growing operational noise.

## Provisioning commands (recreate-from-scratch reference)

These are the **as-applied** commands from the 2026-04-30 activation — keep in sync with the live job state. Two gotchas baked in:

1. `BACKEND_URL` must be the project-number-form URL (`https://fis-backend-136029935063.us-central1.run.app`), not the hash-form returned by `gcloud run services describe ... format=value(status.url)`. The backend's `settings.backend_audience` allowlist only accepts the project-number form.
2. `filing-monitor-hourly` must set `--attempt-deadline=1800s` because the endpoint is synchronous (~5–15 min). The two async jobs use the 180s default since `BackgroundTasks` returns immediately.

### One-time setup (Blocker A)

```bash
gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen
```

### Shared variables

```bash
BACKEND_URL="https://fis-backend-136029935063.us-central1.run.app"
SCHEDULER_SA="136029935063-compute@developer.gserviceaccount.com"
```

### Job 1 — filing-monitor-hourly (sync, 1800s deadline)

```bash
gcloud scheduler jobs create http filing-monitor-hourly \
    --schedule="0 * * * *" \
    --time-zone="UTC" \
    --uri="$BACKEND_URL/api/v1/pipeline/run/filing-monitor" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{}' \
    --oidc-service-account-email="$SCHEDULER_SA" \
    --oidc-token-audience="$BACKEND_URL" \
    --attempt-deadline=1800s \
    --location=us-central1 \
    --project=fis-lead-gen \
    --description="Catches new SEC filings every hour → /alerts"
```

### Job 2 — populate-all-weekly (async, 180s default)

```bash
gcloud scheduler jobs create http populate-all-weekly \
    --schedule="0 2 * * 0" \
    --time-zone="UTC" \
    --uri="$BACKEND_URL/api/v1/pipeline/run/populate-all" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{}' \
    --oidc-service-account-email="$SCHEDULER_SA" \
    --oidc-token-audience="$BACKEND_URL" \
    --location=us-central1 \
    --project=fis-lead-gen \
    --description="Weekly full enrichment refresh — Sunday 02:00 UTC"
```

### Job 3 — initial-load-weekly (async, 180s default)

```bash
gcloud scheduler jobs create http initial-load-weekly \
    --schedule="0 6 * * 0" \
    --time-zone="UTC" \
    --uri="$BACKEND_URL/api/v1/pipeline/run/initial-load" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{}' \
    --oidc-service-account-email="$SCHEDULER_SA" \
    --oidc-token-audience="$BACKEND_URL" \
    --location=us-central1 \
    --project=fis-lead-gen \
    --description="Weekly catch-up of newly-registered broker-dealers from FINRA"
```

### Verify all 3 jobs exist + are enabled

```bash
gcloud scheduler jobs list --location=us-central1 --project=fis-lead-gen \
    --filter="name~filing-monitor-hourly OR name~populate-all-weekly OR name~initial-load-weekly" \
    --format="table(name.basename(),schedule,state,httpTarget.uri)"
```

Expect 3 rows, all `state=ENABLED`.

## Smoke test (post-provisioning)

> **Note on filing-monitor wait time.** Because `filing-monitor` is sync (~5–15 min), a smoke run blocks the request the entire time. Use a 600–900s `--freshness` window when reading the AttemptFinished log; do not give up at 30s.

```bash
gcloud scheduler jobs run filing-monitor-hourly \
    --location=us-central1 --project=fis-lead-gen

# Poll AttemptFinished (may take 5–15 min for filing-monitor to return):
until gcloud logging read \
    'resource.type="cloud_scheduler_job" AND resource.labels.job_id="filing-monitor-hourly" AND jsonPayload."@type"="type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished"' \
    --limit=1 --project=fis-lead-gen --freshness=20m \
    --format="value(timestamp,severity,jsonPayload.status,jsonPayload.debugInfo)" \
    | grep -E "[0-9]"; do sleep 15; done

# Backend-side confirmation (look for HTTP 200 inbound POST):
gcloud logging read \
    'resource.type="cloud_run_revision" AND resource.labels.service_name="fis-backend" AND textPayload:"filing-monitor"' \
    --limit=10 --project=fis-lead-gen --freshness=20m \
    --format="value(timestamp,textPayload)"
```

Expected: scheduler log shows `URL_CRAWLED. Original HTTP response code number = 200`, backend log shows `INFO: ... "POST /api/v1/pipeline/run/filing-monitor HTTP/1.1" 200 OK`. The 2026-04-30 activation smoke produced exactly this — see Activation log above. If anything else (`401`, `403`, `404`, `URL_REJECTED`), pause the job:

```bash
gcloud scheduler jobs pause filing-monitor-hourly \
    --location=us-central1 --project=fis-lead-gen
```

## Disable / pause / resume / modify / delete

```bash
# Pause one job (stops execution, preserves config)
gcloud scheduler jobs pause <name>  --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs resume <name> --location=us-central1 --project=fis-lead-gen

# Change cadence
gcloud scheduler jobs update http <name> \
    --schedule="<new cron>" \
    --location=us-central1 --project=fis-lead-gen

# Tear down
gcloud scheduler jobs delete filing-monitor-hourly --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs delete populate-all-weekly   --location=us-central1 --project=fis-lead-gen
gcloud scheduler jobs delete initial-load-weekly   --location=us-central1 --project=fis-lead-gen
```

## Architectural follow-up — when does Tier 2 become belt-and-suspenders?

After the streaming Files API path (#23 phase 2) ships and the prod flag is flipped, the weekly `populate-all` job will run against fresh-from-SEC data with no on-disk PDF cache to go stale. At that point the architectural cause of the staleness is fully resolved (no cache → no cache rot), and the cron jobs become belt-and-suspenders rather than the primary defense. Tier 2 is still worth shipping for two reasons:

1. **`filing-monitor-hourly`** — `/alerts` freshness is independent of the PDF cache; new SEC filings still need to be detected on a cadence regardless of the Files API path.
2. **`initial-load-weekly`** — newly-registered broker-dealers only enter the system through `initial_load`, which queries FINRA's roster. Without scheduling, new BDs would silently never appear.

`populate-all-weekly` is the one that becomes redundant once Files API streaming is the default. Keep it as a safety net for now; consider dropping cadence to monthly or removing entirely once the Files API path has 60+ days of clean prod telemetry.

## Sequencing — completed

1. **BE PR** — DONE. Shipped `POST /api/v1/pipeline/run/{filing-monitor,populate-all,initial-load}` family with `_ensure_admin_or_scheduler_sa` dual auth in PR #189 (squash-merged 2026-04-29 at `f1e4f660`), promoted to main via PR #190 at `aeba7fa2`. Live on `fis-backend-00089-l7l`.
2. **Infra step** — DONE. `gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen` ran 2026-04-30T00:14:45Z.
3. **Provision step** — DONE. Three jobs created 2026-04-30T00:14:55Z–00:15:23Z, URI/audience corrected at 00:20:13Z–00:20:33Z, deadline corrected at 00:27:26Z. `filing-monitor-hourly` smoked successfully at 00:39:35Z (HTTP 200, ~12 min wall). All three `state=ENABLED`.
4. **Runbook updated** — DONE. This commit.

## Tier 1 (manual cache-bust) — emergency-only path

The Tier 1 manual recovery path remains documented elsewhere and is unchanged. It is the right tool when:

- A specific broker-dealer's data is wrong *now* and the next scheduled run is too far away.
- A pipeline upstream (FINRA, SEC EDGAR, Gemini) had a transient outage and the next scheduled retry would be too late.

Tier 1 is *not* the right tool for routine staleness recurrence — that's what Tier 2 fixes.
