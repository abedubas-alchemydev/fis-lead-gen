# 02 — System Architecture

[← Product Overview](01-product-overview.md) | [Index](README.md) | [Next: Data Sources →](03-data-sources-and-provenance.md)

---

## 2.1 High-level shape

DOX is a conventional two-tier web application with background data pipelines, deployed on Google Cloud Platform:

```
                       ┌──────────────────────────── Google Cloud (us-central1) ───────────────────────────┐
                       │                                                                                    │
  Browser ── HTTPS ──► │  Cloud Run: frontend (Next.js)                                                     │
                       │     │  /api/backend/[...path] server-side proxy                                    │
                       │     ▼                                                                              │
                       │  Cloud Run: backend (FastAPI, /api/v1)  ◄── OIDC ── Cloud Scheduler (nightly jobs) │
                       │     │                                                                              │
                       │     ├──► Neon PostgreSQL (+ pgvector)        ├──► Cloud Run Jobs (batch backfills) │
                       │     ├──► GCS bucket (Vault file storage)     └──► GCP Secret Manager (API keys)    │
                       │     │                                                                              │
                       └─────┼──────────────────────────────────────────────────────────────────────────────┘
                             │ outbound HTTPS
        ┌────────────────────┼──────────────────────────────────────────────┐
        ▼                    ▼                                              ▼
  Government sources   Commercial data APIs                        Email providers (user-linked)
  SEC EDGAR / EFTS     Apollo.io, People Data Labs,                Gmail API, Microsoft Graph,
  SEC IAPD             Hunter.io, Snov.io, SerpAPI,                Yahoo SMTP (XOAUTH2)
  FINRA BrokerCheck    Google Gemini API
```

## 2.2 Components

| Component | Technology | Source location | Role |
|-----------|-----------|-----------------|------|
| **Frontend** | Next.js (React, TypeScript), Tailwind | `frontend/` | All user-facing pages; session handling via Better Auth; server-side proxy to the backend |
| **Backend API** | FastAPI (Python 3.11, async SQLAlchemy) | `backend/app/` | ~100+ REST endpoints under `/api/v1` across 25 endpoint modules; all business logic |
| **Database** | Neon (managed PostgreSQL) with `pgvector` | schema in `backend/app/models/` (40+ models), migrations in `backend/alembic/versions/` (78 migrations) | System of record for firms, filings, contacts, users, outreach, embeddings |
| **Object storage** | Google Cloud Storage | configured via `VAULT_STORAGE_BUCKET` | User-uploaded Vault documents (original files); downloads via 5-minute signed URLs |
| **Background jobs** | Cloud Run Jobs + Cloud Scheduler | `scripts/` | Nightly new-BD discovery, gap-fill enrichment, unified backfills, embedding backfills |
| **BrokerCheck extractor** | Standalone Python package | `brokercheck_extractor/` | Hybrid deterministic-parser + LLM pipeline that extracts firm profiles from FINRA BrokerCheck PDFs and financials from SEC X-17A-5 filings |
| **CI/CD** | GitHub Actions | `.github/workflows/test.yml` | Tests (unit, integration with real Postgres+pgvector, frontend lint/build) and deployment |

## 2.3 Backend layout

- **Entry point** `backend/app/main.py`: creates the FastAPI app ("DOX API"), CORS middleware from an environment allowlist, a startup listener for PostgreSQL `LISTEN filing_alerts_new` (powers the live alert stream), and a stale-pipeline reaper.
- **Endpoints** `backend/app/api/v1/endpoints/`: one module per feature area — `broker_dealers`, `investment_advisors`, `institutional_investors`, `investors` (Form 4), `alerts`, `contacts`, `email_extractor`, `outreach`, `chatbot`, `vault`, `favorites`/`favorite_lists`, `visits`, `settings`, `pipeline`, `users_admin`, `extraction_analytics`, `clearing_memberships`, `stats`, `auth`, `webhooks_apollo`.
- **Services** `backend/app/services/`: business logic, including the data-ingestion services (`edgar.py`, `iapd.py`, `finra.py`, `form4_watcher.py`, `filing_monitor.py`, `focus_reports.py`), classification (`clearing_classifier.py`), enrichment (`contact_discovery/`, `email_extractor/enrichment/`), the website resolver, the outreach pipeline (`outreach.py`, `outreach_send.py`, `email_providers/`), the Doxie assistant (`chatbot*.py`), Vault processing/RAG (`vault_*.py`), and auditing (`audit.py`).
- **Models** `backend/app/models/`: SQLAlchemy models; the complete inventory with personal-data flags is in [Document 05](05-personal-data-and-privacy.md).
- **Configuration** `backend/app/core/config.py`: a pydantic-settings class; every external integration is driven by named environment variables (no secrets in code; see [Document 08](08-security.md) §8.5 for one legacy caveat).

## 2.4 Frontend layout

