# /my-favorites empty-state + loading polish (FE)

**Date:** 2026-04-29
**Branch:** `feature/fe-my-favorites-empty-states`
**Issue:** UX polish on the favorites page after #17 phase 1+2 shipped.

---

## Scope

Polish only. No backend changes. No master-list, firm-detail, or shared
UI primitive changes. All work limited to
`frontend/components/my-favorites/**`.

## Problems

After #17 phases 1+2:

1. **Zero-items empty state** — when a user creates a new list (or
   empties an existing one), the items pane shows a bare
   "No firms in this list yet" card with no path forward. There is no
   actionable CTA pointing the user to where firms can be added.
2. **Loading shape divergence** — the items-pane and sidebar skeletons
   both use `animate-pulse` blocks, but they are generic flat rectangles
   rather than column-faithful skeletons that mimic the eventual
   row content.
3. **Default-list tooltip** — copy is fine in length and tone; no
   change needed.

## Existing patterns reused

- **Empty-state CTA style** — matches the gradient pill CTA already
  shipped in `frontend/components/visited-firms/visited-firms-client.tsx`
  (`Browse the master list` link with `from-[#6366f1] to-[#8b5cf6]`
  gradient). This keeps two adjacent surfaces visually consistent.
- **Skeleton classes** — `animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]`
  matches `frontend/components/master-list/master-list-workspace-client.tsx`
  (table cell skeletons) and `broker-dealer-detail-client.tsx`.
- **Lucide `Star` icon** — already used in the existing inline
  `EmptyState` and the sidebar `Default` badge.

No new shared primitives needed. `frontend/components/ui/` untouched.

## Changes

### 1. New: `frontend/components/my-favorites/empty-items-state.tsx`

Pulls the inline `EmptyState` out of `favorite-list-items-pane.tsx`,
adds a `Browse the master list` CTA `<Link>` matching the
visited-firms gradient pill, and accepts no props (display is invariant
across lists).

### 2. Edit: `frontend/components/my-favorites/favorite-list-items-pane.tsx`

- Drop the inline `EmptyState` function.
- Import `EmptyItemsState` from the new file.
- Tighten `ItemsSkeleton` to render a name + subtext + Review chip
  shape rather than a flat block, so the loading state previews the
  row shape it will resolve to.

### 3. Edit: `frontend/components/my-favorites/favorite-lists-sidebar.tsx`

- Tighten the loading skeleton rows to render a name skeleton + count
  pill skeleton, mirroring the actual row layout. Still
  `h-[44px]`-equivalent, still `bg-[var(--surface-2,#f1f6fd)]`.

### Not changed

- `list-row-menu.tsx` — `DISABLED_TOOLTIP` copy is already concise and
  matches phrasing used elsewhere; no edit.
- `my-favorites-client.tsx` — left as-is; the duplicated transitional
  skeleton block (lines 254-262) is short-lived and consistent with
  the items-pane skeleton. Touching it would push us into orchestration
  changes outside this PR's scope.

## Verification

- `npm run lint` clean.
- `npm run build` clean.
- After deploy, on a fresh account / freshly-created empty list:
  navigating to `/my-favorites` shows the new empty card with a
  `Browse the master list` CTA that routes to `/master-list`.
- Switching lists rapidly: the items-pane skeleton previews row
  layout (name + subtext + Review chip) instead of a flat block.

## Out of scope

- Phase 3 list-picker (cli02 territory).
- Backend changes (no BE PRs).
- Default-list tooltip rewording.
