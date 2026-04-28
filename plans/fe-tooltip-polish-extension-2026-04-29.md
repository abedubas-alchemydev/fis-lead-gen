# FE tooltip polish — extension to /alerts and /email-extractor

**Date:** 2026-04-29
**Branch:** feature/fe-tooltip-polish-extension
**Owner:** cli02 (FE-1)
**Parent PR:** #120/#121 (master-list "—" tooltips)

## Goal

Mirror the `<span title="...">—</span>` data-missing tooltip pattern
from PR #120 onto remaining "—" cells outside the master list.

## Pattern to reuse

PR #120 used the plain HTML `title` attribute on the same `<span>` that
already wraps the em-dash. No new component, no library tooltip:

```tsx
<span title="No SEC registration on file">—</span>
```

## Audit results

### /alerts — NO action needed

Audited `frontend/components/alerts/alerts-client.tsx` and
`frontend/components/alerts/alert-feed-card.tsx`. The `AlertListItem`
type (`frontend/lib/types.ts:87`) has all rendered fields required:

- `firm_name`, `form_type`, `priority`, `filed_at`, `summary` — all
  non-nullable, always rendered.
- `source_filing_url` — nullable but conditionally rendered as a
  "View filing" link; no "—" placeholder when null.

No "—" cells exist on /alerts today. Skipped.

### /email-extractor home — NO action needed

Audited `frontend/app/(app)/email-extractor/page.tsx`. The hub renders
domain, status pill, success / failure counts, and relative timestamps.
All values come from data that is always populated for a `ScanListItem`
(domain comes from user input; counts default to 0; created_at is
always set). No "—" placeholders.

### /email-extractor scan detail — 4 tooltips

`frontend/app/(app)/email-extractor/[scanId]/page.tsx` has the only
genuine "—" cells in scope:

| Field      | Render path                       | "—" when                     | Tooltip copy                      |
|------------|-----------------------------------|------------------------------|-----------------------------------|
| Confidence | `ResultsTable` confidence column  | `row.confidence === null`    | "No confidence score from source" |
| Processed  | Run-metadata `MiniStat`           | `scan.total_items === 0`     | "Scan hasn't started"             |
| Started    | Run-metadata `MiniStat`           | `scan.started_at === null`   | "Scan still queued"               |
| Completed  | Run-metadata `MiniStat`           | `scan.completed_at === null` | "Scan still in progress"          |

The Person `MiniStat` already shows a `helper="Not specified"` line
when `person_name` is null, which serves the same purpose; not adding
a tooltip there to avoid redundancy.

## Implementation notes

- `MiniStat` (local helper in `[scanId]/page.tsx`) takes
  `value: string`. To attach a `title` only to the data-missing case,
  add an optional `valueTitle?: string` prop. When set, it lands on the
  inner `<p>` as a `title` attribute. Existing call sites unaffected.
- `formatConfidence` returns `string`. To wrap "—" in a `<span title>`,
  switch its return to `React.ReactNode` and emit a span for the null
  case. It's only used in this file (verified by grep), so no ripple.

## Pages NOT touched

- `/master-list` — already done in PR #120
- `/master-list/{id}` — cli04's #29 scope this batch
- `/my-favorites` — cli04's #29 scope this batch
- `/visited-firms` — cli04's #29 scope this batch

## Files modified

- `frontend/app/(app)/email-extractor/[scanId]/page.tsx` — extend
  `MiniStat` + `formatConfidence`; wire 4 tooltips.
- `plans/fe-tooltip-polish-extension-2026-04-29.md` — this file.

## Verification

- `npm run lint` clean
- `npm run build` clean
- Manual smoke after auto-promote: hover the four target cells while a
  scan is queued/running and on a row whose source returned no
  confidence; tooltip text appears.
