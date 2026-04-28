# Delete User (search → confirm → cascade delete + audit log)

**How to run this**: in a Claude Code CLI session on `fis-lead-gen`, say:
> Read `prompts/delete-user.md` and follow it.

The CLI has full access (gcloud, Secret Manager, Neon). It handles every lookup itself — DB URL fetch, admin listing, candidate search — and only pauses for two things: (a) the combined "pick actor admin id + search term" reply, and (b) the final target-id confirmation before any DELETE.

---

## Objective

Delete a BetterAuth user account — cascading through `session`, `account`, `verification`, and `user` — and write a privacy-preserving audit entry. The authoritative use case today is the John cleanup flagged by the client (Deshorn's 6:55 AM message), but this prompt is reusable for any admin-initiated user removal.

**This prompt is intentionally destructive.** It deletes rows from production with no soft-delete safety net. The CLI must pause for explicit confirmation of the exact user id before any DELETE runs.

---

## Ground rules

1. **Destructive on prod Neon, no undo.** The Neon plan may or may not have point-in-time recovery; do not assume it does. Once the DELETE runs, the row is gone.
2. **Search first, then delete.** Never delete by name — always delete by a specific `id` that the user has visually confirmed from the search output.
3. **Single confirmation gate.** After presenting the search results, stop and wait for Arvin to reply with the exact target id (or "abort"). Do not delete on "yes" alone — require the id to be echoed back so there's no ambiguity.
4. **Never echo the DATABASE_URL** into chat, into a report, or into any file. Feed it to `psql` via `-d "$DATABASE_URL_BACKEND"` only, or via a Python `psycopg2.connect(dsn=os.environ["DATABASE_URL_BACKEND"])`. Unset the env var when done.
5. **Staging uses the same Neon DB as prod** per `CLAUDE.md`. There is no "staging practice run" here — the DB is shared. Plan accordingly.
6. **No git operations.** This prompt produces no commits, no branches, no PRs. Pure ops.
7. **File-based output** for the summary — chat reply ≤ 5 lines. The report lives at `reports/delete-user-<YYYY-MM-DD>.md` and **must not** contain the user's email, name, id, or DATABASE_URL. Only the SHA-256 of the email (for cross-referencing with the audit-log row), the action, counts, and timestamps.
8. **Delete the uncommitted scratch SQL** at the end. Nothing about the deleted user should persist on Arvin's laptop outside the audit_log row.

---

## Files to read first

1. `CLAUDE.md` — confirm auth model invariants (backend only reads auth tables; this ops task doesn't violate that because it runs as DBA, not as the backend service).
2. `backend/app/models/audit_log.py` (or wherever it lives) — resolve the real column names: `user_id` vs `actor_id`, `details` TEXT vs `metadata` JSONB, `timestamp` vs `created_at`. The signup-approval-gate prompt found these columns are `user_id / action / details TEXT / timestamp`; confirm before the INSERT.
3. `reports/signup-approval-gate-2026-04-18.md` — prior work, same schema shape.

---

## Steps

### Step 0 — Preflight (no user interaction)

- [ ] `gcloud config get-value project` returns `fis-lead-gen`.
- [ ] `gcloud auth list --filter=status:ACTIVE --format='value(account)'` returns a non-empty, authorized account.
- [ ] `psql --version` — confirm `psql` is installed. If not, install Python `psycopg2-binary` (`pip install psycopg2-binary --break-system-packages` per `CLAUDE.md`) and run every SQL step through a short Python script instead. Either tool works — pick once and stick with it.

### Step 1 — Fetch the DATABASE_URL from Secret Manager (no user interaction)

```bash
# Pipe directly into env; do not cat, do not print.
export DATABASE_URL_BACKEND="$(gcloud secrets versions access latest \
  --secret=DATABASE_URL_BACKEND --project=fis-lead-gen)"

# Strip the SQLAlchemy driver prefix so psql/psycopg2 accepts it:
export DATABASE_URL_BACKEND="${DATABASE_URL_BACKEND/postgresql+psycopg:\/\//postgresql:\/\/}"

# Sanity-check length, never echo the value itself.
echo "DB URL length: ${#DATABASE_URL_BACKEND}"   # expect >50 chars
```

### Step 2 — List admin users (no user interaction)

The CLI does this lookup itself. Don't ask Arvin for his email — he may have multiple accounts, or the admin may be under his Workspace identity instead of the one hinted at in comments. Resolve from the database.

```sql
SELECT id, email, name, "createdAt", role
FROM "user"
WHERE role = 'admin'
ORDER BY "createdAt" ASC;
```

Store the result for presenting in Step 3. **If zero admins exist**, stop and report — the audit log needs a valid admin actor. **If exactly one admin exists**, auto-pick it as the actor and skip the admin-picking part of Step 3; still do the search-term pause.

### Step 3 — Run the search AND ask for both actor id + target in one pause

Ask Arvin for the search term inline, then run the search immediately. Present both the **admin list from Step 2** and the **search candidates** in one message. Do NOT split this into multiple back-and-forths.

Step 3a — Ask for search term only:

```
Before I search: what name or email fragment should I look up?
(e.g. "john" — this prompt is reusable for any admin cleanup)
```

Step 3b — Once the term arrives, run the search:

```sql
SELECT id, email, name, "createdAt", "emailVerified", role
FROM "user"
WHERE name ILIKE '%<TERM>%'
   OR email ILIKE '%<TERM>%'
ORDER BY "createdAt" DESC;
```

Step 3c — Present everything at once in chat as two markdown tables:

```
### Admins (pick the actor for the audit log)

| id | email | role |
|---|---|---|
| <uuid-1> | arvin@... | admin |
| <uuid-2> | arvin.edubas15@... | admin |

### Search candidates for "<TERM>" (pick the row to delete)

| id | email | name | created | verified |
|---|---|---|---|---|
| <uuid-A> | john@... | John Smith | 2026-04-17 | yes |
| <uuid-B> | j.doe@... | Johnny Doe | 2026-04-10 | no |

Reply with exactly two lines:
  actor: <admin-id>
  target: <candidate-id-or-"abort">
```

Do NOT write this table output to the report file (PII).

### Step 4 — Parse the confirmation reply (mandatory pause)

Accept Arvin's reply only in the exact format above. Parse:
- `actor:` line → must exactly match one of the admin ids from Step 2. Store as `ARVIN_ID`.
- `target:` line → must exactly match one of the candidate ids from Step 3b, OR the word `abort`.

Reject on any mismatch: ask once to re-send in the correct format; if still malformed, abort safely.

If `target: abort` → skip to Step 8.
If both fields valid → proceed to Step 5.

### Step 5 — Cascade delete inside a transaction

Wrap the whole thing in a `BEGIN;` / `COMMIT;` so a failure mid-cascade rolls back cleanly.

```bash
TARGET_ID='<confirmed-id-from-step-4>'

psql "$DATABASE_URL_BACKEND" <<SQL
BEGIN;

-- Snapshot the email for the audit hash BEFORE the user row is gone.
SELECT email FROM "user" WHERE id = '${TARGET_ID}';   -- captured into $TARGET_EMAIL below

DELETE FROM session      WHERE "userId"    = '${TARGET_ID}';
DELETE FROM account      WHERE "userId"    = '${TARGET_ID}';
DELETE FROM verification WHERE identifier  = (SELECT email FROM "user" WHERE id = '${TARGET_ID}');
DELETE FROM "user"       WHERE id          = '${TARGET_ID}';

COMMIT;
SQL
```

Actually — better pattern, since we need to hash the email AFTER the row is gone: capture the email in a shell var first, THEN run the transaction. Rewrite:

```bash
TARGET_ID='<confirmed-id-from-step-4>'
TARGET_EMAIL="$(psql "$DATABASE_URL_BACKEND" -At -c \
  "SELECT email FROM \"user\" WHERE id = '${TARGET_ID}';")"

if [ -z "$TARGET_EMAIL" ]; then
  echo "Target user not found — aborting"; exit 1
fi

psql "$DATABASE_URL_BACKEND" <<SQL
BEGIN;
DELETE FROM session      WHERE "userId"   = '${TARGET_ID}';
DELETE FROM account      WHERE "userId"   = '${TARGET_ID}';
DELETE FROM verification WHERE identifier = '${TARGET_EMAIL}';
DELETE FROM "user"       WHERE id         = '${TARGET_ID}';
COMMIT;
SQL
```

### Step 6 — Verify the cascade cleared cleanly

```bash
psql "$DATABASE_URL_BACKEND" <<SQL
SELECT COUNT(*) AS user_remaining    FROM "user"    WHERE id     = '${TARGET_ID}';
SELECT COUNT(*) AS session_remaining FROM session   WHERE "userId" = '${TARGET_ID}';
SELECT COUNT(*) AS account_remaining FROM account   WHERE "userId" = '${TARGET_ID}';
SELECT COUNT(*) AS verif_remaining   FROM verification WHERE identifier = '${TARGET_EMAIL}';
SQL
```

All four counts must be `0`. If any is non-zero, stop and report — something cascaded incorrectly.

### Step 7 — Audit log entry

Compute the email SHA-256 and INSERT:

```bash
EMAIL_HASH="$(printf '%s' "$TARGET_EMAIL" | sha256sum | awk '{print $1}')"

# Build the details JSON without putting the plaintext email anywhere.
DETAILS_JSON=$(printf '{"target_user_id":"%s","email_hash":"%s","reason":"client request - admin initiated cleanup"}' \
  "$TARGET_ID" "$EMAIL_HASH")

psql "$DATABASE_URL_BACKEND" <<SQL
INSERT INTO audit_log (user_id, action, details, timestamp)
VALUES ('${ARVIN_ID}', 'user_deleted', '${DETAILS_JSON}', NOW());
SQL
```

Adjust column names if they differ from what the signup-approval-gate report resolved (`user_id` / `action` / `details TEXT` / `timestamp`). If `details` is JSONB, cast with `::jsonb`.

Verify the row landed:
```bash
psql "$DATABASE_URL_BACKEND" -c "SELECT id, action, timestamp FROM audit_log WHERE user_id = '${ARVIN_ID}' AND action = 'user_deleted' ORDER BY timestamp DESC LIMIT 1;"
```

### Step 8 — Cleanup

```bash
unset DATABASE_URL_BACKEND
unset TARGET_ID
unset TARGET_EMAIL
unset EMAIL_HASH
unset ARVIN_ID
unset TERM

# If there's a scratch SQL file from an earlier run:
rm -f reports/.tmp/john-cleanup--DO-NOT-COMMIT.sql
rm -f reports/.tmp/delete-user--DO-NOT-COMMIT.sql
```

---

## What NOT to do

- Do not delete without a confirmed target id (Step 4 gate).
- Do not echo `$DATABASE_URL_BACKEND`, `$TARGET_EMAIL`, or the search-result rows into any file.
- Do not write John's (or any deleted user's) email, name, or id into the report markdown — only the email SHA-256 is acceptable.
- Do not run this against anything other than the resolved `DATABASE_URL_BACKEND` (no staging-specific DB exists per CLAUDE.md).
- Do not install psql via `apt` without confirming it's not already present; fall back to `psycopg2-binary` via pip if needed.
- Do not commit, do not open a PR, do not push.
- Do not touch `audit_log` rows that already exist.
- Do not rotate `BETTER_AUTH_SECRET`.

---

## Deliverable

Write `reports/delete-user-<YYYY-MM-DD>.md` with:

1. **Summary** — action taken (user_deleted or aborted), timestamp, actor email.
2. **Target identification** — `email_hash` only (SHA-256). No plaintext email, no name, no id.
3. **Cascade counts** — before/after for session, account, verification, user (all "after" should be 0).
4. **Audit log row** — the `audit_log.id` of the new row, `action`, `timestamp`.
5. **Cleanup confirmation** — env vars unset, scratch files removed.

End with exactly:
```
User deletion complete. Audit logged. DB cleaned.
Report: reports/delete-user-<YYYY-MM-DD>.md
```

Or on abort:
```
User deletion aborted. No DB mutations. No audit row.
```

---

## Stop conditions

Stop and ask if:
- `gcloud` isn't authenticated against `fis-lead-gen`.
- `DATABASE_URL_BACKEND` fetch fails or the URL is empty.
- Arvin's user id can't be found or his role isn't `admin`.
- Search returns zero candidates (ask Arvin to refine the term).
- Search returns more than 10 candidates (ask Arvin to narrow — avoid accidentally hitting the wrong "John").
- The user replies with something other than a valid id or "abort" at the Step 4 gate.
- Any cascade DELETE count in Step 6 is non-zero after the transaction commits.
- `audit_log` column names don't match the expected shape — pause and adapt.

---

## Acknowledgement

Before Step 5 (first DELETE), reply in chat with exactly:

```
delete-user candidates found: <N>
Arvin actor id resolved: <yes|no — stop if no>
Target id required to proceed. Reply with either:
  (a) the full id of the row to delete, or
  (b) "abort".
```

Wait for Arvin's reply. On valid id → execute Steps 5–8, then reply with the trailing block. On "abort" → skip to Step 8, then reply with the abort trailing block. Nothing else in chat — detail belongs in the report file.
