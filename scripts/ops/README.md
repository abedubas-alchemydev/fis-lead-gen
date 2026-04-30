# Ops scripts

Helpers for production operations.

## smoke-prod.ps1

Runs ~13 lightweight HTTP checks against
`https://fis.alchemydev.io`. Designed to catch 5xx
regressions in <30 seconds after every deploy.

### Usage

```powershell
./scripts/ops/smoke-prod.ps1
```

Exits 0 on all-pass, non-zero on any failure. Output is
color-coded for human review.

### When to run

- After every prod deploy (manually for now; consider
  wiring into a Cloud Scheduler job that POSTs to a
  monitoring endpoint that runs this).
- Before any major data refresh (regen) — confirms the
  app is healthy before we modify data.
- As part of any incident-response runbook to verify
  recovery.

### What it catches
- Any route returning 5xx (server error)
- The `/master-list/{id}` firm-detail page specifically
  (the route that 500'd during the 2026-04-29
  user_favorite incident)
- Missing key page markers on auth-public pages
  (/login, /signup, /pending-approval)

### What it does NOT catch
- Logic regressions that return 200 with wrong content
  (e.g. wrong scoring weights — see scoring smoke separately)
- Performance regressions
- DB drift that doesn't cause 5xx (use the drift audit instead)
