# FE polish: master-list "Clear filters" button (visibility + scope correction)

**Date:** 2026-04-29
**Branch:** feature/fe-master-list-clear-all-filters
**Worktree:** fis-lead-gen-frontend-2

## Current state (read from develop)

The master-list filter bar lives in
`frontend/components/master-list/master-list-workspace-client.tsx`
(not in a `list/` subdir — the CLI prompt assumed a structure that
doesn't exist). It already has:

- A `clearFilters()` function (line ~439) that calls
  `commit(MASTER_LIST_STATE_DEFAULTS)`. This wipes EVERY URL-state key,
  including `sortBy`, `sortDir`, `list`, and `limit`.
- A "Clear filters" button (line ~631) that is always rendered, even
  when no filters are active.
- An `activeFilterCount` derived value that counts filter keys but
  excludes `search` (likely an oversight when search was lifted into
  URL state).

## What this PR changes

### 1. New helpers in `frontend/lib/master-list-state.ts`

- `hasActiveFilters(state)` — returns `true` iff any filter key
  (search, state, health, leadPriority, clearingPartner, clearingType,
  typesOfBusiness, minNetCapital, maxNetCapital, registeredAfter,
  registeredBefore) differs from its default.
- `clearAllFilters(state)` — returns a new state with every filter
  key reset to its default and `page` reset to 1. Preserves `sortBy`,
  `sortDir`, `list`, `limit`, and `source` — those are workspace
  preferences, not filters.

### 2. Filter-bar wiring

- Conditionally render the "Clear filters" button only when
  `hasActiveFilters(queryState)` is `true`. The header layout is
  `flex justify-between`, so the heading stays put when the button
  is hidden.
- Replace the inline reset with `commit(clearAllFilters(queryState))`
  so sort, list mode, and page-size are no longer wiped.
- Include `search` in `activeFilterCount` so the "{N} ACTIVE" pill and
  the button-visibility predicate stay consistent.
- Add a Lucide `X` icon (`h-3.5 w-3.5`, `aria-hidden`) inside the
  button beside the "Clear filters" label. Reuses the same ghost
  styling already on the button — no new variant.

## Visibility rule

Show the button when ANY of:
- `state !== ""`
- `search !== ""`
- `health !== "All"`
- `leadPriority !== "All"`
- `clearingPartner !== ""`
- `clearingType !== "All"`
- `typesOfBusiness.length > 0`
- `minNetCapital !== null`
- `maxNetCapital !== null`
- `registeredAfter !== null`
- `registeredBefore !== null`

## What this PR does NOT touch

- Backend code.
- Sort, list-mode tab, or page-size selector behavior.
- Per-tag dismiss chips ("Active" row).
- Any other filter component (cli01-#138 net-capital range, #123
  registration-date range etc. stay unchanged).
- `master-list/detail/**` (cli02 territory).

## Verification

- `npm run lint` clean.
- `npm run build` clean.
- Manual smoke (recommended): visit `/master-list` with no filters →
  no button. Set state=Florida → button appears. Set sort=Last
  Filing, page-size=50, click button → filters clear, sort+page-size
  preserved, URL drops only filter params, list re-fetches.
