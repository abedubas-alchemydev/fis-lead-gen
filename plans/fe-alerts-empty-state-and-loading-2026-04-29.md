# /alerts empty-state + loading + error polish (FE)

Date: 2026-04-29
Branch: feature/fe-alerts-empty-state-and-loading
Author: cli02 (FE-1)

## Goal

Bring `/alerts` up to the same UX bar that cli04 shipped on `/my-favorites`
earlier today. The page renders three new branches in addition to the
existing list view:

- Loading → skeleton rows that mimic the alert-row layout.
- Empty (zero alerts in the active filter set) → centered card matching
  the `/my-favorites` `EmptyItemsState` look.
- Initial-load error → centered "Couldn't load alerts" card with a
  Retry button.

No backend changes. No new shared UI primitives. Reuses existing Lucide
icons and `animate-pulse` skeleton patterns already in the codebase.

## Existing state of `/alerts`

`frontend/components/alerts/alerts-client.tsx` already had:

- A minimal three-line skeleton block in the list-card body
  (lines 514–526 before this PR). Visually thin and doesn't reflect the
  alert-row column shape (priority dot + pills + actions).
- A bare-text empty state ("No alerts match the current filters.") in a
  dashed-border block — functional but inconsistent with the new
  `/my-favorites` polish.
- A single `error` state used for both initial-load failures and
  inline action failures (`markRead`, `markAllRead`). On load failure it
  rendered as a small red banner above the list card.

## What ships in this PR

### 1. `frontend/components/alerts/empty-alerts-state.tsx` (new)

Centered card mirroring `EmptyItemsState` from `/my-favorites`:

- Surface-2 background, `rounded-2xl`, `px-6 py-12`, `text-center`.
- Icon disc: 14×14 surface-3 circle, BellOff Lucide icon (size 6,
  strokeWidth 1.75, `aria-hidden`).
- Heading: "No alerts to review".
- Subtext: "We'll surface new SEC filings here as they appear."
- No CTA — alerts populate automatically; nothing for the user to do.

### 2. `frontend/components/alerts/alerts-loading-skeleton.tsx` (new)

Six skeleton rows rendered inside the list card. Each row mimics the
real alert-row column shape so the layout doesn't visually jump when
the fetch resolves:

- Priority dot placeholder (2×2, mt-2, shrink-0).
- Pill placeholders (priority pill ~64px, form-type pill ~56px) on the
  top row plus a relative-time placeholder pushed right via `ml-auto`.
- Title placeholder (h-3.5, w-2/5).
- Summary placeholder (h-3, w-full and h-3, w-4/5 stacked).
- Two action-chip placeholders (h-6, w-[78px] / w-[72px]) on the
  bottom row.
- `aria-busy` on the wrapper.

### 3. Edits to `alerts-client.tsx`

- New state: `loadError: string | null` (initial-fetch failure only)
  and `reloadKey: number` (bump to force retry).
- Existing `error` state is now used **only** for action failures
  (`markRead`, `markAllRead`). Keeps the existing inline banner.
- `loadAlerts` catch sets `loadError` instead of `error`.
- The `queryPath` effect depends on `reloadKey` so Retry forces a fresh
  fetch.
- List-card body now branches:
  1. `loading` → `<AlertsLoadingSkeleton />`
  2. `loadError` → centered "Couldn't load alerts" card with Retry
     button (inline helper in this file — small, single-use).
  3. `items.length === 0` → `<EmptyAlertsState />`
  4. else → existing alert list.

## Files touched

- `frontend/components/alerts/alerts-client.tsx` (edit)
- `frontend/components/alerts/empty-alerts-state.tsx` (new)
- `frontend/components/alerts/alerts-loading-skeleton.tsx` (new)

## Out of scope

- Master-list, list-picker, my-favorites, email-extractor, visited-firms.
- `frontend/components/ui/**` — no new shared primitives. The existing
  `animate-pulse` Tailwind utility is the skeleton primitive in this
  codebase.
- Any backend, API, schema, or model change.

## Verification

- `npm run lint` and `npm run build` clean.
- After deploy:
  - Open `/alerts` filtered to a category with no alerts → centered
    BellOff empty card.
  - Throttle to slow 3G in DevTools → skeleton rows flash on initial
    load and on tab/category switch.
  - Block `/api/v1/alerts` in DevTools network tab → "Couldn't load
    alerts" card with Retry. Click Retry to refetch.
