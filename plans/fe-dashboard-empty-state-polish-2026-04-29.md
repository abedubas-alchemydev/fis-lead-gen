# /dashboard — empty-state + loading + error polish

**Date:** 2026-04-29
**Branch:** `feature/fe-dashboard-empty-state-polish`
**Scope:** Pure FE polish of `/dashboard` to match the consistency bar
shipped earlier today on `/my-favorites`, `/alerts`, `/visited-firms`,
and `/export`. No new BE endpoints; no shared primitives.

## Today's reference pattern (sibling pages)

Empty-state visual contract (used in
`empty-alerts-state.tsx`, `empty-visited-state.tsx`,
`empty-items-state.tsx`, `empty-export-matches-state.tsx`):

- Outer: `rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center`
- Icon medallion: `mx-auto grid h-14 w-14 place-items-center
  rounded-full bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]`
  with a Lucide icon `h-6 w-6 strokeWidth={1.75}`
- Title: `mt-5 text-[15px] font-semibold tracking-[-0.01em]`
- Body: `mt-2 max-w-sm text-[13px] leading-5 text-text-dim`
- Optional CTA: gradient pill
  `bg-gradient-to-br from-[#6366f1] to-[#8b5cf6]`

Loading-skeleton contract (used in
`alerts-loading-skeleton.tsx`, `visited-loading-skeleton.tsx`):

- Wrapper carries `aria-busy`
- Shapes use `animate-pulse rounded… bg-[var(--surface-2,#f1f6fd)]`
- Each row mirrors the real row's shape so layout stays stable

Error contract (already in `top-leads-card.tsx`,
`lead-volume-trend-card.tsx`, `alert-feed-card.tsx`): inline red banner.
Polish needed: add a Retry control where a refetch can be triggered.

## Audit of existing dashboard tiles

| Tile | Loading today | Empty today | Error today | Gap |
|------|---------------|-------------|-------------|-----|
| Page wrapper | Full-page spinner (blocks all chrome) | n/a | Banner under KPI grid | Drop spinner; let chrome render and per-tile skeletons appear |
| KpiCard ×4 (stats) | Renders `"-"` placeholder | n/a | Soft helper text | Add skeleton variant; show clear error state under the row |
| ClearingDistributionChart | (Spinner blocks) | Empty card with copy ✓ | None | Add loading skeleton + error card with Retry |
| LeadVolumeTrendCard (self-fetch) | Empty SVG | Implicit (empty SVG) | Inline red banner | Add chart-shape skeleton + error Retry |
| TopLeadsCard (self-fetch) | Skeleton 5 rows ✓ | Bare-style empty card | Inline red banner | Upgrade empty card to today's pattern; add error Retry |
| AlertFeedCard | Skeleton 4 rows ✓ | Bare-style empty card | Inline red banner | **OFF-LIMITS** — `frontend/components/alerts/**` forbidden |

## Design decisions

### Drop the page-level spinner

The current `pageLoading` gate hides the entire dashboard until *all*
three parent fetches resolve. That defeats the purpose of per-tile
skeletons. Removing it lets the page chrome (breadcrumb, title,
TopActions) render immediately while each tile renders its own
skeleton, error, or data — same UX as `/alerts`, `/visited-firms`, and
`/export` shipped today.

### Per-source loading/error/retry

The dashboard fetches three independent sources:

1. `/api/v1/stats` — drives the 4 KPI cards
2. `/api/v1/stats/clearing-distribution` — drives the chart
3. `/api/v1/alerts?page=1&limit=6` — drives the activity feed

Plus two self-fetching children (`TopLeadsCard`, `LeadVolumeTrendCard`).

To enable per-tile retry without a major refactor:

- Lift each parent fetch into its own state slice (`statsState`,
  `distributionState`, `alertsState`) with a stable `reload` callback
  exposed via `useCallback`.
- Pass `loading`, `error`, and `onRetry` props into the prop-based
  tiles (`KpiCard*`, `ClearingDistributionChart`).
