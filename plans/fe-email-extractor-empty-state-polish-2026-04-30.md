# /email-extractor empty-state + loading + error polish

**Branch:** `feature/fe-email-extractor-empty-state-polish`
**Base:** `develop`
**Date:** 2026-04-30
**Driver:** cli04 (FE-2 worktree)

## Why

`/email-extractor` is the only main FE page that did not get the
"empty / loading / error" consistency treatment shipped today on
`/my-favorites`, `/alerts`, `/visited-firms`, `/export`, and
`/dashboard`. PR #132/#133 added "—" cell tooltips, but the
empty / loading / error surfaces are still the original sparse
versions: a one-line dashed-border box for empty, a small red banner
for errors. This PR brings the page in line with the rest of the app.

No BE changes — purely FE state branching around existing fetches.

## Surfaces touched

There are two pages under `/email-extractor`:

| Page | File | Existing state | Gap |
|------|------|----------------|-----|
| Hub  | `app/(app)/email-extractor/page.tsx` | dashed-border one-liner empty; small red banner for `historyError` | upgrade to medallion empty + medallion error card |
| Detail | `app/(app)/email-extractor/[scanId]/page.tsx` | dashed-border one-liner empty; small red banner for `loadError`; status pill + inline `error_message` for `scan.status === "failed"`; no in-progress visual when scan is queued/running with 0 emails | medallion empty when scan completed with 0 emails; medallion "Searching…" while scan is in progress with 0 emails so far; medallion error card for both load-fail and scan-failed |

## Components added (`frontend/components/email-extractor/`)

All four follow the medallion shape already shipped on `/dashboard`,
`/alerts`, `/visited-firms`, `/my-favorites`, `/export`:

```
rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center
  → 14×14 medallion (rounded-full, surface-3 bg or red bg for errors)
  → h3 heading (text-[15px] font-semibold)
  → p subtext (max-w-sm text-[13px] text-text-dim)
  → optional CTA pill (gradient for empty-with-action) or Retry button
```

1. **`empty-scan-results-state.tsx`** — detail page, scan completed
   with `discovered_emails.length === 0`.
   - Icon: `MailX` (Lucide)
   - Heading: "No emails discovered for this domain"
   - Subtext: "The four providers ran but none returned an address.
     Try a different domain or person hint from the hub."
   - CTA: gradient pill "Back to Email Extractor" → `/email-extractor`

2. **`scan-results-loading.tsx`** — detail page, scan is `queued` or
   `running` with `discovered_emails.length === 0` so far.
   - Icon: `Search` (Lucide) inside the medallion
   - Heading: "Searching for emails…"
   - Subtext: dynamic — "Hunter, Snov, in-house crawler, and
     theHarvester are running in parallel. This usually takes 5-30
     seconds."
   - Helper line: "<processed>/<total> providers complete" if
     `total_items > 0`, else "Just started — first results land in a
     few seconds."
   - No CTA — the user is already on the polling page.

3. **`email-extractor-error-card.tsx`** — generic error medallion,
   reused on both hub (history fetch failed) and detail (scan load
   failed OR scan.status === "failed"). Mirrors
   `DashboardErrorCard` shape: dashed border + red medallion + Retry.
   - Props: `{ title, message, onRetry, retryLabel? }`
   - Icon: `AlertTriangle`
   - Subtext rotates per-caller. Detail page uses dynamic subtext
     keyed off `scan.error_message` so "no domain on file", "rate
     limited", and "Apollo unavailable" each get their own copy when
     the BE returns enough info — falls back to the raw error
     message otherwise.

4. **`empty-scans-state.tsx`** — hub page, the
   `recentScans.length === 0 && !historyLoading` branch.
   - Icon: `MailSearch` (Lucide)
   - Heading: "No scans yet"
   - Subtext: "Submit a domain above and we'll fan out to Hunter,
     Snov, the in-house crawler, and theHarvester. Past scans land
     here so you don't have to re-run them."
   - No CTA — the new-scan form is already directly above the panel.

## Page-level wiring

### `app/(app)/email-extractor/page.tsx` (hub)

- Replace the dashed-border one-liner at the
  `recentScans.length === 0` branch with `<EmptyScansState />`.
- Replace the inline `historyError` red banner above the History
  panel with `<EmailExtractorErrorCard title="Couldn't load recent
  scans" message={historyError} onRetry={loadRecent} />`.
- Loading skeleton (the six pulse rows) stays untouched — it already
  matches the in-app skeleton pattern.

### `app/(app)/email-extractor/[scanId]/page.tsx` (detail)

- The page-level skeleton for `scan === null && loadError === null`
  stays untouched (it's the detail-shell pulse pattern shared with
  master-list detail).
- Replace the small red banner inside the `scan === null && loadError !== null`
  branch with `<EmailExtractorErrorCard title="Couldn't load scan"
  message={loadError} onRetry={() => window.location.reload()} />`.
  Reload is the simplest retry primitive without lifting `load` out
  of the effect.
- Inside the "Discovered emails" SectionPanel:
  - When `scan.status === "failed"`, render
    `<EmailExtractorErrorCard title="Scan failed" message={dynamicSubtext(scan.error_message)} onRetry={() => router.push("/email-extractor")} retryLabel="Back to hub" />`
    in place of the `ResultsTable`.
  - When `scan` is `queued` or `running` and
    `discovered_emails.length === 0`, render
    `<ScanResultsLoading processed={scan.processed_items} total={scan.total_items} />`.
  - When `scan.status === "completed"` and
    `discovered_emails.length === 0`, render
    `<EmptyScanResultsState />`.
  - Otherwise: render the existing `<ResultsTable />`.
- The status pill row at the top is **not** removed — the medallion
  inside the panel layers on top of the existing header context.

## Dynamic error subtext

Helper inside the detail page (small inline function, not exported
since /email-extractor is the only consumer):

```
function dynamicSubtext(rawMessage: string | null): string {
  if (rawMessage === null) return "Something went wrong while running this scan.";
  const lower = rawMessage.toLowerCase();
  if (lower.includes("no domain")) return "No domain on file for this firm — try the hub with a domain entered manually.";
  if (lower.includes("rate") && lower.includes("limit")) return "Rate limited — wait a minute and retry.";
  if (lower.includes("apollo")) return "Apollo enrichment is unavailable right now — emails were discovered but couldn't be enriched.";
  return rawMessage;
}
```

This stays as a local helper because the spec is tight and the
copy is intentionally page-specific.

## Out of scope

- No new shared primitives in `components/ui/`.
- No BE changes; no touches to `lib/email-extractor.ts` or any API
  route.
- No changes to `EnrichAllButton` (it owns its own loading +
  toast UX and does not need the consistency treatment).
- No changes to `master-list`, `alerts`, `visited-firms`,
  `my-favorites`, `export`, `dashboard`, `settings`, or `ui/`.

## Verify locally

```
cd frontend
npm run lint
npm run build
```

## Commit + PR

Single commit on `feature/fe-email-extractor-empty-state-polish`,
PR into `develop`, Phase A squash-merge with `--admin`, then Phase B
release coordination per the auto-promote pattern.
