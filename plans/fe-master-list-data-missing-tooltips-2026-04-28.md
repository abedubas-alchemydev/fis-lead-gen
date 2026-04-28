# Master-list data-missing tooltips

Date: 2026-04-28
Branch: `feature/fe-master-list-data-missing-tooltips`

## Why

The 2026-04-28 unknowns audit (`reports/master-list-unknowns-audit-2026-04-28.md`)
found that the "—" / "Unknown" cells on `/master-list` have three different
causes all rendering identically:

- Classifier bug (Clearing Type) — fixed by #19
- Pipeline gap (Financial Health, Lead Priority) — fixed by #22
- **Data missing** (CIK, Net Capital, YoY Growth, Last Filing Date) —
  legitimately unfillable by code

This task ships hover tooltips on the third bucket so users can tell the
difference between "broken", "pending", and "genuinely no data."

## Scope decision: component-level edits, native `title` attribute

### Why not the formatter

`frontend/lib/format.ts` returns plain strings (`"N/A"`, `"Not available"`,
`"$74.3M"`). It does not currently produce JSX, and the `"—"` glyph is
rendered inline in the table component, not in the formatter. Converting
formatters to JSX would be invasive and changes the call site contract for
every consumer.

### Why not a custom Tooltip primitive

`frontend/components/ui/` has no Tooltip primitive (no Radix, Headless UI,
or hand-rolled component). Adding one for four cells is over-engineering.

### Why native `title="…"`

- Already used in this same file at line 976 for the clearing-partner
  full-name reveal — established pattern in this codebase.
- Accessible: keyboard focus on the surrounding row reveals on
  long-press on touch devices, exposed via the accessible name to
  screen readers.
- Zero dependency, zero JS, zero CLS risk.
- This is a hint, not interactive content — the use case the native
  attribute was designed for.

## Tooltip mapping

| Column           | Trigger                              | Tooltip text                           |
|------------------|--------------------------------------|----------------------------------------|
| CIK              | `item.cik === null`                  | "No SEC registration on file"          |
| Net Capital      | `item.latest_net_capital === null`   | "No FOCUS report on file"              |
| YoY Growth       | `item.yoy_growth === null`           | "Needs ≥2 historical FOCUS filings"    |
| Last Filing Date | `item.last_filing_date === null`     | "No Edgar filing on record"            |

## Intentionally untouched

- Clearing Type "Unknown" pill — classifier bug, fix in flight (#19).
  Tooltipping would hide a broken classifier behind nice copy.
- Financial Health / Lead Priority "Unknown" pills — pipeline gap, fix
  in flight (#22).
- Cells with real values — the tooltip only attaches inside the null
  branch, so `"$74M"` and `"+12.5%"` render unchanged.

## Files touched

- `frontend/components/master-list/master-list-workspace-client.tsx`
  — wraps the four data-missing `"—"` renders in `<span title="…">`.

## Acceptance

- Hover any `"—"` in the four target columns: matching tooltip appears.
- Hover real values: no tooltip.
- Pill cells (Clearing Type, Health, Lead Priority): unchanged.
- `npm run lint && npm run build` clean.