- For self-fetching tiles, expose a `reload` trigger inside the
  component and render a Retry button next to the error banner.
- `AlertFeedCard` is off-limits this PR — leave its existing
  loading/empty/error rendering untouched. Its fetch remains in the
  parent for now.

### Skeletons sized to the real layout

Skeleton shapes mirror the actual row to avoid layout shift when data
arrives. KPI skeletons match the card geometry (icon chip + label +
big number + helper line + sparkline placeholder). Trend skeleton
shows ghosted axis + placeholder line. Distribution skeleton shows 5
rows shaped like the real bar grid. Top-leads already does this.

## Per-tile spec

### 1. KPI cards (stats)

Add `KpiCardSkeleton` mirroring KpiCard geometry:
- 36×36 icon-chip placeholder (tone-tinted `surface-2`)
- 11px label placeholder (`w-28 h-3`)
- 34px value placeholder (`w-20 h-9`)
- 12px helper placeholder (`w-40 h-3`)
- 36-tall sparkline placeholder

When `statsLoading`: render 4 `KpiCardSkeleton` (one per slot)
When `statsError`: render compact error card spanning the KPI row
with a Retry button. KPI grid stays in DOM but content is replaced.

### 2. ClearingDistributionChart

Accept `loading`, `error`, `onRetry` props.
- `loading`: skeleton with header + 5 ghost rows (swatch + label
  cluster + bar track + percent placeholder)
- `error`: error medallion card with copy "Couldn't load clearing
  distribution" + Retry button
- empty: existing copy ✓
- data: existing render

### 3. LeadVolumeTrendCard

Adds a `reload` trigger and renders:
- `loading` (replacing the empty SVG): chart-shape skeleton — header
  block, 4 ghost grid lines, 2 ghost line placeholders, 4 ghost axis
  labels, 2 ghost legend swatches.
- `error`: existing inline banner + a Retry button next to it.
- data: existing render.

### 4. TopLeadsCard

Already has skeleton + empty + error. Polish:
- Replace bare-dashed empty state with today's medallion pattern
  (Target icon, "No high-value leads yet", body copy reusing existing
  text, link to `/master-list?lead_priority=hot`).
- Add Retry button to the error banner.

### 5. AlertFeedCard

**OFF-LIMITS.** No changes. Dashboard continues to pass `loading`,
`error`, `alerts`, `onMarkRead`. Note: the existing bare-dashed empty
state in AlertFeedCard does not match today's pattern. This is a
known follow-up for the next time `/alerts` is in scope.

## Implementation order

1. Extract shared dashboard skeleton bits into
   `frontend/components/dashboard/dashboard-loading-skeleton.tsx`
   (KpiCardSkeleton, ClearingDistributionLoadingSkeleton).
2. Add `frontend/components/dashboard/empty-top-leads-state.tsx` for
   the medallion-style empty state on TopLeads.
3. Add `frontend/components/dashboard/dashboard-error-card.tsx` —
   compact error medallion + Retry button used by stats / distribution
   error states.
4. Update `top-leads-card.tsx`: lift `reload` callback, swap empty
   state, add Retry to error.
5. Update `lead-volume-trend-card.tsx`: lift `reload` callback, add
   chart skeleton, add Retry to error.
6. Update `clearing-distribution-chart.tsx`: accept `loading`,
   `error`, `onRetry` props with internal branching.
7. Update `dashboard-home-client.tsx`: split per-source state, drop
   full-page spinner, expose retry callbacks, render
   KpiCardSkeleton / KpiCardErrorRow when stats fail.
8. `npm run lint && npm run build` from `frontend/`.

## Non-goals

- No new BE endpoints (per CLI 04 brief).
- No shared `<Skeleton/>` primitive — reuse the inline `animate-pulse`
  pattern used by sibling pages.
- No changes outside `frontend/components/dashboard/**` and
  `frontend/app/(app)/dashboard/page.tsx`.
- No edit to `frontend/components/alerts/**` (AlertFeedCard).
