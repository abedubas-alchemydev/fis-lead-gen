# /settings/pipelines admin UI — implementation plan

**Date:** 2026-04-29
**Branch:** `feature/fe-settings-pipelines-admin-ui`
**Pairs with:** cli01 BE PR `feature/be-pipeline-endpoints-tier2`
**Worktree:** `C:\Users\DSWDSRV-CARAGA\Desktop\Projects\fis-lead-gen-frontend`

## Goal

Ship an admin-only `/settings/pipelines` page that lets operators trigger the
three Tier 2 pipelines from inside the app instead of requiring SSH + python.
Useful for ad-hoc refreshes alongside the Cloud Scheduler cadence cli01 is
also setting up.

## Pipelines covered (cli01 contract)

| Action | Endpoint | ETA | Cadence |
|---|---|---|---|
| Filing Monitor | `POST /api/v1/pipeline/run/filing-monitor` | minutes | hourly Cloud Scheduler |
| Populate All | `POST /api/v1/pipeline/run/populate-all` | 30–90 min | weekly Sun 02:00 UTC |
| Initial Load | `POST /api/v1/pipeline/run/initial-load` | 15–30 min | weekly Sun 06:00 UTC |

Each returns `PipelineTriggerResponse` (already typed in `frontend/lib/types.ts`):

```ts
type PipelineTriggerResponse = {
  run_id: number;
  status: string;
  total_items: number;
  processed_items: number;
  success_count: number;
  failure_count: number;
};
```

Auth: cookie session for the admin path (this UI). Same admin-only gate as
`/settings/users` (server-side `getRequiredSession()` →
`session.user.role === "admin"`).

## Files

### Write

- `frontend/lib/api.ts` (extend) — typed helpers `runFilingMonitor`,
  `runPopulateAll`, `runInitialLoad`. All POST with `credentials: "include"`
  via existing `apiRequest` wrapper.
- `frontend/app/(app)/settings/pipelines/page.tsx` (new) — server component.
  Mirrors `/settings/users` admin-gate.
- `frontend/components/settings/pipelines/pipelines-admin-client.tsx` (new) —
  page-level client component. Renders 3 trigger cards + recent-runs table,
  manages dialog state, dispatches toasts on completion.
- `frontend/components/settings/pipelines/pipeline-trigger-card.tsx` (new) —
  one trigger card. Props: `name`, `description`, `cadence`, `eta`,
  `runAction`. Confirm button opens inline confirm dialog; on confirm POSTs
  and toasts run_id; cooldown disable to prevent double-click.
- `frontend/components/settings/pipelines/confirm-trigger-dialog.tsx` (new) —
  scoped confirm dialog (cannot reuse `my-favorites/delete-list-dialog`,
  forbidden path; cannot add to `components/ui/`, also forbidden).
  Centered card + backdrop + Esc + focus management. Same idiom as the
  existing my-favorites dialog so it feels consistent.
- `frontend/components/settings/pipelines/recent-runs-table.tsx` (new) —
  reads existing `GET /api/v1/pipeline/clearing` (returns
  `PipelineStatusResponse`), renders the latest five runs with status
  pill, processed/total, started_at, trigger_source. No new BE work in
  this PR.

### Forbidden

- `backend/**`, `scripts/**`, `fis-placeholder/**`
- All other `frontend/components/<feature>/**` outside `settings/pipelines/`
- `frontend/components/ui/**` (no new shared primitives)
- `frontend/middleware.ts`, `frontend/app/api/**`
- `prompts/**`, `docs/**`

### Style

Match existing settings rounded-shell idiom:
`rounded-[30px] border border-white/80 bg-white/92 p-8 shadow-shell`.
Uppercase tracking-tight section labels in blue. Status badges follow the
existing pipeline-admin-client palette (`text-success`, `text-danger`).

## UX flow

1. Admin lands on `/settings/pipelines` (entered via URL — no nav change in
   this PR; `app-shell` nav is forbidden).
2. Page header: "Pipelines" + subtext explaining triggers are async,
   long-running, and admin-only.
3. Three cards stacked vertically (or 1-up on mobile, 1-up on desktop) with:
   - Name, description, scheduled cadence, expected duration.
   - "Run now" primary button.
4. Click "Run now" → confirmation dialog appears centered with title
   "Trigger &lt;Name&gt;?" and a short reminder of duration. Two buttons:
   `Cancel` (focus default) and `Run now`.
5. On confirm:
   - POST to corresponding endpoint via typed client helper.
   - On success: toast `Pipeline started — run #<run_id>`, button enters
     5-second cooldown.
   - On error: toast error message from `ApiError.detail`; button re-enabled.
6. Below the cards: "Recent runs" panel rendering up to five most recent
   pipeline_run rows from existing `GET /api/v1/pipeline/clearing`.

## Coupling + auto-promote

DRAFT pattern (parallel-coupled to cli01):

1. Open as DRAFT immediately so the BE team sees the FE wiring.
2. Stub against the contract (typed helpers + endpoints), no live BE yet.
3. Poll cli01 BE PR `feature/be-pipeline-endpoints-tier2` until merged to
   `develop` (90s sleep loop).
4. On merge: rebase, push --force-with-lease, re-run lint + build, mark ready.
5. Phase A — CI green → squash-merge to develop with `--admin`.
6. Phase B coordination — check for an existing release PR (parallel CLIs
   may already have opened one); ride if present, otherwise open one.
7. Watch prod deploy, smoke `/api/v1/health`, capture frontend Cloud Run
   revision.

## Risk + mitigations

- **BE not yet merged when DRAFT lands** → typed helpers will 404 in staging
  until BE merges. Acceptable; admin can't reach `/settings/pipelines`
  before the operator runs Phase A anyway.
- **Double-click triggers two runs** → button cooldown (5s after success)
  plus disabled-during-pending guard.
- **Long-running runs block UI** → pipelines run async server-side. The FE
  POST returns `PipelineTriggerResponse` immediately with the new
  `run_id`; we surface that and stop polling. Recent runs table shows
  state on next manual refresh.
- **Admin gate bypass attempts** → server component checks
  `session.user.role === "admin"` before rendering anything; client never
  receives the trigger surface for non-admin sessions.

## Out of scope

- New shared UI primitives (`components/ui/**` is forbidden).
- Changes to nav/`app-shell.tsx` (forbidden).
- Polling for run completion (separate concern; recent-runs table is
  read-on-load).
- BE work (cli01 territory).
- `/dashboard` polish (cli04 territory).
- Classifier prompt tweaks (cli03 territory).

## Verify locally

```
cd frontend
npm run lint
npm run build
```
