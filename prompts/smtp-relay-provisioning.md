# Google Workspace SMTP Relay — Provisioning Prompt (fis-lead-gen)

**How to run this**: in a Claude Code CLI session on `fis-lead-gen`, first set the App Password as an env var, then invoke the prompt:

```bash
export SMTP_APP_PASSWORD='xxxxxxxxxxxxxxxx'   # 16 chars, no spaces, single-quoted
```

Then in the CLI:
> Read `prompts/smtp-relay-provisioning.md` and follow it.

---

## Objective

Wire up Google Workspace SMTP relay credentials into GCP Secret Manager so `fis-frontend` can send `noreply@alchemydev.io` transactional mail via `smtp-relay.gmail.com:587` — **without** modifying any application code in this run. Code swap (Resend SDK → nodemailer) happens in a follow-up session once this provisioning is verified.

Source-of-truth docs:
- `plans/email-provider-migration-plan-2026-04-17.md` (§2 prerequisites, §5 acceptance checklist)
- `.auto-memory/project_email_sender.md` (decision: drop Resend, use Workspace SMTP relay)
- `CLAUDE.md` §"Live environment" (project, region, service account values)

## Ground rules

1. **No code changes.** Do not touch `frontend/lib/email.ts`, `frontend/package.json`, `frontend/.env.example`, or any Cloud Run env definition in this run. The only mutations allowed are: creating the `SMTP_PASSWORD` secret, adding a version, and granting IAM.
2. **Never echo the App Password.** Read it from `$SMTP_APP_PASSWORD` only via `printf` piping into `gcloud secrets ... --data-file=-`. Do not `echo` it, do not write it to any file, do not include it in logs, do not commit it. If you must reference it in the report, refer to "the App Password from `$SMTP_APP_PASSWORD`" — never the value.
3. **Use `printf "%s"` (or `echo -n`), not plain `echo`.** Same CRLF-hardening rule as `GEMINI_API_KEY` — trailing newlines corrupt secrets and present as opaque auth failures later.
4. **Idempotent.** If `SMTP_PASSWORD` already exists, add a new **version**; do not recreate the secret. If the IAM binding already exists, do not fail — log "already bound" and move on.
5. **Read-only Workspace checks.** You cannot access Google Workspace Admin console. For any Workspace-side prerequisite, verify by running a side-effect-free probe (DNS lookup, SMTP auth handshake) — do not assume the admin has done it.
6. **File-based output.** All substantive logs / findings go to `reports/smtp-relay-provisioning-<YYYY-MM-DD>.md`. Chat reply ≤ 5 lines.
7. **No commits, no PRs.** This session produces no git mutations.

## Preflight checks (do these first, abort if any fail)

Run these in order. Each is read-only and cheap. Report each as PASS / FAIL / UNKNOWN in the deliverable.

1. **Env var present**
   - Confirm `$SMTP_APP_PASSWORD` is set and is exactly 16 characters (strip spaces first). Do not print the value.
   - Command: `test ${#SMTP_APP_PASSWORD} -eq 16 && echo "length ok" || echo "length wrong"` (after whitespace strip).

2. **`gcloud` authenticated against `fis-lead-gen`**
   - `gcloud config get-value project` must return `fis-lead-gen`.
   - `gcloud auth list --filter=status:ACTIVE --format='value(account)'` must return a non-empty, authorized account.

3. **DNS posture on `alchemydev.io`** (can be done without Workspace Admin access)
   - `dig +short TXT alchemydev.io` — record whether the first TXT starts with `v=spf1` and whether it includes `_spf.google.com`.
   - `dig +short TXT _dmarc.alchemydev.io` — record whether a `v=DMARC1` record exists.
   - DKIM selector lookup: try both common Workspace selectors:
     - `dig +short TXT google._domainkey.alchemydev.io`
     - `dig +short TXT default._domainkey.alchemydev.io`
     Record which (if any) returns a `v=DKIM1` record.
   - If SPF missing or DKIM missing for both selectors → **STOP** and flag. DKIM is a Workspace Admin prerequisite; provisioning the secret without it will produce mail that gets filtered as spam.

4. **SMTP relay reachability & auth** (read-only probe — does NOT actually send mail)
   - Use `openssl s_client -starttls smtp -connect smtp-relay.gmail.com:587 -crlf -quiet` in a scripted session to:
     - Receive the `220` banner.
     - `EHLO alchemydev.io`.
     - `STARTTLS` → re-EHLO.
     - `AUTH LOGIN` with base64 of `noreply@alchemydev.io` and base64 of the App Password.
     - If the server responds `235 2.7.0 Accepted` → auth works, **immediately send `QUIT`**. Do not send `MAIL FROM:` / `DATA`. We are probing auth only; no actual email is sent.
     - If auth is rejected (`535`), record the exact response code and message. Do **not** retry — this likely means (a) SMTP relay service is not enabled in Workspace Admin, (b) App Password is wrong, or (c) 2SV is not active on the Google account. Stop and report.
   - If the session probe is not scriptable cleanly, fall back to `swaks` if installed, using `--quit-after AUTH` so no mail is actually sent.
   - If neither tool is available, skip this step and mark it UNKNOWN — do not install new tools.

