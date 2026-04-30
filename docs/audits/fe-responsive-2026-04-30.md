# FE responsive-design audit — 2026-04-30

**Trigger:** all 8 main FE pages now have consistent empty/loading/error UX
(today's polish ships). Surfacing responsive-design issues now lets us queue
fixes before Deshorn's next review (if mobile usability is on his list) or
proactively if not.

## Method

Static, read-only grep scan against `frontend/components/**` and the
`frontend/app/(app)/**/page.tsx` clients, looking for:

- **a) Fixed widths/heights** — `w-[Npx]`, `h-[Npx]`, raw `width:` /
  `min-width:` / `max-width:` style values.
- **b) Missing breakpoint classes** — `flex` / `grid` containers with no
  `sm:` / `md:` / `lg:` / `xl:` variants.
- **c) Horizontal overflow risk** — multi-column tables wrapped in
  `overflow-hidden` instead of `overflow-x-auto`.
- **d) Sidebar / panel min-widths** — `min-w-N`, `w-64`, `w-72`, `w-80`.
- **e) Oversized fonts** — `text-3xl` / `text-4xl` / `text-5xl` on
  containers below the topbar.

No browser-driven testing this run — purely static code analysis. Findings
target three reference viewports: **mobile 375px**, **tablet 768px**,
**desktop 1280px**.

## Pages audited

| Page | HIGH | MEDIUM | LOW | Status |
|---|---|---|---|---|
| /master-list | 0 | 0 | 1 | ok |
| /master-list/{id} | 0 | 0 | 0 | ok |
| /alerts | 0 | 0 | 0 | ok |
| /export | 0 | 0 | 0 | ok |
| /my-favorites | 0 | 0 | 0 | ok |
| /visited-firms | 0 | 0 | 0 | ok |
| /dashboard | 0 | 1 | 0 | needs-work |
| /email-extractor (list) | 0 | 0 | 0 | ok |
| /email-extractor/{scanId} | 1 | 1 | 0 | needs-work |
| /settings/users | 1 | 0 | 0 | needs-work |
| /settings/pipelines | 1 | 1 | 0 | needs-work |
| **App shell (sitewide)** | 0 | 1 | 0 | needs-work |
| **Totals** | **3** | **4** | **1** | |

## HIGH-severity findings

### H1. Settings → Users pending-approval table clips horizontally on mobile/tablet

- **File:** `frontend/components/settings/users-admin-client.tsx:87-88`
- **Issue:** 5-column table (Name, Email, Signed up, Verified, Actions with
  Approve + Reject buttons) is wrapped in
  `<div className="overflow-hidden rounded-[24px] border …">` — i.e.
  `overflow-hidden`, **not** `overflow-x-auto`. With Approve/Reject buttons
  in the right column, the minimum row width is ~720px+. Below ~720px
  viewport (mobile + tablet portrait) the right column is **clipped**, not
  scrollable.
- **Why it matters:** admin can't see / reach Approve/Reject on mobile;
  tablet portrait users hit the same wall.
- **Suggested fix:** swap `overflow-hidden` → `overflow-x-auto` on
  line 87, or wrap the `<table>` in an inner
  `<div className="overflow-x-auto">`. Master-list workspace already uses
  this pattern (`master-list-workspace-client.tsx:978`).

### H2. Settings → Pipelines recent-runs table clips horizontally

- **File:** `frontend/components/settings/pipelines/recent-runs-table.tsx:78-79`
- **Issue:** identical pattern to H1 — 5-column table
  (Pipeline / Status / Processed / Trigger / Started) wrapped in
  `overflow-hidden`. Below ~600px the right columns clip.
- **Suggested fix:** same as H1 — replace `overflow-hidden` with
  `overflow-x-auto` (or add an inner scroll wrapper).

### H3. Email-extractor scan-detail discovered-emails table clips horizontally

- **File:** `frontend/app/(app)/email-extractor/[scanId]/page.tsx:376-377`
- **Issue:** 5-column table (Email / Source / Confidence / Enrichment /
  Verification — both right columns contain inline buttons + status pills)
  wrapped in `overflow-hidden`. Email column alone is mono-spaced and
  unbreakable for typical addresses, so even at 768px tablet portrait the
  Verification column is at risk of clipping.
- **Suggested fix:** swap `overflow-hidden` → `overflow-x-auto` on
  the table's wrapper div.

## MEDIUM-severity findings

### M1. Dashboard clearing-distribution chart row crams below 375px

- **File:** `frontend/components/dashboard/clearing-distribution-chart.tsx:76, 166`
- **Issue:** `grid grid-cols-[10px_minmax(0,40%)_minmax(80px,1fr)_56px]`
  needs a minimum of roughly 10 + 40% + 80 + 56 + gaps ≈ 274px before any
  text. On a 375px viewport with `px-7` page padding (28px each side =
  56px gone), available width is ~319px → cramped. On 320px viewports the
  middle min-width-80px column stops collapsing and forces wrap.
- **Suggested fix:** consider
  `grid-cols-[10px_1fr_auto_56px] sm:grid-cols-[10px_minmax(0,40%)_minmax(80px,1fr)_56px]`
  so narrow viewports drop the 40% lock.

### M2. Settings/Pipelines "Latest Run" KPI grid jumps to 4 columns at sm: breakpoint

