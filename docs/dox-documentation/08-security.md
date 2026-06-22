# 08 — Security

[← AI Features](07-ai-features-and-llm-data-flows.md) | [Index](README.md) | [Next: User Guide →](09-user-guide.md)

---

This document describes the security architecture: identity and access, session control, client-side data protection, audit logging, secrets, network posture, and database security — followed by an honest list of known weaknesses and accepted risks (§8.9).

## 8.1 Authentication

**Stack:** Better Auth v1.3.x on the frontend (`frontend/lib/auth.ts`), with the FastAPI backend independently verifying every request's session.

- **Methods:** email/password (8–128 chars; reset via emailed link; hashing handled by the framework) and OAuth sign-in via Google, Microsoft (tenant `common`), and Yahoo (OIDC).
- **Signup approval gate:** new accounts are created with status `pending` and role `viewer`. A hook emails all active administrators an approval request; **session creation is refused server-side until an admin activates the account** (`session.create.before` → FORBIDDEN). Email verification flips `email_verified` only — it does not bypass approval.
- **Session model:** opaque HMAC-SHA256-signed token in an **HTTP-only cookie** (`better-auth.session_token`); secure cookies enforced in production. 7-day absolute TTL with 24-hour rolling refresh; session rows record IP, user-agent, and last-activity (bumped at most once per 60s).
- **Single active session:** on each successful login, all of the user's *other* sessions are deleted, the event is audited (`security.forced_logout_other_devices`), and a best-effort sign-in alert email (with new IP/user-agent) is sent. One device at a time, by policy.
- **Backend verification (`backend/app/services/auth.py`):** every API request's cookie is HMAC-verified against `BETTER_AUTH_SECRET`, looked up in the `session` table, and expiry-checked; the user (role + feature permissions) is loaded with it. The backend trusts no header claims — only the verified session row.
- **Machine callers:** Cloud Scheduler/Jobs authenticate to pipeline endpoints with **Google-signed OIDC tokens**, verified for both the service-account email and the audience claim; failures are 403. Audit attribution records `sa:<email>`.

## 8.2 Authorization

Two layers, enforced in the backend (UI gating is cosmetic only):

1. **Role:** `admin` (full access, including user management, settings, pipelines, all-users outreach history) vs `viewer`.
2. **Per-feature permissions:** a JSONB list on the user row (`MASTER_LIST`, `INVESTMENT_ADVISORS`, `INVESTORS`, `ALERTS`, `EMAIL_EXTRACTOR`, `SENT_OUTREACH`, `OUTREACH_CONTACTS`, `MY_FAVORITES`, `VISITED_FIRMS`, `VAULT`, …) checked by `ensure_feature()` on endpoints **and on every Doxie tool**. Admins bypass feature checks.

**Tenant/ownership scoping:** user-owned resources (Vault folders/files, favorite lists, drafts, sends, conversations) are queried by `user_id`; cross-user access returns **404 rather than 403** so resource existence is not disclosed.

## 8.3 Webhook security

The only unauthenticated write endpoint is Apollo's phone-reveal callback. Because Apollo does not sign webhooks, the URL embeds a random secret path segment; the handler compares it in **constant time** against `APOLLO_WEBHOOK_SECRET` and answers 404 (not 401) on mismatch, making the endpoint unguessable and unenumerable. Payloads only ever update phone fields matched by `apollo_person_id`.

## 8.4 Client-side data protection (anti-leak hardening)

Because the dataset is the licensed asset, the frontend applies unusual-for-SaaS protections (`frontend/components/security/`):

- **Identity watermark** overlaid across the UI (user email/ID), discouraging screenshots.
- **Copy/cut blocked** outside form fields, with clipboard clearing; right-click blocked; text selection disabled outside inputs; image/element drag blocked.
- **Print blocked** (Ctrl+P and `beforeprint` lock the document); **PrintScreen** clears the clipboard and blurs the app; the app also blurs when the tab loses focus.
- **Save-page (Ctrl+S) blocked.** Developer tools are **deliberately not blocked** (a prior detector was removed as ineffective theater).
- Violations are reported (1-in-10 sampled) to `/api/security/event` (sendBeacon; 60 events/min/session server cap) and written to the audit log with IP/user-agent.

These are deterrence controls, not absolute prevention — acknowledged in §8.9.

## 8.5 Secrets management

