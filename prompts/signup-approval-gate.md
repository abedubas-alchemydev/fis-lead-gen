# Signup Approval Gate + John Cleanup (fis-lead-gen)

**How to run this**: in a Claude Code CLI session on `fis-lead-gen`, just say:

> Read `prompts/signup-approval-gate.md` and follow it.

No env var needed. The John-cleanup SQL starts with a name-search query so Arvin identifies the correct row in Neon before anything is deleted.

---

## Objective

The client requires: (1) John's account deleted and his access revoked immediately, and (2) all future self-signups must be manually approved by an admin before the user can log in. This prompt executes both in one session — John first (DB-only, no code change), then the approval-gate feature as one PR to `develop`.

Target flow after this ships:
- Anyone can fill out `/signup` → BetterAuth creates a `user` row with `status='pending'` → a "waiting for approval" holding page is shown.
- Admins receive an email notifying them of the new pending user.
- Admin logs in → navigates to `/settings/users` → clicks **Approve** or **Reject** on the pending row.
- On approval, `status` flips to `active` and the user can log in on their next attempt. (No magic notification email back to the user in this MVP — admins can send one manually.)
- On rejection, `status` flips to `rejected`. Rejected users cannot log in.
- Until approval, login attempts are blocked at the BetterAuth layer — no session cookie is ever issued to pending/rejected users.

---

## Ground rules

1. **Zero AI attribution in commits and PR.** No `Co-Authored-By`, no "Generated with" footers, no mention of Claude / AI / assistant / LLM / Anthropic anywhere. Verify with `git log -1 --pretty=full` before pushing.
2. **Use `abedubas-alchemydev` gh account.** `gh auth switch --user abedubas-alchemydev --hostname github.com` + `gh auth status` before any `gh` command.
3. **Stage files by name.** Never `git add -A` / `git add .`.
4. **Never skip hooks** (`--no-verify`). Fix root cause, create a new commit — do NOT `--amend`.
5. **Branch off `develop`.** Feature branch: `feature/signup-approval-gate`. PR base: `develop`.
6. **Hard delete for John** — this codebase does not use soft delete anywhere (no `deleted_at`, no `is_deleted` column across backend + frontend). Do not introduce a soft-delete pattern here; `status='rejected'` in the new schema is how future rejections are expressed.
7. **Existing users must not be locked out.** The migration backfills `status='active'` for every pre-existing `user` row. Admins themselves must remain `active` post-migration or the admin UI is unreachable.
8. **Do not touch production** during this run. Only code changes + staging behavior. Prod cutover happens after staging verification.
9. **File-based output.** Substantive logs, migration SQL, diffs → `reports/signup-approval-gate-<YYYY-MM-DD>.md`. Chat reply ≤ 5 lines.
10. **PII handling.** Do not write John's email, name, or user id into any committed file, the main report, the commit message, or the PR body. The only file that contains his identity is the uncommitted cleanup SQL under `reports/` — and that file should be deleted by Arvin after the Neon run.

---

## Files to read first (in this order)

1. `reports/email-auth-audit-2026-04-17.md` — current auth surface.
2. `reports/smtp-code-swap-2026-04-18.md` — email transport, reusable for admin notifications.
3. `CLAUDE.md` — auth model, backend invariants (backend does NOT write to BetterAuth tables — exception for the status field will need evaluation), `role = 'admin'` promote SQL pattern, audit-log usage.
4. `backend/app/models/auth.py` — current BetterAuth user/session bridge.
5. `backend/app/services/auth.py` — cookie validation + `AuthenticatedUser` construction. Check whether it's feasible to surface `status` on `AuthenticatedUser` without violating the "backend only reads auth tables" rule.
6. `backend/alembic/versions/*.py` — find the migration that created `user`; base the new migration on that head.
7. `backend/app/models/__init__.py` + the `audit_log` model — confirm existing audit-log shape; reuse for approval actions.
8. `frontend/lib/auth.ts` — BetterAuth server config. Find the hooks surface (`databaseHooks` or `callbacks` depending on BetterAuth version) — specifically `user.create.before` (set `status='pending'` at insert) and the login path (reject non-active users before session is created).
9. `frontend/lib/email.ts` — reuse nodemailer transport for admin notifications.
10. `frontend/app/(app)/settings/` — existing settings surface. Decide: add `/settings/users` subsection vs new `/admin/users` route. Prefer the existing settings pattern unless it's obviously wrong.
11. `frontend/middleware.ts` — cookie gate; confirm whether a `status != active` check is needed here or if the BetterAuth hook alone is sufficient.
12. `frontend/app/(auth)/signup/page.tsx` + `login/page.tsx` — redirect wiring.

