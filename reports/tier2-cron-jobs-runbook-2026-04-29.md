# Tier 2 — Cloud Scheduler cron jobs (planned, blocked on BE follow-up)

**Authored:** 2026-04-29
**Project:** `fis-lead-gen`
**Region:** `us-central1`
**Intended invoker SA:** `136029935063-compute@developer.gserviceaccount.com` (same SA the frontend uses to invoke `fis-backend` — already has `roles/run.invoker` on the backend service).
**Intended audience:** `https://fis-backend-saxzdkn5nq-uc.a.run.app`

> **Status:** *Not yet provisioned.* During scope-discovery on 2026-04-29 we found two blockers (one infra, one auth) that have to be resolved before the jobs can be created. This runbook captures the planned design, the blockers, the follow-up work needed, and the operational commands to use once Tier 2 actually ships.

## Why this exists

Pre-2026-04-29, the data pipeline only ran when someone manually triggered `python -m scripts.populate_all_data` from a workstation. Result: prod data drifted 1-2 months stale, flagged by Deshorn on 2026-04-27. Tier 1 (manual cache-bust + re-run) restored fresh data for that complaint. Tier 2 (this runbook) is the structural fix — scheduled jobs that run the pipeline on a fixed cadence with no human in the loop.

## Planned jobs

| Name | Schedule (UTC) | Cadence rationale | Target endpoint (intended) |
|---|---|---|---|
| `filing-monitor-hourly` | `0 * * * *` | Catch new SEC filings the same business day they post → drives `/alerts` freshness | `POST /api/v1/alerts/monitor/run` |
| `populate-all-weekly` | `0 2 * * 0` | Weekly full enrichment refresh — Sunday 02:00 UTC (lowest-traffic window for Neon) | *(no endpoint — see Blocker B)* |
| `initial-load-weekly` | `0 6 * * 0` | Weekly catch-up of newly-registered broker-dealers from FINRA — runs after `populate-all` so new BDs land before the next weekday | *(no endpoint — see Blocker B)* |

## Blocker A — Cloud Scheduler API not enabled

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

## Blocker B — Admin endpoints are session-cookie-gated, Scheduler can't supply one

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

## Provisioning commands (for when both blockers clear)

These are kept as reference — do **not** run them today.

### One-time setup (Blocker A)

```bash
gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen
```

### Job 1 — filing-monitor-hourly

```bash
BACKEND_URL="https://fis-backend-saxzdkn5nq-uc.a.run.app"
SCHEDULER_SA="136029935063-compute@developer.gserviceaccount.com"

gcloud scheduler jobs create http filing-monitor-hourly \
    --schedule="0 * * * *" \
    --time-zone="UTC" \
    --uri="$BACKEND_URL/api/v1/internal/scheduled/filing-monitor" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{}' \
    --oidc-service-account-email="$SCHEDULER_SA" \
    --oidc-token-audience="$BACKEND_URL" \
    --location=us-central1 \
    --project=fis-lead-gen \
    --description="Catches new SEC filings every hour → /alerts"
```

### Job 2 — populate-all-weekly (Sunday 02:00 UTC)

```bash
gcloud scheduler jobs create http populate-all-weekly \
    --schedule="0 2 * * 0" \
    --time-zone="UTC" \
    --uri="$BACKEND_URL/api/v1/internal/scheduled/populate-all" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{}' \
    --oidc-service-account-email="$SCHEDULER_SA" \
    --oidc-token-audience="$BACKEND_URL" \
    --location=us-central1 \
    --project=fis-lead-gen \
    --description="Weekly full enrichment refresh — Sunday 02:00 UTC"
```

If `populate-all` ends up on Cloud Run Jobs instead of the service, the `--uri` shape becomes a `run.googleapis.com/v2/projects/.../jobs/...:run` URL. Capture the actual shape during the BE PR.

### Job 3 — initial-load-weekly (Sunday 06:00 UTC)

```bash
gcloud scheduler jobs create http initial-load-weekly \
    --schedule="0 6 * * 0" \
    --time-zone="UTC" \
    --uri="$BACKEND_URL/api/v1/internal/scheduled/initial-load" \
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

```bash
gcloud scheduler jobs run filing-monitor-hourly \
    --location=us-central1 --project=fis-lead-gen

# Wait ~30s, then check the scheduler-side log:
gcloud logging read \
    'resource.type="cloud_scheduler_job" AND resource.labels.job_id="filing-monitor-hourly"' \
    --limit=5 --project=fis-lead-gen --format=json

# And the backend-side log:
gcloud run services logs read fis-backend --region=us-central1 \
    --project=fis-lead-gen --limit=30 \
    | grep -i "scheduled\|filing-monitor"
```

Expected: scheduler log shows `200`/`202` from the backend, backend log shows the inbound POST + filing-monitor service kicked off. If anything else (`401`, `403`, `404`), pause the job:

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

## Sequencing

1. **BE PR** — Add `/api/v1/internal/scheduled/{kind}` family (or Cloud Run Jobs equivalents). OIDC-token auth, no session cookie. Includes tests for token validation. *(Out of scope for this Tier 2 docs PR — separate ticket.)*
2. **Infra step** — `gcloud services enable cloudscheduler.googleapis.com --project=fis-lead-gen`. *(One-line, but counts as infra; do once the BE PR is merged + deployed.)*
3. **Provision step** — Run the three `gcloud scheduler jobs create http` commands above. Verify with `jobs list`. Smoke `filing-monitor-hourly` first.
4. **Update this runbook** — once the jobs exist, update the "Status" line at the top to `Provisioned YYYY-MM-DD`, fill in the actual smoke result, and remove the "do not run today" caveat from the provisioning section.

## Tier 1 (manual cache-bust) — emergency-only path

The Tier 1 manual recovery path remains documented elsewhere and is unchanged. It is the right tool when:

- A specific broker-dealer's data is wrong *now* and the next scheduled run is too far away.
- A pipeline upstream (FINRA, SEC EDGAR, Gemini) had a transient outage and the next scheduled retry would be too late.

Tier 1 is *not* the right tool for routine staleness recurrence — that's what Tier 2 fixes.
