# DOX — Institutional Finance Intelligence

Broker-dealer intelligence platform for the clearing-services workflow described in the PRD and sprint plan.

## What is included

- `frontend/`: Next.js 14 App Router app with Tailwind, BetterAuth wiring, protected app shell, login/signup pages, and the enterprise working surface
- `backend/`: FastAPI app with async SQLAlchemy, Alembic, `/api/v1` routing, health endpoint, and BetterAuth session validation for `/api/v1/auth/me`
- `docker-compose.yml`: local development stack with Postgres, Redis, frontend, and backend
- `scripts/seed_test_user.ts`: seed helper that creates a BetterAuth-backed test user and assigns a role

## Architecture alignments already applied

- BetterAuth session-based auth only
- FastAPI validates BetterAuth sessions via the shared database-backed session store
- Backend routes are namespaced under `/api/v1`
- No JWT injection in the frontend API client
- Custom domain / Cloud Run / Secret Manager assumptions preserved for later sprints

## Local development

1. Copy `.env.example` to `.env`.
2. Run `docker-compose up --build`.
3. Open [http://localhost:3000](http://localhost:3000).
4. Create a user through the sign-up form, or run the seed helper once dependencies are installed:

```bash
cd frontend
npm install
npm run seed:test-user
```

## Data loading

After the stack is up and migrations have run, load the broker-dealer dataset:

```bash
python -m scripts.initial_load
```

`DATA_SOURCE_MODE=live` is the default operating mode. The loader expects real upstream data sources and will fail clearly if those sources are unavailable.

## Gemini-backed PDF extraction

The clearing pipeline supports a real Gemini-backed extraction flow for X-17A-5 annual audit PDFs when you run in live mode.

1. Set the backend environment variables in `.env` and `backend/.env`:

```bash
DATA_SOURCE_MODE=live
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
GEMINI_PDF_MODEL=gemini-2.5-pro
PDF_CACHE_DIR=.tmp/pdf-cache
SEC_USER_AGENT=Your Company Name ops@yourcompany.com
```

2. Run the backend and trigger the clearing pipeline from the admin settings page or the initial load script.

What the live pipeline does:
- reads SEC submissions JSON for each broker-dealer
- finds the latest `X-17A-5` filing
- resolves the filing directory to a real PDF attachment
- caches the PDF locally in `PDF_CACHE_DIR`
- sends the PDF to the Gemini API with a strict JSON schema
- stores `clearing_partner`, `clearing_type`, `agreement_date`, confidence, and review notes in `clearing_arrangements`

Important:
- API keys belong in backend environment files only. Do not put `GEMINI_API_KEY` in frontend env files.
- `DATA_SOURCE_MODE=sample` remains available only as a developer fallback path and is not intended for delivery.
- Low-confidence, missing-partner, and provider-error cases are stored as review items instead of being silently treated as successful parses.
- The provider layer still supports `openai` as an alternate backend, but Gemini is now the preferred live configuration.
- `gemini-2.5-pro` is the default live model for extraction quality. You can override it in env if you want a lower-cost model.

## Google OAuth — Login with Google + Send via Gmail

"Continue with Google" on `/login` and `/signup`, plus the per-contact
"Send via Gmail" action in the Outreach modal on `/master-list/{id}`,
both share one OAuth 2.0 Web Client. Sending outreach uses the
logged-in user's own Gmail so messages come from the BD rep, not from
`noreply@alchemydev.io`.

### One-time GCP setup (per environment)

1. Create an OAuth 2.0 Web Client in the Google Cloud Console.
2. Enable the **Gmail API** on the same GCP project.
3. Add these authorized redirect URIs:
   - `http://localhost:3000/api/auth/callback/google` (dev)
   - `https://<staging-host>/api/auth/callback/google`
   - `https://<prod-host>/api/auth/callback/google`
4. On the OAuth consent screen add
   `https://www.googleapis.com/auth/gmail.send` to the scope list. Add
   yourself under **Test users** while the publishing status is
   `Testing`.

### Env vars

Set in both `.env` (backend reads them for token refresh) and
`frontend/.env.local` (Better Auth reads them at signup/login):

```bash
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
BETTER_AUTH_URL=http://localhost:3000   # explicit so redirect_uri is unambiguous
```

### Scope timing

Sign-in only asks for `openid email profile`. The `gmail.send` scope is
requested **incrementally** — the first time the user clicks Send in
the Outreach modal, the FE triggers Better Auth's `linkSocial` to merge
the new scope onto the existing `account` row. Users who never send
outreach never see the Gmail scope prompt.

### Restricted-scope verification (production deployment caveat)

`gmail.send` is a Google **restricted** scope. The implications:

- **Internal / Workspace-only** (OAuth client set to "Internal" so only
  alchemydev.io accounts can use it) — no verification required, ship
  freely.
- **External users beyond the 100-test-user cap or "Production"
  publishing status** — Google requires app verification **and** a
  CASA Tier 2 security assessment (third-party audit, ~$2k–$15k,
  4–12 week turnaround). Until that clears, the consent screen warns
  "unverified app" and new external users are blocked above the cap.

Plan accordingly: ship to staging as "Internal" or "Testing" first;
defer the CASA audit until a broad external rollout is in scope.

## Important notes

- The backend includes a BetterAuth-compatible schema bootstrap in Alembic so it can validate sessions against the same `user`, `session`, `account`, and `verification` tables BetterAuth expects.
- The backend only owns project-specific tables like `broker_dealers` and `audit_log`; auth tables exist to keep the shared session store workable from day one.
- The backend owns project-specific tables like `broker_dealers`, `clearing_arrangements`, `filing_alerts`, `financial_metrics`, and related audit and scoring tables.
- Live PDF extraction, alerts, export controls, settings, and scoring are implemented through the backend service layer and admin UI.