- **Routes** under `frontend/app/`: public landing/auth pages; protected app pages (`/dashboard`, `/master-list` ["Broker Dealers"], `/advisor-list`, `/investors`, `/alerts`, `/email-extractor`, `/outreach/sent`, `/outreach/contacts`, `/vault`, `/my-favorites`, `/visited-firms`, `/settings/...`). Full route-by-route description in [Document 09](09-user-guide.md).
- **Auth** `frontend/lib/auth.ts`: Better Auth v1.3.x — email/password plus Google/Microsoft/Yahoo OAuth, signup approval gate, single-active-session enforcement, audit hooks. Details in [Document 08](08-security.md).
- **Backend access** `frontend/lib/api.ts`: all data calls go through the frontend's `/api/backend` server-side proxy. This exists for a security reason: the backend Cloud Run service is **not publicly invokable** (a GCP organization policy blocks `allUsers`), so the frontend service identity is the only path in from the internet.
- **Feature flags** `frontend/lib/feature-flags.ts`: `EMAIL_EXTRACTION_ENABLED = true`, `DATA_EXPORT_ENABLED = false`. `frontend/middleware.ts` enforces authentication on protected prefixes and redirects hidden features to the dashboard.
- **Client-side hardening** `frontend/components/security/`: watermark overlay, copy/print/right-click guards, security-event reporting (described in [Document 08](08-security.md) §8.4).

## 2.5 Data model at a glance

The center of the schema is the firm:

- `broker_dealers` — one row per broker-dealer (CRD-keyed), carrying identifiers, registration data, extracted financial snapshot, clearing classification and current partner, lead score/priority, website, business types, officers (JSONB), and data-provenance columns.
- `investment_advisors` and `institutional_investors` — the adviser-side equivalents (the Institutional Investors page was merged into Investment Advisers; its tables remain).
- `financial_metrics` — historical extracted financials per firm per report date, with extraction status and confidence.
- `clearing_arrangements`, `clearing_agency_memberships`, `introducing_arrangements`, `industry_arrangements`, `competitor_provider` — the clearing-relationship graph.
- `executive_contacts`, `advisor_contacts`, `investor_contacts` — people at firms, with per-field provenance (`discovery_source`, `discovery_confidence`, `apollo_person_id`).
- `form4_transactions` / `reporting_owner` — SEC Form 4 insider transactions and the consolidated per-person view.
- `filing_alert`, `pipeline_run`, `audit_log`, `user_activity`, `user_visit` — monitoring, job tracking, and audit.
- `extraction_run`, `discovered_email`, `email_verification`, `verification_run` — Email Extractor scans and results.
- `outreach_send`, `outreach_draft`, `user_outreach_settings` — the outreach audit trail, drafts, and per-user signature.
- `vault_folder`, `vault_folder_file`, `vault_folder_chunk` — user document folders, file metadata (bytes in GCS), and embedded text chunks (pgvector, 768-dim).
- `chatbot_conversation`, `chatbot_message`, `chatbot_firm_embedding`, `chatbot_learned_term` — Doxie history, firm-level semantic-search vectors, and the learned glossary.
- `user`, `session`, `account`, `verification` — Better Auth identity tables (roles, feature permissions, OAuth tokens).

## 2.6 Request and data flows

**Interactive request:** Browser → Next.js (session cookie) → `/api/backend` proxy → FastAPI (`get_current_user` verifies the HMAC-signed session token against the `session` table) → PostgreSQL → JSON back to the browser. Long-lived streams (alerts feed, Doxie chat) use Server-Sent Events through the same path (Cloud Run timeout raised to 3600s for this purpose).

**Ingestion flow (example: a firm files its annual report):** The daily filing monitor queries SEC's `submissions.json` for each tracked firm → inserts a `filing_alert` (Postgres NOTIFY pushes it to connected browsers) → for watched firms the financial-extraction pipeline downloads the X-17A-5 PDF from EDGAR → Gemini extracts structured figures with a confidence score → rows land in `financial_metrics` with status (`extracted` / `needs_review`), and the firm's snapshot columns and lead score update.

**Enrichment flow (example: user clicks "Enrich" on a firm):** Officer names from filings → contact-discovery chain (`apollo_match`, `hunter`, `snov`, configurable) queried in parallel with name + firm + domain → results above a confidence floor merge into the contact row with `discovery_source`/`discovery_confidence` recorded → if Apollo's asynchronous phone reveal is enabled, the phone number arrives later via a secret-path webhook and is matched by `apollo_person_id`.

## 2.7 Environments

| | Staging | Production |
|---|---|---|
| Git branch | `develop` | `main` |
| Backend | Cloud Run `fis-backend-staging` | Cloud Run `fis-backend` |
| Frontend | Cloud Run `fis-frontend-staging` | Cloud Run `fis-frontend` |
| Database | Neon (staging instance) | Neon (separate production instance) |
| Batch jobs | `fis-advisor-gap-fill-staging`, `fis-backfill-all-staging`, `fis-extract-new-bds-staging` (+ Cloud Scheduler `fis-extract-new-bds-nightly`) | — (staging-only at present) |

Deployments run database migrations **before** swapping code, and every deploy is preceded by the full test matrix. Operational details, including the deploy-concurrency behavior and backup procedure, are in [Document 10](10-operations-and-environments.md).

---

[← Product Overview](01-product-overview.md) | [Index](README.md) | [Next: Data Sources →](03-data-sources-and-provenance.md)