---

## Work items

### Step 0 — Sanity checks (no writes yet)

- [ ] Confirm `gh auth status` shows `abedubas-alchemydev` as active.
- [ ] `gcloud config get-value project` → `fis-lead-gen`.
- [ ] DB access path for John deletion: the CLI **never executes destructive SQL**. It only writes a script for Arvin to run in the Neon SQL Editor (human in the loop). The script starts with a search query so Arvin identifies the correct row — no email has to be pre-known.

### Step 1 — John cleanup (DB-only, not a commit)

Write the following to `reports/.tmp/john-cleanup--DO-NOT-COMMIT.sql` (a scratch artifact; `reports/.tmp/` must never be committed per CLAUDE.md — confirm it's ignored or stage by name only):

```sql
-- =========================================================================
-- JOHN CLEANUP — run in the Neon SQL Editor, NOT from application code.
-- Run each block separately. Review results before proceeding to the next.
-- =========================================================================

-- 1. SEARCH: find candidate rows matching "john" by name or email.
--    Inspect the output and copy the correct `id` value into the variable
--    below before running any DELETE.
SELECT id, email, name, "createdAt", "emailVerified", role
FROM "user"
WHERE name ILIKE '%john%'
   OR email ILIKE '%john%'
ORDER BY "createdAt" DESC;

-- 2. SET the target id. Replace <PASTE-ID-HERE> with the id from step 1.
--    Using a psql \set variable keeps the id in one place and avoids typos
--    in the cascade deletes below. If the Neon SQL Editor does not support
--    \set, hardcode the id in each statement instead.
\set target_id '<PASTE-ID-HERE>'

-- 3. CONFIRM the target row once more before deleting.
SELECT id, email, name, "createdAt", role
FROM "user"
WHERE id = :'target_id';

-- 4. Revoke sessions first so any open browser session is killed on next request.
DELETE FROM session WHERE "userId" = :'target_id';

-- 5. Remove credential rows (BetterAuth password hash lives in account).
DELETE FROM account WHERE "userId" = :'target_id';

-- 6. Remove any pending verification tokens (by the target user's email).
DELETE FROM verification
WHERE identifier = (SELECT email FROM "user" WHERE id = :'target_id');

-- 7. Finally, remove the user row.
DELETE FROM "user" WHERE id = :'target_id';

-- 8. Verify deletion — all three queries should return 0.
SELECT COUNT(*) AS user_remaining    FROM "user"    WHERE id = :'target_id';
SELECT COUNT(*) AS session_remaining FROM session   WHERE "userId" = :'target_id';
SELECT COUNT(*) AS account_remaining FROM account   WHERE "userId" = :'target_id';

-- 9. Post-hoc audit log entry (hash the email so PII isn't stored in plaintext).
--    Replace <ARVIN_USER_ID> with Arvin's own user id (SELECT id FROM "user"
--    WHERE email = 'arvin.edubas15@gmail.com';). Replace <SHA256-OF-JOHN-EMAIL>
--    with `echo -n '<email>' | sha256sum | cut -d' ' -f1` from a terminal.
INSERT INTO audit_log (actor_id, action, target_id, metadata, created_at)
VALUES (
  '<ARVIN_USER_ID>',
  'user_deleted',
  :'target_id',
  '{"email_hash": "<SHA256-OF-JOHN-EMAIL>", "reason": "client request"}'::jsonb,
  NOW()
);
```

**Do NOT execute this SQL from the CLI.** Write the file, reference it in the report, and instruct Arvin to run it in the Neon SQL Editor against the appropriate environment DB.

**If the Neon SQL Editor doesn't support `\set`**, the CLI should produce an alternative block with the `:'target_id'` pattern replaced by a copy-paste placeholder (`<USER-ID>`) that Arvin fills in once after step 1.

Before writing this file, confirm the `audit_log` table's actual column shape (it may use `target_user_id` instead of `target_id`, or a different `metadata` column name). Adapt the INSERT in step 9 to the real schema — inspect `backend/app/models/` first.

### Step 2 — Alembic migration: `user.status`

From `backend/`:
```bash
alembic revision -m "add user.status with pending/active/rejected"
```

Migration body:
- **Upgrade:**
  - Add column `status` to `"user"` table: `VARCHAR(16) NOT NULL DEFAULT 'pending'`. Use a CHECK constraint: `status IN ('pending', 'active', 'rejected')`.
  - Backfill: `UPDATE "user" SET status = 'active';` — every pre-existing user stays active. This is the lock-out guard.
  - Add an index on `status` for the admin list query.
- **Downgrade:** drop the index, drop the column.

Update `backend/app/models/auth.py` to add the `status` field on the User model bridge. Mark it as read-only from the backend's perspective (backend only reads it for `AuthenticatedUser`; writes go through the admin endpoints the frontend calls through the BFF proxy).

**Decision point — who writes `status`:**
- The BetterAuth create hook on the frontend sets `status='pending'` at user insert (frontend writes to the BetterAuth user table — that's fine, frontend is where BetterAuth lives).
- Admin approve/reject actions: two options.
  - **(a) Frontend-only write** via BetterAuth's admin API or a direct DB call from a Next.js route handler. Cleanest. Backend stays pure-reader.
  - **(b) Backend endpoint** (`POST /api/v1/admin/users/<id>/approve`). This **violates CLAUDE.md's rule** "backend must not write to the BetterAuth auth tables — only read sessions to authorize requests." Do not pick (b) without an explicit exception approved by Arvin.
- **Recommendation: (a).** Implement the admin approve/reject as a Next.js route handler in `app/api/admin/users/[id]/approve/route.ts` (and `/reject`), which talks directly to the DB via the frontend's Drizzle/Prisma client (whichever BetterAuth uses — inspect `frontend/lib/auth.ts` or `frontend/lib/db.ts`).

Run the migration locally to confirm it upgrades and downgrades cleanly against a fresh DB (or note in the report that this was skipped because of env constraints and staging auto-apply will cover it).

### Step 3 — BetterAuth hook: set status at create + block login if not active

In `frontend/lib/auth.ts`:
- Add a `databaseHooks.user.create.before` hook that sets `status = 'pending'` on the inserted user object before it hits the DB. (Confirm the exact hook name by checking the installed BetterAuth version — in 1.3.x it's `databaseHooks.user.create.before`.)
- Add a login-block: either (a) a `databaseHooks.session.create.before` that throws if the target user's `status !== 'active'`, or (b) the `emailAndPassword` hook equivalent. Pick whichever one reliably prevents session creation without leaving a dangling session row. Throwing inside the hook should translate to a BetterAuth 403 response, which the login page can display as "Your account is awaiting approval."
- Preserve the existing `emailVerification` config block unchanged (24h TTL, sendOnSignUp, hook wiring).

### Step 4 — Signup UX: holding page

- After a successful POST to `/api/auth/sign-up/email`, redirect the user to `/pending-approval` instead of straight into the app.
- New page: `frontend/app/(auth)/pending-approval/page.tsx`. Copy: "Thanks for signing up. Your account is being reviewed by our team. We'll email you when it's approved. If you haven't verified your email yet, please check your inbox — you'll need to do both." No CTA to log in (login is gated anyway).
- Update middleware: `/pending-approval` is allowed without a session (belongs in the `(auth)` group's allow-list).

### Step 5 — Login error UX

- Login page catches the BetterAuth 403-from-hook and displays a clear error: "Your account is pending admin approval." Distinct from "Invalid credentials." Do not leak whether the account exists (timing-safe: show the same message for any "account exists but not active" state — pending and rejected can share copy to avoid enumerating rejected users).

### Step 6 — Admin UI at `/settings/users`

- Add a new tab/section under the existing `/settings` route (confirm the settings page structure first). Visible only if the current user has `role = 'admin'`.
- Server component that queries:
  ```
  SELECT id, email, name, "createdAt", "emailVerified", status, role
  FROM "user"
  WHERE status = 'pending'
  ORDER BY "createdAt" ASC
  ```
- Render a table with columns: email, name, signup date, email verified (Y/N), and Actions (**Approve** / **Reject** buttons).
- Action buttons call `POST /api/admin/users/<id>/approve` and `POST /api/admin/users/<id>/reject`.
- Add a separate "All users" view (filterable by status) under the same section — useful for finding an existing user to flip back. Paginate if >50.
- Disallow admins from rejecting themselves or their own role row (hard guard on the server, not just the UI).

### Step 7 — Admin notification email

- When `user.create.after` fires (or equivalent), send an email via the existing nodemailer transport to every admin.
- Query: `SELECT email FROM "user" WHERE role = 'admin' AND status = 'active'`.
- Subject: "New fis-lead-gen signup pending approval". Body includes: new user's name, email, signup timestamp, and a direct link to `/settings/users`.
- Failure to send must not block signup — wrap in try/catch, log the error, let the user reach `/pending-approval` regardless.
- Reuse `frontend/lib/email.ts` — add a new exported function `sendAdminApprovalRequestEmail({ newUser, adminEmails })`.

### Step 8 — Audit logging

- On approve: insert into `audit_log` with `action='user_approved'`, `actor_id=<admin id>`, `target_id=<user id>`, `metadata={"previous_status": "pending"}`.
- On reject: same pattern, `action='user_rejected'`.
- On John's deletion (done out-of-band in Step 1): manually insert an audit entry post-hoc via the same SQL file, `action='user_deleted'`, `actor_id=<Arvin's admin id>`, `metadata={"email_hash": "<sha256>", "reason": "client request"}` — **hash the email, don't store it in plaintext** in the audit metadata since the user row is gone.
- Audit-log writes happen from the frontend's admin route handlers (same path as the status mutation) to keep the write-path in one place.

### Step 9 — Tests

- Backend: a unit test in `app/tests/` verifying the migration's CHECK constraint rejects bad values (e.g. `INSERT ... status='foo'` raises). If the test infrastructure can't apply migrations in test, at minimum verify the `User` model's type annotation.
- Frontend: no existing test infra per CLAUDE.md — skip frontend unit tests for this run. Rely on CI build/lint + staging smoke test.

### Step 10 — Local build + lint

```bash
cd frontend
npm install
npm run lint
npm run build
```
Must pass. Do not push if build fails.

```bash
cd ../backend
pytest app/tests/ -v --tb=short
```
Must pass.

### Step 11 — Git + PR

```bash
gh auth switch --user abedubas-alchemydev --hostname github.com
gh auth status

git fetch origin
git checkout -b feature/signup-approval-gate origin/develop

# Stage explicitly by name — adjust the list to the actual files changed:
git add backend/alembic/versions/<new-migration>.py
git add backend/app/models/auth.py
git add frontend/lib/auth.ts
git add frontend/lib/email.ts
git add frontend/app/\(auth\)/pending-approval/page.tsx
git add frontend/app/\(auth\)/signup/page.tsx
git add frontend/app/\(auth\)/login/page.tsx
git add frontend/app/\(app\)/settings/users/page.tsx
git add frontend/app/api/admin/users/\[id\]/approve/route.ts
git add frontend/app/api/admin/users/\[id\]/reject/route.ts
git add frontend/middleware.ts
# Any test files you added.

git status                           # confirm nothing extra is staged — NO SQL files, NO reports/
git commit                           # see commit contract below
git log -1 --pretty=full             # verify no AI trailer
git push -u origin feature/signup-approval-gate

gh pr create --base develop --head feature/signup-approval-gate   # see PR body contract
```

#### Commit message (Arvin's voice, zero AI attribution)

```
Gate new signups on admin approval

Adds user.status column (pending/active/rejected, default pending) and
wires it through BetterAuth so new signups cannot log in until an admin
approves them.

- Alembic migration adds the column with a CHECK constraint and backfills
  every existing user to 'active' so current users aren't locked out.
- BetterAuth user.create.before hook sets status='pending' on signup.
- session.create.before hook throws for non-active users, so no session
  cookie is ever issued to pending or rejected accounts.
- /signup now redirects to a new /pending-approval holding page.
- /login shows a distinct "account pending approval" error for gated
  attempts.
- New admin section at /settings/users lists pending users with approve
  / reject actions. Route handlers write status changes and audit-log
  entries. Admins cannot mutate their own row.
- New nodemailer template emails all admins when a pending signup
  arrives. Send failure does not block signup.

Client requirement — self-signup was letting uninvited users into the
dashboard.
```

**Forbidden anywhere in commit/PR:** `Co-Authored-By: Claude`, `Generated with Claude Code`, `🤖`, "Claude", "AI", "assistant", "LLM", "Anthropic".

#### PR body

```
## What

Every new signup is now held in a `pending` state until an admin clicks
Approve at /settings/users. Login is blocked for non-active users at the
BetterAuth layer, so no session cookie is ever issued.

## Why

Client flagged that a user registered and got straight into the dashboard.
They've asked for manual approval by an admin on every new account.

## Schema change

`user.status` VARCHAR(16) NOT NULL DEFAULT 'pending', CHECK IN
('pending','active','rejected'). Existing rows backfilled to 'active'
during the migration.

## Flow

- /signup → `user.create.before` sets status=pending → holding page
- /login for non-active users → 403 with "account pending approval"
- /settings/users (admin only) → approve/reject

## Notification

Admins get an email on each new signup via the existing nodemailer
transport. Failure to notify does not block signup.

## Audit trail

user_approved / user_rejected entries written to audit_log from the admin
route handlers.

## Not in this PR

- No user-facing notification on approve/reject (admins can message manually).
- No bulk approve.
- No self-service "request approval again" for rejected users.
- Prod cutover — staging verification first.

## Test plan

- [ ] Sign up a new account on staging. Expect /pending-approval page,
      no session cookie, admin notification email arrives.
- [ ] Attempt to log in with that account → 403 "pending approval" copy.
- [ ] As admin, /settings/users shows the pending user. Click Approve.
- [ ] Log in with the newly-approved account → dashboard loads.
- [ ] Reject a second test account → its login shows the same gated copy.
- [ ] Confirm audit_log has entries with correct actor_id and target_id.
- [ ] Existing active users can still log in normally (backfill worked).
- [ ] Admin cannot reject their own row (UI disabled, server guard also blocks).

## Rollback

`alembic downgrade -1` on the backend removes the column. Revert this PR
removes the frontend gating code. BetterAuth tables remain intact.
```

---

## What NOT to do

- Do not execute John's cleanup SQL from the CLI — that's Arvin's manual step in Neon.
- Do not write John's email, name, or user id into any committed file, PR description, commit message, or the main report. The `reports/.tmp/` scratch SQL is the only place his identity appears, and that directory must not be committed.
- Do not add backend endpoints that WRITE to the `user` / `session` / `account` / `verification` tables — violates CLAUDE.md invariant. Approve/reject is frontend-route-handler work.
- Do not touch `fis-frontend` (prod) env or deploy.
- Do not introduce a soft-delete framework — project doesn't use it.
- Do not change the `emailVerification` settings (TTL, sendOnSignUp) — out of scope.
- Do not loosen the export PRD rules while refactoring nearby.
- Do not merge the PR — Arvin reviews and merges.
- Do not rotate `BETTER_AUTH_SECRET`.
- Do not `git add -A`, `git commit --amend`, `git push --force`, or `--no-verify`.

---

## Deliverable

Write `reports/signup-approval-gate-<YYYY-MM-DD>.md` with:

1. **Summary** (PR URL, branch, staging auto-deploy pending).
2. **John cleanup** — path to the SQL file + instruction for Arvin to run it in Neon. DO NOT include `$JOHN_EMAIL` in this section; refer to "the target user flagged by the client."
3. **Migration** — path to the new Alembic file + a verbatim copy of its upgrade/downgrade SQL (redact nothing — it's schema only).
4. **Hook wiring** — the exact BetterAuth hook names + line refs used in `lib/auth.ts`.
5. **Admin UI** — route path, access guard, list of rendered columns.
6. **Audit log entries** — `action` values, payload shape.
7. **Build + test output** — trailing lines of `npm run build`, `npm run lint`, `pytest`.
8. **Git ops log** — branch, staged files (by name), commit SHA, push result, PR URL.
9. **Commit verification** — paste of `git log -1 --pretty=full`, explicit "no AI trailer present."
10. **Follow-ups** — listed in "Not in this PR" section of the PR body + prod cutover.

End with exactly:
```
Signup-approval gate PR opened. Not merged. Staging deploy pending CI.
John cleanup SQL ready at reports/.tmp/john-cleanup--DO-NOT-COMMIT.sql — run manually in Neon.
Report: reports/signup-approval-gate-<YYYY-MM-DD>.md
PR: <URL>
```

---

## Stop conditions

Stop and ask if:
- `gh auth status` shows an account other than `abedubas-alchemydev`.
- The current BetterAuth version's hook API differs materially from what this prompt assumes (e.g. hook names moved between majors) — write the actual API surface into the report and pause.
- The existing `/settings` page has no obvious place to add a `/users` subsection and adding one would require a significant layout refactor.
- `audit_log` model doesn't support the payload shape this prompt assumes — report the actual columns and pause before adapting.
- The migration errors on backfill (e.g. because there's a user already in a weird state).
- `npm run build` or `pytest` fails — do NOT push.

---

## Acknowledgement

After reading the source files but before any write operation, reply in chat with exactly:

```
signup-approval-gate plan ready.
John cleanup: SQL written to reports/.tmp/john-cleanup--DO-NOT-COMMIT.sql — starts with ILIKE '%john%' search; Arvin identifies correct row before any DELETE.
Migration: user.status (pending/active/rejected, default pending, backfill active).
Hook surface: <BetterAuth hook names resolved from installed version>.
Admin UI path: <chosen route>.
Approve to execute? (yes / modify)
```

Wait for approval. Once approved, execute Steps 1–11 and at the end reply with exactly:

```
Signup-approval gate PR opened. Not merged. Staging deploy pending CI.
John cleanup SQL ready at reports/.tmp/john-cleanup--DO-NOT-COMMIT.sql — run manually in Neon.
Report: reports/signup-approval-gate-<YYYY-MM-DD>.md
PR: <URL>
```

Nothing else in chat — all detail lives in the report file.
