# SMTP Code Swap — Resend SDK → Nodemailer (fis-lead-gen)

**How to run this**: in a Claude Code CLI session on `fis-lead-gen`, say:
> Read `prompts/smtp-code-swap.md` and follow it.

**Prereq (already complete):** `prompts/smtp-relay-provisioning.md` has run successfully — `SMTP_PASSWORD` secret exists in Secret Manager and is bound to the frontend runtime SA. Verify before proceeding by reading `reports/smtp-relay-provisioning-2026-04-18.md`.

---

## Objective

Switch the transactional email path from Resend to Google Workspace SMTP relay, wire the Cloud Run staging service env, and open a PR to `develop`. Do **not** merge. Do **not** touch production (`fis-frontend`) — staging auto-deploy is the verification gate.

Scope of this run:
1. Replace Resend SDK usage in `frontend/lib/email.ts` with nodemailer.
2. Update `frontend/package.json` + lockfile.
3. Update `frontend/.env.example` (document new vars, remove `RESEND_API_KEY`).
4. Fix the token-TTL drift from the audit (Fix #3): set `emailVerification.expiresIn = 60 * 60 * 24` in `frontend/lib/auth.ts` so the code matches the "24 hours" claim in the email copy.
5. Wire env vars + `--set-secrets` on `fis-frontend-staging` only.
6. Build + lint locally to catch regressions.
7. Open a PR to `develop`. Do not merge.

Source-of-truth docs:
- `reports/email-auth-audit-2026-04-17.md` (current findings; Fix #1/#2/#3 are addressed here)
- `plans/email-provider-migration-plan-2026-04-17.md` (target state + acceptance checklist)
- `.auto-memory/project_email_sender.md` (decision record)
- `CLAUDE.md` — §"Conventions specific to this repo" (commit-and-PR style, branch/PR flow, stage-by-name, run-from-repo-root)

---

## Ground rules

1. **Zero AI attribution in commits and PR.** No `Co-Authored-By: Claude`, no "Generated with Claude Code" footer, no mention of Claude / AI / assistant / LLM / Anthropic anywhere in commit body, title, or PR description. Commit message must read as if Arvin wrote it. Before `git push`: run `git log -1 --pretty=full` and confirm no AI trailer.
2. **GitHub account must be `abedubas-alchemydev`** before any `gh` operation. Run `gh auth switch --user abedubas-alchemydev --hostname github.com` then `gh auth status` — confirm the active account before `gh pr create`.
3. **Stage files by name only.** Never `git add -A` / `git add .`. The repo has workspace artifacts (`credentials.md`, local `.env`, `reports/.tmp/`) that must not be swept in.
4. **Never skip hooks** (`--no-verify`) or bypass signing. If a pre-commit hook fails, fix the root cause and make a **new** commit — do NOT `--amend` the failed commit.
5. **Branch off `develop`, not `main`.** `develop` auto-deploys to staging via `.github/workflows/test.yml`. New feature branch name: `feature/smtp-relay-migration`.
6. **Do NOT touch `fis-frontend` (prod).** Only `fis-frontend-staging` env update in this run. Prod cutover happens after staging verification.
7. **Do NOT delete `RESEND_API_KEY` secret** or remove the Resend wiring on prod. Two-week rollback window per §4 of the migration plan.
8. **File-based output.** Substantive findings, diffs, command outputs → `reports/smtp-code-swap-<YYYY-MM-DD>.md`. Chat reply ≤ 5 lines.
9. **Run scripts from repo root** per `CLAUDE.md`.
10. **Do not merge the PR.** Arvin reviews and merges manually.

---

## Files to read first (in this order)

1. `reports/smtp-relay-provisioning-2026-04-18.md` — confirm `SMTP_PASSWORD` secret exists.
2. `reports/email-auth-audit-2026-04-17.md` — Fix #1, #2, #3 definitions + blast radius.
3. `frontend/lib/email.ts` — current Resend SDK usage; identify exported function signatures (they must be preserved).
4. `frontend/lib/auth.ts` — locate the `emailVerification` config block; find `expiresIn` (or its absence).
5. `frontend/package.json` — confirm `resend` is listed; confirm nodemailer is not already present.
6. `frontend/.env.example` — current env contract.
7. `.github/workflows/test.yml` — confirm staging deploy job uses `--set-env-vars` / `--set-secrets` or if env is set out-of-band. Decide whether env update goes through the workflow file or via `gcloud run services update`.
8. `CLAUDE.md` — confirm current rules haven't shifted since last session.

---

## Work items

### 1. Code change — `frontend/lib/email.ts`

Replace the Resend SDK with nodemailer. **Preserve exported function signatures exactly** — callers in `frontend/lib/auth.ts` (and anywhere else) must not need to change.

Target pattern:
```typescript
import nodemailer, { type Transporter } from "nodemailer";

let cachedTransporter: Transporter | null = null;

function getTransporter(): Transporter {
  if (cachedTransporter) return cachedTransporter;

  const host = process.env.SMTP_HOST;
  const port = Number(process.env.SMTP_PORT ?? 587);
  const user = process.env.SMTP_USER;
  const pass = process.env.SMTP_PASSWORD;

  if (!host || !user || !pass) {
    throw new Error(
      "SMTP transport not configured. Missing SMTP_HOST / SMTP_USER / SMTP_PASSWORD."
    );
  }

  cachedTransporter = nodemailer.createTransport({
    host,
    port,
    secure: false,        // STARTTLS on 587
    requireTLS: true,
    auth: { user, pass },
  });
  return cachedTransporter;
}

const FROM = process.env.EMAIL_FROM ?? "noreply@alchemydev.io";
```

Notes:
- Resend's `resend.emails.send({ from, to, subject, html, text })` maps 1:1 to `transporter.sendMail({ from, to, subject, html, text })`. Return value differs — adjust call sites only if they currently inspect `result.id` or Resend-specific fields.
- If the existing file has error handling that expects Resend's `{ error: {...}, data: {...} }` shape, rewrite to standard `try/catch` around `await transporter.sendMail(...)`.
- Drop the `Resend` import and the `new Resend(...)` initializer.

### 2. `frontend/package.json` + lockfile

- Remove `"resend"` from `dependencies`.
- Add `"nodemailer": "^6.9.14"` (or latest 6.x) to `dependencies`.
- Add `"@types/nodemailer": "^6.4.15"` to `devDependencies`.
- Run `npm install` (from `frontend/`) to regenerate `package-lock.json`. Stage both files.

### 3. `frontend/.env.example`

- Remove the `RESEND_API_KEY=` line.
- Add:
  ```
  # SMTP (Google Workspace relay)
  SMTP_HOST=smtp-relay.gmail.com
  SMTP_PORT=587
  SMTP_USER=noreply@alchemydev.io
  SMTP_PASSWORD=            # 16-char Google App Password — DO NOT COMMIT
  EMAIL_FROM=noreply@alchemydev.io
  ```

### 4. Token TTL — `frontend/lib/auth.ts`

In the BetterAuth config, locate `emailVerification: { ... }` (or add it if missing). Set:
```typescript
emailVerification: {
  sendOnSignUp: true,                 // preserve existing value if already set
  autoSignInAfterVerification: true,  // preserve existing value if already set
  expiresIn: 60 * 60 * 24,            // 24 hours — match the email copy
  sendVerificationEmail: /* existing hook */,
},
```
If the audit found the `sendVerificationEmail` hook already wired, leave it alone — only add or update `expiresIn`. Do NOT refactor the hook body in this PR.

### 5. Cloud Run staging env

Update `fis-frontend-staging` only. Inspect `.github/workflows/test.yml` first — if the staging deploy job passes env flags explicitly, add them there so subsequent deploys don't reset the service. Otherwise, apply out-of-band:

```bash
gcloud run services update fis-frontend-staging \
  --project=fis-lead-gen \
  --region=us-central1 \
  --set-env-vars=SMTP_HOST=smtp-relay.gmail.com,SMTP_PORT=587,SMTP_USER=noreply@alchemydev.io,EMAIL_FROM=noreply@alchemydev.io \
  --set-secrets=SMTP_PASSWORD=SMTP_PASSWORD:latest
```

**Do NOT run this command until Arvin approves.** The preflight / plan should list it; the actual execution requires the Acknowledgement gate below. If the workflow file already sets env on deploy, the cleaner path is: edit `.github/workflows/test.yml` so the next deploy carries the new env. Pick one path, not both.

### 6. Build + lint verification (local)

From `frontend/`:
```bash
npm install                  # regenerate lockfile
npm run lint                 # must pass
npm run build                # must pass — catches TS errors
```
Capture any warnings/errors into the report. Do not proceed to git operations if `npm run build` fails.

### 7. Git + PR

```bash
gh auth switch --user abedubas-alchemydev --hostname github.com
gh auth status                                                    # confirm active account

git fetch origin
git checkout -b feature/smtp-relay-migration origin/develop

# Stage ONLY the files we touched, by name:
git add frontend/lib/email.ts
git add frontend/lib/auth.ts
git add frontend/package.json
git add frontend/package-lock.json
git add frontend/.env.example
# Plus .github/workflows/test.yml if it was edited.

git status                                                        # double-check nothing extra is staged
git commit                                                        # use editor — see commit-message contract below
git log -1 --pretty=full                                          # VERIFY no AI trailer before pushing
git push -u origin feature/smtp-relay-migration

gh pr create --base develop --head feature/smtp-relay-migration   # see PR body contract below
```

#### Commit message contract (Arvin's voice, zero AI attribution)

```
Swap transactional email provider from Resend to Workspace SMTP relay

- lib/email.ts now uses nodemailer against smtp-relay.gmail.com:587,
  pulling credentials from SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD.
- Function signatures unchanged so auth.ts and other callers are unaffected.
- Set emailVerification.expiresIn to 24h to match the copy in the email body
  (was defaulting to BetterAuth's 1h).
- package.json: drop resend, add nodemailer + @types/nodemailer.
- .env.example updated; RESEND_API_KEY removed.

Cost-driven: Workspace already paid for; Resend's paid tier wasn't
justified for auth-only volume.

Staging env wired separately on fis-frontend-staging. Prod cutover happens
after staging verification — RESEND_API_KEY secret stays for now as a
two-week rollback window.
```

**Forbidden** in commit/PR: `Co-Authored-By: Claude`, `Generated with Claude Code`, `🤖`, the words "Claude" / "AI" / "assistant" / "LLM" / "Anthropic".

#### PR body contract

```
## What

Swap transactional email transport from Resend → Google Workspace SMTP relay.
Send as `noreply@alchemydev.io` via `smtp-relay.gmail.com:587`.
Also fixes verification-token TTL drift (1h → 24h) to match the email body.

## Why

Cost — Workspace is already paid for; Resend's paid tier wasn't justified
for auth-only mail volume. Resend sandbox was the #1 production blocker
for email verification (emails only reached the Resend account owner).

## How it's wired

- `lib/email.ts` — nodemailer transport, credentials from SMTP_* env.
- `lib/auth.ts` — `emailVerification.expiresIn = 60 * 60 * 24`.
- `.env.example` — documents SMTP_* vars; drops RESEND_API_KEY.

## Staging env

`fis-frontend-staging` env + secret wired out-of-band (or via this PR's
workflow edit — whichever this PR ended up taking).

## Test plan

- [ ] CI backend + frontend jobs green.
- [ ] Staging auto-deploy succeeds.
- [ ] Sign up with a real address on staging; verification email arrives
      from noreply@alchemydev.io; SPF/DKIM pass in full headers.
- [ ] Click verification link within 24h → session issued, `/api/v1/auth/me`
      returns 200 with the user payload.
- [ ] Click link after 24h → BetterAuth default "link expired" UI appears.
- [ ] Confirm no regression in password-reset flow (same transport).

## Rollback

- Revert this PR; `RESEND_API_KEY` secret is still wired on prod.
- Or on staging only: `gcloud run services update fis-frontend-staging
  --remove-env-vars=SMTP_HOST,SMTP_PORT,SMTP_USER,EMAIL_FROM
  --remove-secrets=SMTP_PASSWORD` and redeploy prior revision.

## Not in this PR

- Prod cutover (`fis-frontend`).
- Deleting `RESEND_API_KEY` secret (two-week rollback window).
- DMARC tightening (`p=none` → `p=quarantine`).
```

---

## What NOT to do

- Do not edit `fis-frontend` (prod) env or deploy prod.
- Do not delete `RESEND_API_KEY` Secret Manager secret.
- Do not merge the PR.
- Do not rotate `BETTER_AUTH_SECRET` (invalidates all sessions).
- Do not modify `backend/` — backend doesn't send mail.
- Do not add new dependencies beyond `nodemailer` + `@types/nodemailer`.
- Do not refactor unrelated code in `lib/email.ts` or `lib/auth.ts` while in there — keep the diff tight to Fix #1/#2/#3 from the audit.
- Do not use `git add -A` / `git add .`.
- Do not `git commit --amend` or `git push --force`.
- Do not skip hooks with `--no-verify`.
- Do not send a live test email from the CLI during this run — Arvin tests on staging post-deploy.

---

## Deliverable

Write `reports/smtp-code-swap-<YYYY-MM-DD>.md` with:

1. **Summary** (3 bullets: PR opened on branch X, build/lint status, staging env update path taken).
2. **Files changed** (table: path | purpose | line count delta).
3. **Build + lint output** (verbatim trailing 20 lines of `npm run build` + `npm run lint` — redact any secret-shaped strings).
4. **Cloud Run staging env update** (command run + truncated response; do not print secret values).
5. **Git operations log** (branch created, files staged by name, commit SHA, push result, PR URL).
6. **Commit message verification** (paste of `git log -1 --pretty=full` with confirmation "no AI trailer present").
7. **Open follow-ups** (prod cutover, `RESEND_API_KEY` decommission after 2 weeks, DMARC tightening, any regressions to watch).

End with exactly:
```
SMTP code swap PR opened. Not merged. Staging deploy pending CI.
Report: reports/smtp-code-swap-<YYYY-MM-DD>.md
PR: <URL>
```

---

## Stop conditions

Stop and ask if:
- `reports/smtp-relay-provisioning-2026-04-18.md` does not exist or shows `SMTP_PASSWORD` was not created.
- `gh auth switch --user abedubas-alchemydev` fails (wrong account or not logged in).
- `npm run build` or `npm run lint` fails — do not push broken code.
- `frontend/lib/email.ts` exposes more surface area than Resend `emails.send` (e.g. attachments, custom headers) that doesn't map 1:1 to nodemailer without thought.
- `git status` shows untracked files you don't recognize (workspace artifacts) — do NOT stage them, but flag them.
- `.github/workflows/test.yml` sets env in a way that conflicts with an out-of-band `gcloud run services update`.
- The commit message you drafted contains any forbidden AI attribution phrase — rewrite before committing.

---

## Acknowledgement

After reading the source files but before any write operation, reply in chat with exactly:

```
smtp code swap plan ready.
Files to touch: frontend/lib/email.ts, frontend/lib/auth.ts, frontend/package.json, frontend/package-lock.json, frontend/.env.example<, .github/workflows/test.yml if needed>.
Staging env path: <workflow-edit | out-of-band gcloud update>.
Approve to execute? (yes / modify)
```

Wait for approval. Once approved, execute Work items 1–7 and at the end reply with exactly:

```
SMTP code swap PR opened. Not merged. Staging deploy pending CI.
Report: reports/smtp-code-swap-<YYYY-MM-DD>.md
PR: <URL>
```

Nothing else in chat — all detail lives in the report file.