- **Runtime secrets** (DB URLs, all provider keys, webhook secret, auth secret) live in **GCP Secret Manager** and are bound at deploy time via `gcloud run deploy --set-secrets=NAME=SECRET:latest` (visible in `.github/workflows/test.yml`). They are not in the repository.
- **CI secrets** (deploy credentials, migration DB URLs) are GitHub Actions secrets; GCP auth uses **Workload Identity Federation** (OIDC), not exported service-account keys.
- **Local development** uses `.env` files (gitignored).
- **Known caveat:** `backend/app/core/config.py` ships a hard-coded *development fallback* for the auth secret ("deshorn-local-dev-…"). Deployed environments override it via Secret Manager, but the fallback means a misconfigured deployment would still boot rather than fail closed. Listed in §8.9.

## 8.6 Data at rest and in transit

- **In transit:** HTTPS/TLS terminates at Google's load balancing for both Cloud Run services; backend egress to providers is HTTPS; Yahoo SMTP uses STARTTLS.
- **Backend reachability:** the backend Cloud Run service cannot be invoked by `allUsers` (GCP org policy); the public internet reaches it only through the frontend's server-side proxy. Webhook traffic rides the same path via `PUBLIC_BASE_URL`.
- **At rest:** Neon (managed Postgres) and GCS provide platform-level storage encryption per their documentation. The application adds **no field-level encryption**; notably, **linked-mailbox OAuth access/refresh tokens are stored as plaintext columns** in the `account` table. Compensating controls are network isolation, Secret-Manager-held DB credentials, and least access; the residual risk is listed in §8.9.
- **Vault downloads** use 5-minute signed GCS URLs; bucket objects are not public.

## 8.7 Audit logging

`audit_log` (user_id, action, JSON details, timestamp) records: logins/logouts (IP, user-agent, session id), forced logouts from single-session enforcement, sampled client-side security events (copy/print/right-click/clipboard/shortcut), and system/pipeline actions (nullable user for service-account work). `user_activity` separately records navigation/search/usage per user, and `user_visit` records firm-page visits; admins review both per user in Settings. Outreach has its own complete audit (`outreach_send`, [Doc 06](06-outreach-and-email-compliance.md) §6.5). Audit writes are non-blocking (failures never abort the user action) and there is currently **no retention policy** (§8.9).

## 8.8 Application-security practices

- **CI gate on every PR and push:** backend unit tests, backend integration tests against a real PostgreSQL 15 + pgvector service **including `alembic upgrade head`** (schema migrations are exercised before they ever reach an environment), frontend lint/build. Deploys run only on branch pushes after all tests pass; migrations run against the live DB before new code ships.
- **Input validation:** pydantic schemas on all endpoints; SQLAlchemy parameterized queries (no string SQL); file-type/size allowlists on Vault uploads; chat message/request size caps.
- **Error discipline:** provider-agnostic error surfaces; opaque 404s for cross-tenant probes; webhook 404-masking.
- **No `eval`/dynamic code paths; LLM output is schema-validated** for extraction and tool arguments are schema-checked before execution.

## 8.9 Known weaknesses and accepted risks (current, factual)

| # | Item | Status / compensating control |
|---|---|---|
| 1 | OAuth tokens (and Better Auth `id_token`s) stored plaintext at application layer | Platform-level encryption + isolated network + secretized DB credentials; field-level encryption not implemented |
| 2 | Dev-fallback auth secret constant in `config.py` | Overridden by Secret Manager in deployed envs; should fail-closed instead |
| 3 | No audit-log/user-activity retention policy (unbounded growth) | Operational task; DB-level policy possible |
| 4 | No application-layer API rate limiting (beyond the security-event endpoint) | Small authenticated user base; Cloud Run concurrency limits; provider quotas on egress |
| 5 | CORS configured with wildcard methods/headers (origins are allowlisted) | Cookie-credentialed CORS restricted by origin list |
| 6 | Client-side copy/print guards are deterrence, not prevention (devtools intentionally allowed) | Watermarking + audit trail of violation events; acceptance documented |
| 7 | Doxie tool calls not persisted for audit | Sends independently audited; gap noted in [Doc 11](11-legal-considerations-for-counsel.md) §9 |
| 8 | Session cookie SameSite left at framework default | HTTP-only + secure flags set in production; explicit SameSite worth pinning |
| 9 | Demo-mode relaxations live on staging (cooldowns removed, always-enabled buttons) | Marked with in-code revert notes; staging only |

---

[← AI Features](07-ai-features-and-llm-data-flows.md) | [Index](README.md) | [Next: User Guide →](09-user-guide.md)