5. **Existing Secret Manager state**
   - `gcloud secrets describe SMTP_PASSWORD --project=fis-lead-gen` — does the secret already exist?
   - `gcloud secrets versions list SMTP_PASSWORD --project=fis-lead-gen --limit=5` — how many versions, and when was the latest created?
   - Decide: **create** (if not exists) vs **add-version** (if exists). Record the decision.

6. **Runtime service account existence**
   - `gcloud iam service-accounts describe 136029935063-compute@developer.gserviceaccount.com --project=fis-lead-gen` — confirm it exists and is not disabled.

## Provisioning steps (only run after all Preflight PASS / UNKNOWN-acceptable)

1. **Create or add-version the secret** (pick based on preflight #5):

   *If creating:*
   ```bash
   printf "%s" "$SMTP_APP_PASSWORD" | gcloud secrets create SMTP_PASSWORD \
     --project=fis-lead-gen \
     --replication-policy=automatic \
     --data-file=-
   ```

   *If adding version:*
   ```bash
   printf "%s" "$SMTP_APP_PASSWORD" | gcloud secrets versions add SMTP_PASSWORD \
     --project=fis-lead-gen \
     --data-file=-
   ```

   Capture the returned version number.

2. **Grant `secretAccessor` to the frontend runtime SA** (idempotent):
   ```bash
   gcloud secrets add-iam-policy-binding SMTP_PASSWORD \
     --project=fis-lead-gen \
     --member='serviceAccount:136029935063-compute@developer.gserviceaccount.com' \
     --role='roles/secretmanager.secretAccessor'
   ```

3. **Verify binding landed**:
   ```bash
   gcloud secrets get-iam-policy SMTP_PASSWORD --project=fis-lead-gen \
     --format='value(bindings.members)'
   ```
   Must include `serviceAccount:136029935063-compute@developer.gserviceaccount.com`.

4. **Verify the version is accessible by that SA** (without printing the secret):
   ```bash
   gcloud secrets versions describe <VERSION_NUMBER> \
     --secret=SMTP_PASSWORD --project=fis-lead-gen \
     --format='value(state,createTime)'
   ```
   Must return `ENABLED` + a fresh `createTime`.

5. **Do NOT run `gcloud secrets versions access`** — that would print the secret into the shell history and conversation log.

## What NOT to do

- Do not deploy `fis-frontend` or `fis-frontend-staging`. Env-var wiring (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `EMAIL_FROM`, `--set-secrets SMTP_PASSWORD`) is a separate task and requires the code swap first.
- Do not delete `RESEND_API_KEY`. It stays until the new flow is verified in staging.
- Do not edit `frontend/lib/email.ts`, `frontend/package.json`, or `frontend/.env.example`.
- Do not send an actual email via the relay. Auth probe only (`AUTH LOGIN` + `QUIT`, no `MAIL FROM:`).
- Do not commit, push, or open a PR.
- Do not write the App Password to any file — including the report.
- Do not use `echo` (CRLF); use `printf "%s"` or `echo -n`.

## Deliverable

Write a single markdown report to `reports/smtp-relay-provisioning-<YYYY-MM-DD>.md` with these sections:

1. **Summary** (3 bullets: secret created/updated, IAM binding status, what's gated next).
2. **Preflight results** (table: check | status | evidence). Include DNS lookups verbatim. Redact any IPs or selectors that could constitute a leak; DKIM selector *name* is fine, DKIM *value* truncated to first 20 chars.
3. **Provisioning result** (which path — create vs add-version — and the returned version number).
4. **IAM binding** (before/after policy members for `SMTP_PASSWORD`).
5. **Gaps / follow-ups** (anything marked UNKNOWN in preflight, and the fact that code swap is still pending).
6. **Rollback recipe** (how to undo if the App Password needs to be revoked):
   - Disable the version: `gcloud secrets versions disable <N> --secret=SMTP_PASSWORD --project=fis-lead-gen`.
   - Revoke App Password at https://myaccount.google.com/apppasswords.
   - Generate a new App Password, add a new secret version, re-enable the new version.

At the end, include exactly:
```
SMTP provisioning complete. No code changes. No deploys.
Report: reports/smtp-relay-provisioning-<YYYY-MM-DD>.md
```

## Stop conditions

Stop and ask if:
- `$SMTP_APP_PASSWORD` is not set or is not 16 chars.
- `gcloud` is not authenticated against `fis-lead-gen`.
- DKIM is not resolvable for `alchemydev.io` on either common selector.
- The SMTP `AUTH LOGIN` probe returns `535` — means Workspace-side setup isn't complete.
- `SMTP_PASSWORD` already has ≥ 3 versions (may indicate prior troubleshooting — confirm with user before adding another).
- Any step would require touching application code or deploying a Cloud Run revision.

## Acknowledgement

After preflight finishes but before provisioning mutates anything, reply in chat with exactly this (no other text):

```
SMTP provisioning preflight complete.
Preflight: <N-PASS>/<N-TOTAL> PASS, <N-FAIL> FAIL, <N-UNKNOWN> UNKNOWN.
Proposed action: <create | add-version> SMTP_PASSWORD.
Approve to provision? (yes / modify)
```

Wait for approval. Once approved, execute Provisioning steps 1–4 and at the end reply with exactly:

```
SMTP provisioning complete. No code changes. No deploys.
Report: reports/smtp-relay-provisioning-<YYYY-MM-DD>.md
```

Nothing else — no summary in chat, no pasted log contents, no next steps. Everything belongs in the report file.
