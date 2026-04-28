# /alerts page — Form BD / Deficiency / All tabs

Sprint 4 task #18 FE half. The BE `category` param shipped in PR #122
and was promoted in PR #124.

## Goal

Deshorn flagged in the 2026-04-27 meeting that deficiency notices were
leading the alerts page and felt noisy. Form BD filings should be the
primary alert category; deficiency notices secondary.

## UI

A three-tab row sits above the filters card on `/alerts`:

  - **Form BD** (default; primary visual weight)
  - **Deficiency Notices**
  - **All Alerts**

Tab styling matches the master-list Primary / Alternative / All Firms
pattern in `frontend/components/master-list/master-list-workspace-client.tsx`
(gradient pill on the active tab, small numeric badge to the right of
each label).

## State shape

- Tab lives in URL search params: `?tab=form_bd|deficiency|all`.
- Default tab = `form_bd`. The default is **omitted** from the URL so
  bare `/alerts` shows Form BD with a clean URL — matches the
  master-list-state.ts convention from #9 where defaults are stripped.
- `router.replace` on tab click (NOT `push`) so tab switching does not
  pollute browser history — same semantics as master-list and the
  Deshorn-driven decision documented in
  `master-list-workspace-client.tsx:166-175`.

## Per-tab count badges

- Three `?category=X&limit=1` GETs fire on mount in parallel via
  `Promise.allSettled`. One failed count does not break the page —
  same resilience pattern as the master-list bootstrap fix earlier
  today (`master-list-workspace-client.tsx:320-408`).
- Counts are *unfiltered* category totals (they do not narrow by the
  user's priority / read-status filter selections). Same convention
  as the master-list Primary/Alternative/All counts. Users mentally
  compose: "of the X total Form BD alerts, my filters narrowed it to
  N".

## Existing filters

The form-type dropdown (`All / Form BD / Form 17a-11`), priority
segmented control, and read-status segmented control all stay as-is.
The tab is an additional filter dimension; the BE applies tab category
AND any explicit `form_type` AND priority AND read together.

If a user lands on Deficiency Notices and explicitly picks
`Form BD` from the dropdown, the result set is empty. That mirrors the
behavior of any other conflicting filter combo on master-list and is
addressable via the Clear Filters button. Out of scope to redesign
the filters card here.

## Files touched

- `frontend/app/(app)/alerts/page.tsx` — pass `tab` searchParam through
  to the client (alongside the existing `form_type` and `priority`).
- `frontend/components/alerts/alerts-client.tsx` — read/write `tab` to
  URL via `useSearchParams` + `router.replace`, send `category` to the
  fetch, render the tab row with badges.

## Out of scope

- BE changes (already shipped in #122 / #124).
- `mark-all-read` category scoping — the BE endpoint does not accept
  `category` yet. Users can still bulk-read within the existing
  `form_type` + `priority` window. Future BE PR if Deshorn asks for it.
- Any other page.
- Removing or restyling the existing filters card.