- **File:** `frontend/components/settings/pipeline-admin-client.tsx:183`
- **Issue:** `grid gap-4 sm:grid-cols-4` — at 640px viewport the four KPI
  cards (Status / Processed / Successes / Flagged) get ~140px each minus
  gaps. Tablet portrait (768px) is workable but tight; the sm: breakpoint
  itself is borderline.
- **Suggested fix:**
  `grid grid-cols-2 gap-4 lg:grid-cols-4` — KPIs use 2-up on phone/tablet
  and only fan out to 4-up at desktop.

### M3. Email-extractor scan-detail loading skeleton uses `w-72` (288px)

- **File:** `frontend/app/(app)/email-extractor/[scanId]/page.tsx:709-710`
- **Issue:** placeholder bars `h-3 w-72` (288px) and `mt-3 h-7 w-72`
  occupy almost the full width of an iPhone SE viewport (375px - 56px
  page padding = 319px). On Galaxy Fold (~280px) they overflow. Real
  rendered headings later are width-fluid; only the skeleton is static.
- **Suggested fix:** `w-2/3` or `w-48 sm:w-72`. Skeletons should use
  fluid widths.

### M4. Sitewide page padding `px-7 ... lg:px-9` skips a small-mobile step

- **File:** every authenticated page client
  (`px-7 pb-12 pt-7 lg:px-9` repeated in master-list, alerts, export,
  visited-firms, dashboard-home, email-extractor home + scan-detail,
  settings, my-favorites).
- **Issue:** 28px each side on viewports as narrow as 320–375px is heavier
  than a typical Tailwind app. Combined with the dashboard chart row
  (M1) and table cells, content gets pinched.
- **Suggested fix:** the canonical pattern is
  `px-4 sm:px-7 lg:px-9`. Gives back ~24px to phone viewports without
  changing tablet/desktop. Should be applied across ~10 page wrappers in
  the same PR.

## LOW / nice-to-haves

### L1. Master-list workspace search hidden below md, no mobile alternative

- **File:** `frontend/components/master-list/master-list-workspace-client.tsx:531`
  (and equivalent shell-wide via `frontend/components/layout/top-actions.tsx:55`).
- **Issue:** the 320px-wide search input is `hidden ... md:flex` on every
  authenticated page. Mobile users have **no quick search affordance** —
  they have to scroll to the toolbar-card filter or open filters
  individually. Likely intentional (master-list also has a filter card
  with its own search at line 883), but worth noting if Deshorn flags
  mobile usability.
- **Suggested fix:** none required this sprint; if mobile becomes a
  real surface, a mobile search-icon button that expands into a
  full-width drawer would close the gap.

## Recommendations

1. **Ship the 3 HIGH fixes as one PR** — they're all the same one-token
   change (`overflow-hidden` → `overflow-x-auto`). ~5 minute job; low
   blast radius; eliminates the only "you literally can't reach the
   button" bugs in the audit.
2. **Then ship M4 (sitewide padding) as a single PR** — touches ~10 page
   wrappers, single search/replace. Helps M1 indirectly by giving the
   chart row 24px more breathing room.
3. **Defer M1 / M2 / M3 to a follow-up sprint** — design tweaks rather
   than functional bugs.
4. **L1 is a no-op until Deshorn flags it.**

## Followup tasks (each = small PR scoped to one fix)

1. **fix(fe): swap `overflow-hidden` → `overflow-x-auto` on 3 admin tables**
   (H1, H2, H3) — single PR, three files.
2. **fix(fe): standardize page padding to `px-4 sm:px-7 lg:px-9`**
   (M4) — touches ~10 page wrappers.
3. **fix(fe): dashboard clearing-distribution row collapses cleanly below sm:**
   (M1).
4. **fix(fe): settings/pipelines KPI grid uses 2-up on phone/tablet**
   (M2).
5. **fix(fe): scan-detail loading skeleton uses fluid width**
   (M3).

## Pages audited — file map

For reproduction, these were the source files scanned:

- `/master-list` → `frontend/components/master-list/master-list-workspace-client.tsx`,
  `frontend/components/master-list/filters/*.tsx`
- `/master-list/{id}` → `frontend/components/master-list/broker-dealer-detail-client.tsx`,
  `frontend/components/master-list/detail/*.tsx`
- `/alerts` → `frontend/components/alerts/*.tsx`
- `/export` → `frontend/components/export/*.tsx`
- `/my-favorites` → `frontend/components/my-favorites/*.tsx`
- `/visited-firms` → `frontend/components/visited-firms/*.tsx`
- `/dashboard` → `frontend/components/dashboard/*.tsx`
- `/email-extractor` (list) → `frontend/app/(app)/email-extractor/page.tsx`,
  `frontend/components/email-extractor/*.tsx`
- `/email-extractor/{scanId}` → `frontend/app/(app)/email-extractor/[scanId]/page.tsx`
- `/settings/users` → `frontend/components/settings/users-admin-client.tsx`
- `/settings/pipelines` → `frontend/components/settings/pipeline-admin-client.tsx`,
  `frontend/components/settings/pipelines/*.tsx`
- App shell → `frontend/components/layout/app-shell.tsx`,
  `frontend/components/layout/top-actions.tsx`
