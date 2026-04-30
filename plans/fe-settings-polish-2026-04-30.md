# /settings visual polish — 2026-04-30

Pure-visual pass on the `/settings` admin surface. No logic, no API, no
schema changes. The page was visually out of step with `/dashboard` and
`/master-list`: heavy 30px-radius "shell" cards on a fixed-color
(`text-navy` / `bg-white/92`) palette, no dark-mode support, plain
range-slider styling, no pulsing affordance on a Running run, and a
wall-of-text Recent failures block.

This PR adopts the modern card system already in use on `/dashboard`,
`/alerts`, `/my-favorites`: `rounded-2xl` soft cards on the CSS-var
palette (`var(--surface)`, `var(--text)`, `var(--border)`,
`var(--shadow-card)`, `var(--accent)`, etc.), with status pills colored
per state and a stacked failure-card list.

## Files touched (write list per cli04 brief)

- `frontend/components/settings/pipeline-admin-client.tsx`
- `frontend/app/(app)/settings/page.tsx`
- `plans/fe-settings-polish-2026-04-30.md` (this file)

Out of scope (intentionally untouched):
- `frontend/components/settings/pipelines/**` (just shipped)
- `frontend/components/settings/users-admin-client.tsx` + `frontend/app/(app)/settings/users/**` (admin approval gate)
- `frontend/components/ui/**`, `frontend/lib/api.ts`, `frontend/middleware.ts`, `frontend/app/api/**`

## Class-by-class swap

| Surface | Before (old "shell" tokens) | After (dashboard / alerts tokens) |
|---|---|---|
| Soft card wrapper | `rounded-[30px] border-white/80 bg-white/92 p-8 shadow-shell` | `rounded-2xl border-[var(--border,…)] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,…)]` |
| Page H1 | `text-2xl font-semibold text-navy` | `text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]` |
| Eyebrow | `text-sm font-medium uppercase tracking-[0.24em] text-blue` | `text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,…)]` |
| Card title | _(none — implicit)_ | `mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,…)]` |
| Body text | `text-sm leading-6 text-slate-600` | `text-[13px] leading-5 text-[var(--text-dim,#475569)]` |
| Primary button | `rounded-2xl bg-navy px-5 py-3 text-white` | `rounded-xl bg-[var(--accent,#6366f1)] px-4 py-2.5 text-white shadow-[…/0.35]` (matches /my-favorites CTA) |
| Secondary | `rounded-2xl bg-blue px-5 py-3 text-white` | `rounded-xl border border-[var(--border-2,…)] bg-transparent text-[var(--text,…)]` |
| Tertiary | `rounded-2xl border-slate-200 px-5 py-3 text-slate-700` | `rounded-xl px-4 py-2.5 text-[var(--text-dim,…)] hover:bg-[var(--surface-2,…)]` |
| Status pill: Running | _(none)_ | `bg-blue-500/12 text-[var(--pill-blue-text,#1d4ed8)] border-blue-500/25` + animated dot |
| Status pill: Completed | _(none)_ | `bg-emerald-500/12 text-[var(--pill-green-text,#047857)] border-emerald-500/25` |
| Status pill: Failed | _(none)_ | `bg-red-500/12 text-[var(--pill-red-text,#b91c1c)] border-red-500/25` |
| Status pill: Idle | _(none)_ | `bg-slate-100 text-slate-600 border-slate-200` |
| Range slider | _browser default_ | `accent-[var(--accent,#6366f1)]` (filled track + thumb in brand color) |
| Failure card | inline paragraph | `border-l-4 border-l-red-500/40` card with title row (partner, type pill, BD #) + collapsible note (line-clamp-3 + Show more / Show less) |
| Recent failures empty | `"No flagged extractions."` plain text | `EmptyState` with green `CheckCircle2`: "No failures in the latest run" |
| Latest Run empty (no run yet) | `"No pipeline runs recorded yet."` plain text | `EmptyState` with `Inbox` icon: "No pipeline run yet" + pointer to actions |
| Total weight | `bg-slate-50 px-4 py-3 text-sm` | bordered row with green/red pill, `CheckCircle2` / `AlertCircle` icon |
| Save scoring | enabled regardless of total | disabled when total ≠ 100, with explanatory red note |
| Layout | `xl:grid-cols-[1.1fr_0.9fr]` (stacked until 1280px) | `md:grid-cols-2` (stacked < md, 2-col ≥ 768px per brief) |

## Brief items addressed

- **(a) Page header** — H1 + crumb-style eyebrow + subtitle in the dashboard typography stack (`text-[24px] font-bold tracking-[-0.02em]`).
- **(b) Admin Controls** — soft-card wrapper. Refresh data = primary (filled brand). Refresh FINRA details = secondary (outline). Retry failed = tertiary (ghost). All three swap their leading icon to `Loader2` with `animate-spin` while `isPending` is true.
- **(c) Latest Run** — soft-card wrapper. Status pill in the header pulses when Running; recolored per Idle / Running / Completed / Failed (matches /alerts severity treatment).
- **(d) Recent failures** — converted from a paragraph wall to a stacked list of `FailureCard`s. Each card: title row (partner + clearing type tag + BD #), red-tinted left border, collapsible body with `line-clamp-3` default and a `Show more` / `Show less` toggle when the note exceeds ~160 chars.
- **(e) Empty states** — added for "no run yet" (Inbox icon, points to actions) and "no failures" (green CheckCircle2). Mirrors the empty-state vocabulary already in `/my-favorites/empty-items-state.tsx`.
- **(f) Scoring Weights** — `accent-[var(--accent,…)]` styles the slider track + thumb. Percentages right-aligned in `font-mono text-xs`. Total renders as a pill: green `CheckCircle2` at 100%, red `AlertCircle` otherwise. Save button disabled when total ≠ 100, with a small red explanatory note below.
- **(g) Spacing / hierarchy** — `space-y-6` between cards, `p-6` inside, eyebrow style normalized to the dashboard `text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,…)]`. Card titles consistent at `text-[15px] font-semibold tracking-[-0.01em]`.
- **(h) Responsive** — Latest Run / Scoring Weights stack < md (768 px), 2-col ≥ md.

## Verification

- `npm run lint` — clean (one pre-existing warning in `export-client.tsx`, unchanged here).
- `npm run build` — green; `/settings` route 6.81 kB / 94.1 kB First Load JS.
- Dark-mode safe: every color reaches through CSS vars (`var(--text)`,
  `var(--surface)`, `var(--border)`, `var(--shadow-card)`,
  `var(--accent)`, `var(--pill-*-text)`), with the `[data-theme="dark"]`
  ladder already wired in `globals.css`.

## Out-of-scope decisions (deliberately not done)

- Did not extract per-card components into separate files. The brief is
  a pure visual pass and the existing `pipeline-admin-client.tsx`
  centralises related state — splitting it would expand the diff and
  invite logic regressions for zero visual gain. Helper components
  (`Tile`, `EmptyState`, `FailureCard`, `FieldLabel`, `CompetitorEditor`)
  live in the same file alongside the parent.
- Did not touch the existing `buildApiPath` import (unused, but
  pre-existing). Cleaning unused imports is a separate refactor pass.
- Did not change any `apiRequest(...)` paths, body shapes, or response
  types. Save / refresh / retry / create-competitor flows behave
  identically.
