# Ops scripts

Helpers for production operations.

## smoke-prod.ps1

Lightweight HTTP smoke-check against `https://fis.alchemydev.io`.
Designed to catch 5xx regressions, broken auth gates, and React
mount failures in <30 seconds after every deploy.

Requires PowerShell 7+ (`pwsh`) — uses `-SkipHttpErrorCheck`.

### Usage

Anonymous mode (default):

```powershell
./scripts/ops/smoke-prod.ps1
```

Hits public routes + asserts the auth gate redirects unauthenticated
callers on every protected route. Does not require any credentials.

Authenticated admin mode:

```powershell
./scripts/ops/smoke-prod.ps1 -AdminCookie "__Secure-better-auth.session_token=eyJhbGciOi..."
```

In addition to all anonymous checks, hits each protected route with
the supplied session cookie and asserts the rendered HTML contains
the expected page-title marker.

#### How to obtain a session cookie

1. Open <https://fis.alchemydev.io> in your browser.
2. Sign in as an admin.
3. Open DevTools → Application → Cookies → `https://fis.alchemydev.io`.
4. Copy the value of `__Secure-better-auth.session_token`
   (or `better-auth.session_token` in local dev).
5. Pass it as `-AdminCookie "<name>=<value>"` (the full Cookie header
   format — keep the cookie name as a prefix).

Treat the cookie value as a credential. Don't paste it into shared
chat, don't commit it, and rotate by signing out when finished.

### Exit code

Exits 0 on all-pass, non-zero on any failure. Output is color-coded
for human review and ends with a one-line `Summary: …` block plus a
final `Smoke OK.` / `Smoke FAILED.` headline.

### When to run

- After every prod deploy (manually for now; consider wiring into a
  Cloud Scheduler job that POSTs to a monitoring endpoint that runs
  this).
- Before any major data refresh (regen) — confirms the app is healthy
  before we modify data.
- As part of any incident-response runbook to verify recovery.

### What it catches

Anonymous mode (always runs):

- Any route returning 5xx (server error)
- The `/master-list/{id}` firm-detail page specifically (the route
  that 500'd during the 2026-04-29 user_favorite incident)
- `/login`, `/signup` missing their expected markers
- Auth-gate regressions: `/dashboard`, `/master-list`, `/alerts`,
  `/settings/users`, `/settings/pipelines` must 307 to `/login`
  for unauthenticated callers (NEGATIVE tests)

Authenticated admin mode (additional, only when `-AdminCookie` set):

- `/dashboard` renders the `Lead Intelligence Workspace` heading
- `/master-list` renders the `Broker-Dealer Master List` heading
- `/alerts` renders the `Daily filing monitor` heading
- `/my-favorites` renders the `Saved firms` heading
- `/visited-firms` renders the `Visited Firms` heading
- `/email-extractor` renders the `Domain email discovery` heading
- `/export` renders the `Restricted CSV export` heading
- `/settings/users` renders the `User approvals` admin panel
- `/settings/pipelines` renders the `Filing Monitor` trigger card

### What it does NOT catch

- Logic regressions that return 200 with wrong content (e.g. wrong
  scoring weights — see scoring smoke separately)
- Performance regressions
- DB drift that doesn't cause 5xx (use the drift audit instead)
- Full hydration of client-only pages like `/master-list/{id}` (the
  static-HTML check only confirms the route doesn't 5xx)
