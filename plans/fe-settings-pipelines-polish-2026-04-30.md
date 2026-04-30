# /settings/pipelines visual polish — 2026-04-30

Followup to PR #220 (/settings polish + content-container wrapper).
Applies the same design language to /settings/pipelines: var-token soft
cards, brand-accent primary on "Run now", status pills with colored
dots and pulse on Running, polished DESTRUCTIVE ZONE divider with the
red emphasis preserved, red-tinted Fresh Regen card with red
destructive button.

## Files touched (visual only)
- frontend/app/(app)/settings/pipelines/page.tsx
- frontend/components/settings/pipelines/pipelines-admin-client.tsx
- frontend/components/settings/pipelines/pipeline-trigger-card.tsx
- frontend/components/settings/pipelines/fresh-regen-card.tsx
- frontend/components/settings/pipelines/recent-runs-table.tsx
- frontend/components/settings/pipelines/confirm-trigger-dialog.tsx

## Confirmed untouched
- Regen orchestration logic (setFilesApiFlag, wipeBdData,
  runInitialLoad, runPopulateAll, runFilingMonitor, findPipelineRun
  call sites — all unchanged)
- Files API toggle behavior (`useFilesApi` state + flag handling)
- Phase-progress chain state machine (`Stage` union + transitions)
- Confirmation modal flow (FreshRegenConfirmModal + RegenProgress —
  both files completely untouched)
- API surface in `frontend/lib/api.ts` (forbidden path, not edited)

## Design language source of truth
- /settings post-PR #220 (`frontend/components/settings/pipeline-admin-client.tsx`)
- CSS-var tokens already defined in `frontend/app/globals.css`
  (`--accent`, `--surface`, `--border`, `--text`, `--text-dim`,
  `--text-muted`, `--shadow-card`, `--pill-*-text`)

## Per-card changes
- Page header: eyebrow breadcrumb (Workspace / Settings / Pipelines)
  + bold 24px title + 13px description, mirroring /settings.
- Container wrapper: `px-7 pb-12 pt-7 lg:px-9` on both render
  branches of page.tsx (admin-restricted view + admin tree).
- PipelineTriggerCard: var-token surface card, eyebrow cadence,
  brand-accent primary "Run now" with shadow ring, full-width on
  narrow viewports (`w-full ... sm:w-auto`).
- FreshRegenCard: red-tinted soft card with `border-l-4
  border-l-red-500/70`, var-token surface for the icon chip,
  red-600 destructive button. Danger affordance preserved per brief.
- DESTRUCTIVE ZONE divider: extra `mt-4 ... pt-6` breathing room,
  red dots flanking eyebrow text, red-500/30 hairline.
- RecentRunsTable: var-token surface card, status pill catalog
  (Running pulses blue, Completed green, Failed red, Idle slate)
  matching /settings + /alerts severity, font-mono pipeline name,
  truncated trigger column with `title` tooltip for long SA emails.
- ConfirmTriggerDialog: var-token surface card, brand-accent
  primary on the modal "Run now" so it reads consistently with the
  trigger card.
