# Fresh Regen modal — Phase 0 (Files API toggle)

**Date:** 2026-04-30
**Branch:** `feature/fe-fresh-regen-files-api-toggle`
**Worktree:** `C:\Users\DSWDSRV-CARAGA\Desktop\Projects\fis-lead-gen-frontend`
**Pairs with BE PR:** `feature/be-pipeline-set-files-api-flag` (cli01)

## Goal

Eliminate the manual `gcloud run services update` step Arvin previously
had to run before the Fresh Regen button. The toggle defaults ON; when
ON, the regen now runs 4 phases instead of 3:

| Phase | Action | ETA |
|-------|--------|-----|
| 0 | `POST /api/v1/pipeline/set-files-api-flag` (flips `LLM_USE_FILES_API=true` and waits for the BE Cloud Run revision to roll out) | ~60–90s |
| 1 | `POST /api/v1/pipeline/wipe-bd-data` | seconds |
| 2 | `POST /api/v1/pipeline/run/initial-load` + poll | ~15–30 min |
| 3 | `POST /api/v1/pipeline/run/populate-all` + poll | ~30–90 min |

When the admin unchecks the toggle, Phase 0 is skipped and the legacy
3-phase flow runs unchanged.

## Files changed

- `frontend/lib/api.ts` — added `SetFilesApiFlagResponse` type and
  `setFilesApiFlag(enabled)` helper.
- `frontend/components/settings/pipelines/fresh-regen-confirm-modal.tsx`
  - new `Stage` value `"files_api_flipping"`
  - new state: `useFilesApi` (default `true`), `filesApiResult`
  - `handleSubmit` runs Phase 0 first when `useFilesApi`
  - `TypingBody` renders a styled native checkbox toggle
  - `buildPhases` conditionally prepends a Phase 0 row
  - new `buildFilesApiErrorMessage` helper for 503 / 403 mapping
- (No changes needed to `regen-progress.tsx` — already generic over the
  phase array.)

## Error handling — Phase 0

| Status | Surfaced message |
|--------|------------------|
| 503 | "Could not enable Files API (Cloud Run rollout timed out). Try again, or proceed without streaming by unchecking the toggle." |
| 403 | "Admin access required." |
| other 4xx/5xx | "Failed to enable Files API: \<BE detail\>" |

A Phase 0 failure rewinds the modal to the typing stage and surfaces
the message inline; the wipe / loads never run, so no destructive
action happens. The admin can either retry, or untick the toggle to
proceed with the legacy 3-phase path.

## Phase 0 in-flight UX

- Phase 0 row shows a spinning loader and the detail string
  "Rolling out new backend revision — please wait, this is not hung."
- Phases 1–3 stay `pending` (greyed out) until Phase 0 reports `done`.
- The "Close (regen continues server-side)" link is shown during Phase
  0 too — `inFlight` includes `files_api_flipping`.

## Coordination with cli01

- This PR is opened DRAFT.
- It will be marked `ready` only after cli01's
  `feature/be-pipeline-set-files-api-flag` merges to `develop`.
- After merge, this branch rebases onto `develop`, runs lint + build
  again, then squash-merges to `develop`.
- Phase B coordination: if a release PR base=main head=develop is
  already open, ride it; otherwise open a new one promoting both PRs
  together.

## Reminder for Arvin

The IAM grant for the new BE endpoint still needs to be run ONCE
manually before Phase 0 will succeed in production. cli01's PR body
documents the exact `gcloud run services add-iam-policy-binding`
command. Without it, Phase 0 returns 403 and the toggle's error
message points the admin at that step.
