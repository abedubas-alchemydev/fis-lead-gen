# /export page UX polish — 2026-04-29

Pure FE polish on the `/export` CSV page. The PRD-locked behaviors
(9-column allowlist, 100-row cap, 3-exports/user/day cap, watermark
footer) are **not** changed. This PR tightens the surfacing of
existing server-side rules and fills in missing UX states.

## Current shape (before polish)

`frontend/components/export/export-client.tsx` (single file, ~298
lines) renders:

- A topbar with quota pill (`X of 3 exports remaining today`).
- A live-match strip showing matching record count.
- A filters card with three Segmented controls (List, Lead Priority,
  Financial Health).
- A flat error `<div>` (red) when `apiRequest` throws — uses
  `err instanceof Error ? err.message : ...`, so it loses
  `ApiError.status` and shows raw FastAPI `detail` text for every
  error class (cap, validation, generic).
- A Preview panel (matching / requested / remaining counters).
- A Rules panel with the gradient `Export CSV` button. Button label
  flips to `Preparing CSV…` while exporting; otherwise reads
  `Export CSV`.

What's missing:

- No empty state when `matching_records === 0` (filters yield no
  exportable firms). The Preview panel just shows `0` with no
  guidance.
- No specialized cap-exceeded copy. When the BE returns 429 (or
  `remaining_exports_today <= 0`), the user sees a raw error string
  and a generic disabled button.
- No success confirmation after the file downloads — the file just
  appears and the page returns to its neutral state.
- The button shows `Preparing CSV…` text but no spinner glyph; users
  can confuse it with a frozen UI.

## Plan

### A. Empty state — 0 matching firms

Add `frontend/components/export/empty-export-matches-state.tsx`
mirroring the `EmptyAlertsState` / `EmptyItemsState` rounded-2xl
pill on `surface-2`, with a `FileText` icon + copy:

> **No firms match these filters**
> Adjust the filters above to pick up firms. Each export ships up
> to 100 rows, includes a source watermark, and counts toward your
> 3 exports/day cap.

Renders inside the Preview `SectionPanel` instead of the three
counter tiles when `matching_records === 0`. Counters return when
filters yield matches.

### B. Form validation — surface BE errors with class

Currently every error renders as a flat red panel. Switch to
`ApiError`-aware classification:

- `status === 429` → cap-exceeded copy (Section D).
- `status >= 400 && status < 500` → keep `detail` but render with a
  warning tone + leading "Filter rejected:" label so users know it
  was their input the BE rejected.
- Any other failure → generic "Couldn't load export preview." /
  "Couldn't generate CSV." copy with the raw `detail` underneath.

No new client-side rules are added — the rules already live in
`backend/api/v1/export.py`, and we're just labelling what the BE
returned. Error state is dismissable via a small ✕ button so it
doesn't stay sticky after the user fixes filters.

### C. Loading state — spinner

Replace the bare `Preparing CSV…` text with a `Loader2` icon (the
`lucide-react` spinner already used elsewhere) + `Generating CSV…`
copy while the POST is in flight. Add `aria-busy="true"` to the
button. The disabled state is preserved.

### D. Cap-exceeded state

When `remaining_exports_today <= 0` OR a 429 is returned, render an
amber-outlined banner above the Export CSV button:

> **Daily cap reached** — You've used all 3 exports today.
> The cap resets at midnight UTC.

The Export CSV button stays disabled (already wired to
`quotaExhausted`). The pill in the topbar already turns red, so the
banner reinforces the message right next to the button.

### E. Success state

After a successful POST + download, set a transient
`successMessage` like:

> **Exported `firms-2026-04-29.csv`** — N rows. M exports left
> today.

Renders as a rounded-2xl green-tinted banner above the filters
card. Auto-dismisses after 6 seconds and is also clearable. Clears
itself when the user changes any filter.

## Files touched

- `frontend/components/export/export-client.tsx` — reworked error
  handling, added success/cap/empty state hooks, swapped button
  glyph.
- `frontend/components/export/empty-export-matches-state.tsx`
  *(new)* — empty-state pill for the 0-match case.
- `plans/fe-export-page-polish-2026-04-29.md` *(this file)*.

## Out of scope (explicit)

- 9-column allowlist (PRD-locked).
- 100-row cap (PRD-locked).
- 3-exports/day cap value (PRD-locked).
- Watermark footer (PRD-locked).
- Backend `/api/v1/export*` shape — untouched.
- New shared UI primitives (no `frontend/components/ui/**` writes).
- Other components: `master-list/**`, `list-picker/**`,
  `my-favorites/**`, `alerts/**`, `email-extractor/**`,
  `visited-firms/**`.

## Verification

- `npm run lint` and `npm run build` from `frontend/`.
- Manual: visit `/export`, exercise filters, confirm empty state
  fires when filters produce 0 matches, confirm cap banner fires
  when remaining drops to 0, confirm success banner fires after a
  download.
