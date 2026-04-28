# Email Authentication Flow Audit — fis-lead-gen

**How to run this**: in a Claude Code CLI session on `fis-lead-gen`, say:
> Read `prompts/email-auth-audit.md` and follow it.

Do NOT change any code. This is a read-only audit. The output is a status report I can act on afterwards.

---

## Objective

Produce a full status report of the email authentication flow — from signup through verified login — so I know exactly what currently works, what's broken, and what's partially wired. Cover both local dev and production (Cloud Run) configurations.

## Ground rules

1. **Read-only.** No edits to source files, configs, migrations, or env. If you find something to fix, note it in §10 — don't touch it.
2. **Verify against current code**, not docs. Docs may lag. Treat `git log` / current file contents as truth.
3. **Distinguish observation vs inference.** If you can see it in a file, mark it `[observed]`. If you're reasoning about behavior, mark it `[inferred]`. Don't conflate the two.
4. **Respect the invariants in `CLAUDE.md`** (env load order, auth model, etc.) — if any of them are violated by current code, that's a finding.
5. **Use ECC agents**: dispatch `/plan` first to scope the audit, then use research-only dispatch (no implementation).
6. **All substantive output goes to `.md` files, not chat.** The `/plan` output must be written to `plans/email-auth-audit-plan-<YYYY-MM-DD>.md` before anything else runs. The final audit goes to `reports/email-auth-audit-<YYYY-MM-DD>.md`. Any intermediate reconciliation notes, agent output summaries, or config comparisons longer than ~10 lines likewise go to a `.md` file under `reports/` or `plans/`. The chat reply is always short — ≤ 5 lines: file path(s) written, one-sentence summary, the confirmation or question. Do NOT paste the plan, findings, or checklist contents into chat.

## Files to read (in this order)

### Frontend (Next.js + BetterAuth)
- `frontend/lib/auth.ts` — BetterAuth server config (email verification settings, token TTL, callback URLs)
- `frontend/lib/auth-server.ts` / `frontend/lib/auth-client.ts` — the split between server and browser surfaces
- `frontend/lib/email.ts` — Resend wrapper (from-address, template, send function)
- `frontend/app/api/auth/[...all]/route.ts` — BetterAuth catch-all handler (signup / verify-email / reset-password all flow through here)
- `frontend/app/(auth)/signup/page.tsx` and `frontend/app/(auth)/login/page.tsx` — client-side entry points
- `frontend/app/(auth)/forgot-password/page.tsx` and `reset-password/page.tsx` — recovery flow
- `frontend/middleware.ts` — gate on session cookie
- `frontend/.env.example` and `frontend/.env.local` (if present) — env vars: `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`, `RESEND_API_KEY`, cookie prefix behavior

### Backend (FastAPI session validation)
- `backend/app/services/auth.py` — cookie-based session validation (does NOT send emails, only reads sessions)
- `backend/app/api/v1/endpoints/auth.py` — `/api/v1/auth/me` endpoint
- `backend/app/core/config.py` — `AUTH_SESSION_COOKIE_NAME`, `BETTER_AUTH_SECRET`, `ENVIRONMENT`
- `backend/.env` / `backend/.env.example` — confirm `AUTH_SESSION_COOKIE_NAME` matches the env's actual cookie name

### Database
- `backend/alembic/versions/*.py` — identify which migration provisions the BetterAuth tables (`user`, `session`, `account`, `verification`). Confirm `verification` table exists with a `value` + `expiresAt` column.

### Operational context (reference only)
- `email-verification-diagnosis.md` — prior diagnosis notes
- `deployment-report.md` — deployment-time findings
- `CLAUDE.md` — cookie-name-differs-by-env rule, `BETTER_AUTH_SECRET` must-match rule

## Flow stages to trace

For each stage, answer: **does it currently work? with what evidence? what would break it?**

1. **Signup submit** (`app/(auth)/signup`) → BetterAuth `/api/auth/sign-up/email` → writes `user` row with `emailVerified=false` → creates `verification` row with token + expiry → triggers email send via `sendVerificationEmail` hook
2. **Email send** (`lib/email.ts` → Resend) — identify the `from` address, confirm whether it's a verified domain or the sandbox `onboarding@resend.dev`. Note which environments use which.
3. **Token TTL** — the `expiresAt` on the `verification` row. Record the exact value in BetterAuth config and compare against the claim in top-5 production risks that tokens expire before users open the email.
4. **Email delivery** — which accounts can actually receive the email given (2)? Note the Resend sandbox constraint (only delivers to the Resend account owner) when applicable.
5. **Link click → `/api/auth/verify-email`** — BetterAuth catch-all handler processes the token. Note: the codebase has **no dedicated `/verify-email` page** — the default BetterAuth UI is what the user sees on expired/invalid tokens. Confirm this is still the case.
6. **Session issuance** after verification — `session` row created, cookie set on response. Cookie name varies by env (`better-auth.session_token` local / `__Secure-better-auth.session_token` prod).
7. **Cookie forwarded to backend** — browser → Next.js BFF proxy (`frontend/app/api/backend/[...path]/route.ts`) → FastAPI. Confirm the proxy does `new Headers(request.headers)` (full forward) and does not allowlist.
8. **Backend validates session** — `backend/app/services/auth.py` reads cookie, verifies HMAC-SHA256 with `BETTER_AUTH_SECRET`, looks up `session` by token, checks `expiresAt`, loads user, returns `AuthenticatedUser`.
9. **`/api/v1/auth/me`** responds 200 with user payload → UI redirects to dashboard.

