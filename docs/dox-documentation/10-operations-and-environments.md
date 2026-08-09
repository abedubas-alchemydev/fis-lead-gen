# 10 — Operations & Environments

[← User Guide](09-user-guide.md) | [Index](README.md) | [Next: Legal Considerations →](11-legal-considerations-for-counsel.md)

---

## 10.1 Environments and release flow

| | **Staging** | **Production** |
|---|---|---|
| Branch | `develop` | `main` |
| Frontend | Cloud Run `fis-frontend-staging` | Cloud Run `fis-frontend` |
| Backend | Cloud Run `fis-backend-staging` | Cloud Run `fis-backend` |
| Database | Neon (staging instance) | Neon (separate production instance) |
| Role | Daily work, client demos, validation | Customer-facing |

**Release discipline:** all feature work merges by pull request into `develop` (branch protection: one approving review) and deploys automatically to staging. Production releases are **deliberate, explicitly named events** — a `develop → main` merge is never made as a side effect of routine work. The production database was initialized from a full clone of staging on 2026-06-03; environment data has since diverged normally.

## 10.2 CI/CD pipeline (`.github/workflows/test.yml`)

On every PR and push to `develop`/`main`:

1. **Backend unit tests** (Python 3.11, providers stubbed).
2. **Backend integration tests** against a real **PostgreSQL 15 + pgvector** service container, including a full **`alembic upgrade head`** — schema migrations are proven on every change.
3. **Frontend** install, lint, production build (Node 22).

On a **push** (not PRs), after all tests pass, the deploy job (GCP auth via Workload Identity Federation — no exported keys):

4. Runs `alembic upgrade head` against the target environment's live database **before** shipping code (a failed migration aborts the deploy).
5. Builds and pushes the backend image, deploys Cloud Run (2 vCPU / 2 GiB, min 1 instance, 3600s timeout for SSE streams), binding all secrets from **Secret Manager** (`--set-secrets=…`).
6. Deploys the frontend service the same way.
7. (Staging) updates the three batch **Cloud Run Jobs**: `fis-advisor-gap-fill-staging`, `fis-backfill-all-staging`, `fis-extract-new-bds-staging`.

**Concurrency:** PR runs cancel superseded runs; pushes queue. Operationally important: a second merge to `develop` while a deploy is mid-flight cancels the first deploy — after batched merges, verify the live revision rather than trusting the first run's green check.

## 10.3 Scheduled and batch work

| Job | Schedule / trigger | Purpose |
|---|---|---|
| `fis-extract-new-bds-nightly` (Cloud Scheduler → Cloud Run Job) | 4 AM ET nightly | Discover newly registered broker-dealers from FINRA, insert with Form BD dates, embed into semantic search |
| Daily filing monitor | Daily (admin/scheduler) | New-filing alerts; auto-queue financial extraction for watched firms |
| Form 4 watcher | Daily | Insider-transaction ingestion |
| `fis-advisor-gap-fill-staging` / `fis-backfill-all-staging` | Executed on demand (`gcloud run jobs execute`) | Batch enrichment/backfill sweeps with per-run bounds |
| Embedding backfill | Admin endpoint / scripts / nightly hook | Semantic-index freshness (content-hash deduped) |

Scheduler/job calls into the API authenticate with verified Google OIDC tokens ([Doc 08](08-security.md) §8.1). Every pipeline writes `pipeline_run` rows (status, counts, parent/child); a startup reaper marks runs orphaned by instance restarts, and anything `running` for over an hour without completion is treated as stale. Admins watch this on `/settings`; Doxie exposes it via `get_data_freshness`.

## 10.4 Database administration

- **Migrations:** Alembic, 78 revisions as of this writing; CI-tested per change; applied to the live DB only by the deploy pipeline (with the same discipline for both environments).
- **Backups:** Neon provides point-in-time restore on its side; in addition, operator-made logical dumps (`pg_dump`, custom format) are kept under `db-backups/` (this directory and procedure were used for the 2026-06-03 staging→production clone: dump → `pg_restore --clean` → sequence sync; documented in-folder). No automated dump rotation is configured.
- **Extensions:** `pgvector` (embeddings); created by migration 0028.
- **Access:** application connects with a Secret-Manager-held URL; ad-hoc human access is by the operators only, against staging by default (production queries require an explicit decision).

## 10.5 Configuration management

All runtime behavior is environment-driven (`backend/app/core/config.py`, ~100 settings): provider keys, chain orders (`EMAIL_ENRICHMENT_CHAIN`, `CONTACT_DISCOVERY_CHAIN`), confidence floors, rate limits, feature toggles (`WEB_FALLBACK_ENABLED`, `LLM_USE_FILES_API`, `DATA_SOURCE_MODE`). Cloud Run env/secret bindings are authoritative per environment; flags set directly via `gcloud run services update` persist across deploys (the workflow only manages the bindings it declares). Frontend feature flags (`frontend/lib/feature-flags.ts`) are compile-time constants (`EMAIL_EXTRACTION_ENABLED=true`, `DATA_EXPORT_ENABLED=false`).

## 10.6 Monitoring & operational visibility

- Cloud Run request/instance logs and job execution logs (GCP).
- `pipeline_run` + admin UI for pipeline health; extraction analytics for provider yield; `audit_log`/`user_activity` for user-side events.
- Live alert stream keepalives; SSE windows capped (55 min) under the 3600s service timeout.
- Known operational watch-items: SEC rate-limiting of the shared egress IP during bulk runs (mitigated by Retry-After honoring + caching), SerpAPI monthly quota (search-dependent features degrade when capped), Apollo credit exhaustion surfacing as empty enrichments (HTTP 422), and provider webhooks requiring the public proxy URL.

## 10.7 Incident-relevant facts (quick reference)

- **Where are secrets?** GCP Secret Manager (runtime), GitHub Actions secrets (CI). Rotating a provider key = update secret, redeploy/restart.
- **How to take an environment to a known schema?** `alembic upgrade head` is idempotent; CI proves every migration against pgvector/PG15.
- **How to disable a provider quickly?** Unset/empty its key (chains skip missing providers) or update the chain env var; no code change required.
- **How to lock out a user immediately?** Admin sets status inactive (sessions are refused at creation and verification); single-session policy already limits concurrent access.
- **Audit questions** ("who emailed whom?", "who viewed what?", "what changed?"): `outreach_send`, `user_activity`/`user_visit`, `audit_log`, `pipeline_run` respectively.

---

[← User Guide](09-user-guide.md) | [Index](README.md) | [Next: Legal Considerations →](11-legal-considerations-for-counsel.md)