For each of stages 1–9, produce one row in the report with: `stage`, `observed/inferred`, `status (working | partial | broken | unknown)`, `evidence (file path + line range)`, `risks`.

## What to check specifically (config-level gotchas)

- [ ] `BETTER_AUTH_SECRET` value in `frontend/.env.local` vs `backend/.env` — are they the same? Mismatched secrets present as "login redirects back to login" with no obvious error.
- [ ] `AUTH_SESSION_COOKIE_NAME` in `backend/.env` — does it match the cookie BetterAuth actually sets in this env? Local: `better-auth.session_token`. Prod (Cloud Run, `ENVIRONMENT=production`): `__Secure-better-auth.session_token`.
- [ ] `BETTER_AUTH_URL` — is it the Next.js frontend URL, not the backend? BetterAuth catch-all lives on the frontend.
- [ ] Resend `from` address — sandbox `onboarding@resend.dev` vs a verified domain. If sandbox: who is the Resend account owner (only address that receives)?
- [ ] Token TTL: BetterAuth default vs override. Note the exact value. If < email typical delivery time + user read time (~30 min), mark as production risk.
- [ ] Email template: does it render the verification link with the correct `callbackURL`? A wrong callback URL sends users to a 404.
- [ ] `/api/auth/[...all]` route presence — confirm it exists and exports `GET` and `POST` handlers via `toNextJsHandler(auth)`.
- [ ] Middleware gating: does `frontend/middleware.ts` allow `/api/auth/*` and the `(auth)` group through without a session? Blocking these would create a login loop.
- [ ] BFF proxy header forwarding: `frontend/app/api/backend/[...path]/route.ts` must forward cookies via `new Headers(request.headers)` with `host` / `content-length` stripped. Any header allowlist there = silent auth break.
- [ ] Migration: is there a migration that creates `user`, `session`, `account`, `verification` tables? 503 error "auth tables don't exist" means it wasn't run.
- [ ] Cloud Run env wiring: are `BETTER_AUTH_SECRET` and `RESEND_API_KEY` injected via `--set-secrets` (not plain env vars)? Confirm via `gcloud run services describe fis-frontend --region=us-central1 --project=fis-lead-gen` (read-only, just show the config).

## What NOT to do

- Do not rotate `BETTER_AUTH_SECRET` (invalidates all existing sessions).
- Do not run `alembic upgrade head` or any migration — this is audit only.
- Do not test by creating real users in production. If you need to reproduce, describe the reproduction steps textually; I'll run them myself in staging.
- Do not send a test email via Resend. Do not call the Resend API.
- Do not `gh pr create`, commit, or edit any file.

## Deliverable format

Write a single markdown report to `reports/email-auth-audit-<YYYY-MM-DD>.md` with these sections:

1. **Executive summary** (3 bullets max: headline status, most critical blocker, recommended next action)
2. **Stage-by-stage status table** (the 9 stages from above)
3. **Config findings** (the config checklist results)
4. **Env comparison** (local vs staging vs prod, values that differ)
5. **Delivery status** — who can actually receive verification emails given current Resend config
6. **Token TTL analysis** — current value, risk level
7. **Known gaps vs `CLAUDE.md` / `PROJECT_ANALYSIS.md` claims** — anything the docs say that the code contradicts
8. **Blast radius** — what happens to existing verified users if the email flow is changed (e.g., does changing cookie name invalidate their sessions? Does rotating secret?)
9. **Reproducibility recipe** — how I can repro each failure mode locally (so I can verify fixes later)
10. **Recommended fix order** (prioritized list — don't implement, just propose). Each item: problem, evidence, proposed fix (one paragraph), blast radius, whether it requires a staging deploy or can be local-only.

At the very end, include:
```
Audit complete. Read-only. No files changed.
Report: reports/email-auth-audit-<YYYY-MM-DD>.md
```

## Stop conditions

Stop and ask if:
- Any required file is missing or materially different from what `PROJECT_ANALYSIS.md` describes.
- You find evidence that a prior audit was already run and produced a conflicting conclusion.
- You would need to make a network call or run a command beyond `gcloud ... describe` to verify a claim.

## Acknowledgement

After `/plan` has written `plans/email-auth-audit-plan-<YYYY-MM-DD>.md`, reply in chat with exactly this (no other text, no pasted plan contents):

```
email-auth audit plan saved: plans/email-auth-audit-plan-<YYYY-MM-DD>.md
Read-only trace of 9 stages (signup → verified session). No edits will be made.
Approve to run? (yes / modify)
```

Wait for approval before running Phases 1–3. Once approved, execute and at the end reply with exactly:

```
Audit complete. Read-only. No files changed.
Report: reports/email-auth-audit-<YYYY-MM-DD>.md
```

Nothing else — no summary in chat, no highlights, no next steps. Everything belongs in the report file.
