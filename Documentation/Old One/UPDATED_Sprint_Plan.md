**SPRINT PLAN**

**Client Clearing Lead Gen Engine**

Incremental Development & Testing Plan

Next.js (Frontend) + FastAPI (Backend) + PostgreSQL + GCP

|                     |               |
|---------------------|---------------|
| **Version:**        | 1.0           |
| **Date:**           | April 6, 2026 |
| **Classification:** | Confidential  |
| **Prepared By:**    | Alchemy Dev   |
| **Reference:**      | PRD v1.0      |
| **Total Sprints:**  | 7             |
| **Est. Duration:**  | 7–8 weeks     |

1\. Sprint Plan Overview

This document defines the incremental sprint plan for building the
Client Clearing Lead Gen Engine. Each sprint builds directly on the
previous one, producing a testable, deployable increment. The plan is
designed so that:

- Each sprint delivers a fully functional, testable increment of the
  product.

- Sprint N+1 assumes Sprint N is complete and tested — strict sequential
  dependency.

- Testing happens at the end of every sprint before moving forward.

- No sprint introduces throwaway work — everything ships to production.

- The final sprint (Sprint 7) is exclusively for GCP deployment,
  hardening, and production readiness.

1.1 Tech Stack

|                    |                                                                                                       |
|--------------------|-------------------------------------------------------------------------------------------------------|
| **Layer**          | **Technology**                                                                                        |
| Frontend           | Next.js 14+ (App Router) with TypeScript, Tailwind CSS, shadcn/ui component library                   |
| Backend API        | FastAPI (Python 3.11+) with Pydantic v2 models, async endpoints                                       |
| Database           | PostgreSQL 15+ (local Docker for dev; Cloud SQL for prod)                                             |
| ORM / Migrations   | SQLAlchemy 2.0 + Alembic                                                                              |
| Auth               | BetterAuth (TypeScript-native, self-hosted, integrates with Next.js + PostgreSQL)                     |
| Task Queue         | Celery + Redis (for PDF pipeline and scheduled jobs)                                                  |
| PDF Processing     | OpenAI GPT-4o Vision API or Anthropic Claude API (direct PDF input — no Python parsing libraries)     |
| LLM Integration    | OpenAI API or Anthropic Claude API (via Python SDK)                                                   |
| Contact Enrichment | Apollo.io API (or ZoomInfo as fallback)                                                               |
| Hosting            | Google Cloud Platform: Cloud Run (API), Vercel or Cloud Run (Next.js), Cloud SQL, GCS, Secret Manager |
| CI/CD              | GitHub Actions → Cloud Build → Cloud Run deployment                                                   |
| Monitoring         | GCP Cloud Monitoring + Sentry for error tracking                                                      |

1.2 Sprint Map (Visual Overview)

|            |                                       |                                                    |              |                |
|------------|---------------------------------------|----------------------------------------------------|--------------|----------------|
| **Sprint** | **Name**                              | **Core Deliverable**                               | **Duration** | **PRD Ref**    |
| S1         | Foundation & Project Scaffolding      | Running app with auth + empty dashboard shell      | 5 days       | §4, §8, §10    |
| S2         | Master BD List + EDGAR Integration    | Searchable BD table with live SEC data             | 7 days       | §3, §4.5       |
| S3         | Financial Health + FOCUS Reports      | Health badges + net capital + YoY growth           | 5 days       | §3, §4.6, §6   |
| S4         | PDF Pipeline + Clearing Map           | Clearing partner extraction from X-17A-5 PDFs      | 7 days       | §3.3, §4.6, §9 |
| S5         | Alerts + Firm Detail + Deficiency     | Alert feed + 360° firm profile + 17a-11 tracking   | 5 days       | §4.7, §5       |
| S6         | Contacts + Lead Scoring + Export      | Enriched contacts + scoring model + controlled CSV | 5 days       | §6, §7, §10    |
| S7         | GCP Deployment + Production Hardening | Live production system on GCP with monitoring      | 5 days       | §8, §11, §12   |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><strong>Incremental Principle</strong></td>
</tr>
<tr class="even">
<td><p>At the end of each sprint, the application is runnable and
testable as-is.</p>
<p>Sprint 2 adds data to the shell from Sprint 1. Sprint 3 adds health
analysis to Sprint 2’s data. And so on.</p>
<p>Nothing is thrown away. Each sprint’s code is production
code.</p></td>
</tr>
</tbody>
</table>

2\. Sprint 1 — Foundation & Project Scaffolding

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>SPRINT 1</strong></p>
<p>Foundation &amp; Project Scaffolding</p>
<p><em>Duration: 5 days (Week 1)</em></p></td>
</tr>
</tbody>
</table>

Sprint Goal

Establish the entire project infrastructure: Next.js frontend, FastAPI
backend, PostgreSQL database, authentication, and a basic dashboard
shell with navigation. At the end of this sprint, a developer can log in
and see an empty but fully styled enterprise dashboard.

Task Breakdown

|          |                             |            |                                                                                                                                                                                                                                                                                                                                                                  |                                                                                                                                  |          |
|----------|-----------------------------|------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|----------|
| **ID**   | **Task**                    | **Type**   | **Details**                                                                                                                                                                                                                                                                                                                                                      | **Acceptance Criteria**                                                                                                          | **Est.** |
| **S1.1** | **Project Repo Setup**      | Infra      | Create monorepo with /frontend (Next.js 14, TypeScript, Tailwind, shadcn/ui) and /backend (FastAPI, SQLAlchemy, Alembic). Add .env.example, README, docker-compose.yml for local dev.                                                                                                                                                                            | Running \`docker-compose up\` starts frontend on :3000 and backend on :8000                                                      | 4h       |
| **S1.2** | **Database Schema v1**      | Backend    | Design and migrate initial PostgreSQL schema: broker_dealers (cik, crd, name, city, state, status, created_at), users (id, email, role, last_login), audit_log (user_id, action, timestamp, details).                                                                                                                                                            | Alembic migration runs cleanly; tables visible in psql                                                                           | 4h       |
| **S1.3** | **FastAPI Base Setup**      | Backend    | Configure FastAPI app with CORS, health check endpoint (/health), structured error handling, Pydantic settings, and SQLAlchemy async session. Add /api/v1/ prefix.                                                                                                                                                                                               | GET /health returns {status: ok}; Swagger docs at /docs                                                                          | 3h       |
| **S1.4** | **BetterAuth Setup**        | Full-Stack | Install BetterAuth (npm i better-auth). Configure auth server in Next.js API route (/api/auth/\[...all\]). Set up PostgreSQL session/user tables via BetterAuth’s built-in adapter. Implement email/password sign-up and sign-in. Add role field (admin/viewer) to user model. Create /api/v1/auth/me FastAPI endpoint that validates BetterAuth session tokens. | Sign-up creates user in DB; sign-in returns session; /auth/me returns user object with role; unauthenticated requests return 401 | 6h       |
| **S1.5** | **Next.js App Shell**       | Frontend   | Create Next.js app with App Router. Implement sidebar navigation (Dashboard, Master List, Alerts, Export, Settings). Build responsive layout with navy header bar, collapsible sidebar, main content area. Use design system colors from PRD §4.1.                                                                                                               | All nav items render; sidebar collapses on mobile; colors match PRD palette                                                      | 6h       |
| **S1.6** | **Login + Signup Pages**    | Frontend   | Build sign-in and sign-up pages using BetterAuth’s React client SDK (createAuthClient). Implement protected routes via middleware (redirect to /login if no session). Store session in httpOnly cookie (handled by BetterAuth). Show user avatar + role in header.                                                                                               | User can sign up, sign in, see dashboard; signing out redirects to login page                                                    | 5h       |
| **S1.7** | **Dashboard Home Skeleton** | Frontend   | Build Dashboard Home with 4 KPI card placeholders (Total BDs, New BDs, Deficiency Alerts, High-Value Leads) showing “—” values. Add empty activity feed panel (left) and empty chart placeholder (right).                                                                                                                                                        | Dashboard renders with styled KPI cards, empty feed, and chart placeholder                                                       | 4h       |
| **S1.8** | **API Client Setup**        | Frontend   | Create typed API client (axios or fetch wrapper) with JWT injection, error interceptors, and base URL config. Define TypeScript interfaces matching Pydantic models.                                                                                                                                                                                             | Frontend can call /health and /auth/me successfully with JWT                                                                     | 3h       |
| **S1.9** | **Docker Compose Finalize** | Infra      | Finalize docker-compose with postgres, redis, backend, frontend services. Add volume mounts for hot reload. Create seed script for a test user.                                                                                                                                                                                                                  | Full stack runs from single \`docker-compose up\` with hot reload on both FE and BE                                              | 3h       |

**⚠ Dependencies & Blockers**

- BetterAuth is self-hosted (no external account needed), but PostgreSQL
  must be running before S1.4.

- Design system colors and typography must be finalized (refer to PRD
  §4.1).

**✅ Sprint Deliverables (What the tester verifies)**

- Authenticated login/logout flow works end-to-end.

- Dashboard shell renders with proper navigation, KPI card placeholders,
  and enterprise styling.

- FastAPI Swagger docs are accessible at /api/v1/docs.

- Full stack runs locally via docker-compose.

Test Checklist

|          |                                           |                                                           |           |
|----------|-------------------------------------------|-----------------------------------------------------------|-----------|
| **\#**   | **Test Case**                             | **Expected Result**                                       | **Pass?** |
| **TC01** | Navigate to app URL without login         | Redirected to login page                                  | ☐         |
| **TC02** | Sign up with email/password, then sign in | Dashboard Home loads with KPI placeholders                | ☐         |
| **TC03** | Click each sidebar nav item               | Correct page/placeholder renders; active state highlights | ☐         |
| **TC04** | Call GET /api/v1/health                   | Returns 200 with {status: ok}                             | ☐         |
| **TC05** | Call GET /api/v1/auth/me with valid JWT   | Returns user object with email and role                   | ☐         |
| **TC06** | Call GET /api/v1/auth/me without JWT      | Returns 401 Unauthorized                                  | ☐         |
| **TC07** | Resize browser to mobile width            | Sidebar collapses; layout remains usable                  | ☐         |
| **TC08** | Log out and attempt to visit /dashboard   | Redirected to login page                                  | ☐         |

3\. Sprint 2 — Master BD List + EDGAR Integration

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>SPRINT 2</strong></p>
<p>Master BD List + EDGAR Integration</p>
<p><em>Duration: 7 days (Week 2)</em></p></td>
</tr>
</tbody>
</table>

Sprint Goal

Populate the database with all active U.S. Broker-Dealers from SEC EDGAR
and FINRA BrokerCheck. Build the Master List table in the frontend with
search, sort, filter, and pagination. The dashboard KPI card “Total
Active BDs” becomes live.

Task Breakdown

|          |                                      |          |                                                                                                                                                                                                                                                                                              |                                                                             |          |
|----------|--------------------------------------|----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|----------|
| **ID**   | **Task**                             | **Type** | **Details**                                                                                                                                                                                                                                                                                  | **Acceptance Criteria**                                                     | **Est.** |
| **S2.1** | **EDGAR Data Ingestion Service**     | Backend  | Create /services/edgar.py using edgartools library. Implement fetch_all_broker_dealers() that queries EDGAR Submissions API for all filers with SIC codes 6211 (security brokers/dealers). Extract CIK, name, SIC, state, filings index URL. Set User-Agent header with Alchemy Dev contact. | Script returns list of 3,000+ active BD entities from EDGAR                 | 6h       |
| **S2.2** | **FINRA BrokerCheck Scraper**        | Backend  | Create /services/finra.py that queries the BrokerCheck search API (public, no key). For each firm: extract CRD number, name, SEC number, registration status, branch count, address. Implement rate limiting (2 req/sec) and retry logic.                                                    | Script returns CRD-enriched data for 3,000+ firms; rate limits respected    | 8h       |
| **S2.3** | **Data Merge & Dedup Pipeline**      | Backend  | Create /services/data_merge.py that matches EDGAR entities to FINRA records using SEC file number and name fuzzy matching. Merge into single broker_dealer record. Flag unmatched entities for review.                                                                                       | Merged table has \>90% match rate; duplicates eliminated                    | 5h       |
| **S2.4** | **Initial Data Load Command**        | Backend  | Create FastAPI CLI command (or management script) \`python -m scripts.initial_load\` that runs S2.1 → S2.2 → S2.3 sequentially. Store results in broker_dealers table. Log progress and errors.                                                                                              | Full initial load completes in \<30 minutes; database populated             | 3h       |
| **S2.5** | **Master List API Endpoints**        | Backend  | Create /api/v1/broker-dealers with: GET / (paginated list, search, sort, filter), GET /{id} (single BD detail). Support query params: ?search=, ?state=, ?sort_by=, ?sort_dir=, ?page=, ?limit=. Return Pydantic response models.                                                            | API returns paginated, searchable, sortable broker-dealer list              | 5h       |
| **S2.6** | **Master List Frontend Table**       | Frontend | Build Master List page with data table using TanStack Table (or similar). Columns: Firm Name (link), CIK (monospace), CRD, State, Registration Status, Last Filing Date. Implement client-side search bar, column sorting (click headers), pagination controls (25/50/100 per page).         | Table renders 5,000+ rows with smooth pagination, search filters in \<500ms | 8h       |
| **S2.7** | **Filter Sidebar**                   | Frontend | Add collapsible filter panel to Master List: State dropdown (multi-select), Registration Status toggle. Filters send query params to API and update table.                                                                                                                                   | Selecting filters updates table results; clearing filters resets            | 4h       |
| **S2.8** | **Dashboard KPI — Total Active BDs** | Frontend | Wire the “Total Active BDs” KPI card on Dashboard Home to GET /api/v1/stats/total-bds. Show count with number formatting (e.g., 3,847). Add subtle loading skeleton.                                                                                                                         | KPI card shows live count from database                                     | 2h       |
| **S2.9** | **DB Schema Migration v2**           | Backend  | Extend broker_dealers table: add columns for crd_number, sec_file_number, branch_count, business_type, registration_date, matched_source (edgar/finra/both). Add indexes on name, cik, state.                                                                                                | Migration runs without data loss; queries use indexes                       | 2h       |

**⚠ Dependencies & Blockers**

- Sprint 1 must be complete (auth, app shell, database).

- SEC EDGAR requires User-Agent header set (no API key).

- FINRA BrokerCheck is publicly accessible but rate-limited; scraper
  must be polite.

**✅ Sprint Deliverables (What the tester verifies)**

- Master List page shows all active U.S. Broker-Dealers with search,
  sort, and filter.

- Dashboard KPI card “Total Active BDs” shows live count.

- Clicking a firm name in the table shows a placeholder detail page
  (full detail in Sprint 5).

- Data can be refreshed by re-running the initial load script.

Test Checklist

|          |                                                  |                                                                        |           |
|----------|--------------------------------------------------|------------------------------------------------------------------------|-----------|
| **\#**   | **Test Case**                                    | **Expected Result**                                                    | **Pass?** |
| **TC01** | Run initial data load script                     | Database populated with 3,000+ broker-dealer records                   | ☐         |
| **TC02** | Open Master List page                            | Table renders with columns: Name, CIK, CRD, State, Status, Last Filing | ☐         |
| **TC03** | Type a firm name in search bar                   | Table filters to matching results within 500ms                         | ☐         |
| **TC04** | Click “Name” column header                       | Rows sort A→Z; click again for Z→A                                     | ☐         |
| **TC05** | Select “New York” in State filter                | Only NY firms shown; count updates                                     | ☐         |
| **TC06** | Navigate to page 2 of results                    | Next page loads; previous page button activates                        | ☐         |
| **TC07** | Check Dashboard Home KPI card                    | Total Active BDs shows formatted number matching DB count              | ☐         |
| **TC08** | Call GET /api/v1/broker-dealers?search=Goldman   | Returns matching records in JSON                                       | ☐         |
| **TC09** | Call GET /api/v1/broker-dealers/999999 (invalid) | Returns 404 with error message                                         | ☐         |

4\. Sprint 3 — Financial Health + FOCUS Reports

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>SPRINT 3</strong></p>
<p>Financial Health + FOCUS Reports</p>
<p><em>Duration: 5 days (Week 3)</em></p></td>
</tr>
</tbody>
</table>

Sprint Goal

Ingest FOCUS Report data for net capital figures, calculate
Year-over-Year growth, assign financial health badges (Healthy / OK / At
Risk), and display them in the Master List. Build the lead scoring
engine foundation.

Task Breakdown

|          |                                  |          |                                                                                                                                                                                                                                                                     |                                                                 |          |
|----------|----------------------------------|----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------|----------|
| **ID**   | **Task**                         | **Type** | **Details**                                                                                                                                                                                                                                                         | **Acceptance Criteria**                                         | **Est.** |
| **S3.1** | **FOCUS Report Ingestion**       | Backend  | Create /services/focus_reports.py. Query EDGAR for X-17A-5 filings (FOCUS section). Extract net capital, excess net capital, total assets from XBRL data where available. For PDF-only FOCUS reports, extract via text parsing (LLM pipeline deferred to Sprint 4). | Net capital data extracted for 60%+ of active BDs               | 8h       |
| **S3.2** | **Financial Metrics Table**      | Backend  | Create financial_metrics DB table: bd_id, report_date, net_capital, excess_net_capital, total_assets, source_filing_url. Add Alembic migration. Store multiple years for YoY calculation.                                                                           | Table stores multi-year financial data per BD                   | 3h       |
| **S3.3** | **YoY Growth Calculator**        | Backend  | Create /services/scoring.py with calculate_yoy_growth(bd_id) that compares latest vs prior year net capital. Return percentage change and trend direction.                                                                                                          | YoY growth calculated correctly for firms with 2+ years of data | 3h       |
| **S3.4** | **Health Badge Logic**           | Backend  | Implement health classification in scoring.py: Healthy (\>120% of required min + positive YoY), OK (100–120% or flat growth), At Risk (\<100% or 17a-11 filed). Store health_status enum on broker_dealers table.                                                   | Every BD with financial data has a health_status assignment     | 3h       |
| **S3.5** | **Financial API Endpoints**      | Backend  | Add to /api/v1/broker-dealers: include health_status, net_capital, yoy_growth in list response. Add GET /api/v1/broker-dealers/{id}/financials for detailed history. Add ?health= filter param.                                                                     | API returns financial fields; filtering by health works         | 3h       |
| **S3.6** | **Health Badge UI Components**   | Frontend | Create reusable HealthBadge component: green pill for Healthy, amber for OK, red for At Risk. Add to Master List table as a new column. Add filter dropdown for health status.                                                                                      | Color-coded badges render correctly in table; filter works      | 4h       |
| **S3.7** | **Net Capital + Growth Columns** | Frontend | Add Net Capital column (formatted currency: \$1.2M) and YoY Growth column (green ↑+12.3% or red ↓-5.1%) to Master List. Both sortable.                                                                                                                              | Columns render formatted values with correct color-coded arrows | 3h       |
| **S3.8** | **Dashboard KPI Updates**        | Frontend | Wire remaining KPI cards: “New BDs (30 days)” from Form BD filing dates, “Deficiency Alerts” placeholder (live in Sprint 5). Add sparkline or trend indicator to KPI cards.                                                                                         | KPI cards show live data with trend indicators                  | 3h       |

**⚠ Dependencies & Blockers**

- Sprint 2 must be complete (BD data in database, Master List
  rendering).

- FOCUS Report XBRL data availability varies; PDF fallback handled in
  Sprint 4.

- Some BDs will have no financial data — show “N/A” gracefully.

**✅ Sprint Deliverables (What the tester verifies)**

- Master List shows Financial Health badges (green/amber/red) for each
  BD.

- Net Capital and YoY Growth columns are visible, sortable, and
  color-coded.

- Users can filter the Master List by financial health status.

- Dashboard KPI cards show live counts.

Test Checklist

|          |                                                |                                                                     |           |
|----------|------------------------------------------------|---------------------------------------------------------------------|-----------|
| **\#**   | **Test Case**                                  | **Expected Result**                                                 | **Pass?** |
| **TC01** | Open Master List after financial data load     | Health badge column shows green/amber/red pills for firms with data | ☐         |
| **TC02** | Sort by Net Capital descending                 | Highest net capital firms appear first                              | ☐         |
| **TC03** | Sort by YoY Growth ascending                   | Firms with largest declines appear first; red arrows visible        | ☐         |
| **TC04** | Filter by “Healthy” only                       | Only green-badge firms shown; count matches                         | ☐         |
| **TC05** | Check a firm with no financial data            | Columns show “N/A”; badge shows gray “Unknown”                      | ☐         |
| **TC06** | Call GET /api/v1/broker-dealers?health=at_risk | Returns only At Risk firms in JSON                                  | ☐         |
| **TC07** | Verify YoY calculation manually for one firm   | Percentage matches (currentYear - priorYear) / priorYear \* 100     | ☐         |

5\. Sprint 4 — PDF Pipeline + Clearing Map

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>SPRINT 4</strong></p>
<p>PDF Pipeline + Clearing Map</p>
<p><em>Duration: 7 days (Week 3–4)</em></p></td>
</tr>
</tbody>
</table>

Sprint Goal

Build the PDF parsing pipeline that downloads X-17A-5 annual audit PDFs,
extracts clearing partner names using an LLM, and maps every BD’s
clearing relationship. Display clearing partner and clearing type in the
Master List with competitor badges.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><strong>This is the hardest sprint</strong></td>
</tr>
<tr class="even">
<td><p>PDF extraction is the core technical challenge of the entire
product.</p>
<p>Budget extra time for edge cases: scanned PDFs, inconsistent
formatting, multi-page notes.</p>
<p>Aim for 80%+ extraction success rate; flag failures for manual
review.</p></td>
</tr>
</tbody>
</table>

Task Breakdown

|          |                                     |          |                                                                                                                                                                                                                                                                                                                                                                                                                                                  |                                                                                       |          |
|----------|-------------------------------------|----------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|----------|
| **ID**   | **Task**                            | **Type** | **Details**                                                                                                                                                                                                                                                                                                                                                                                                                                      | **Acceptance Criteria**                                                               | **Est.** |
| **S4.1** | **PDF Download Service**            | Backend  | Create /services/pdf_downloader.py. For each BD, query EDGAR for latest X-17A-5 filing, find the PDF attachment URL, download to local storage (or GCS bucket). Implement caching: skip if PDF already downloaded. Rate limit: 10 req/sec.                                                                                                                                                                                                       | PDFs downloaded for all BDs with X-17A-5 filings; cached on disk/GCS                  | 5h       |
| **S4.2** | **Direct PDF-to-LLM Service**       | Backend  | Create /services/pdf_processor.py. Read downloaded PDF as base64. Send directly to OpenAI GPT-4o vision API (or Anthropic Claude API with PDF support). Do NOT use Python PDF parsing libraries (PyMuPDF, pdfplumber, pytesseract) — they produce unreliable results on regulatory filings. The LLM natively handles text-based PDFs, scanned PDFs, and mixed-format documents.                                                                  | LLM receives raw PDF and returns structured text; works on both text and scanned PDFs | 5h       |
| **S4.3** | **Structured Extraction Prompt**    | Backend  | Create /services/llm_parser.py. Build a structured prompt that instructs the LLM to read the PDF and extract: clearing_partner (str), clearing_type (fully_disclosed/self_clearing/omnibus/unknown), agreement_date (str, optional). Request JSON output format. Include few-shot examples in the prompt for accuracy. Parse JSON response and store confidence_score. S4.2 sends the PDF; this module defines the prompt and parses the output. | LLM returns structured clearing data in JSON; 80%+ extraction rate across test set    | 8h       |
| **S4.4** | **Clearing Data DB Schema**         | Backend  | Create clearing_arrangements table: bd_id, clearing_partner, clearing_type, agreement_date, source_filing_url, extraction_confidence, extracted_at, is_verified. Add Alembic migration.                                                                                                                                                                                                                                                          | Table created; stores one record per BD per filing year                               | 2h       |
| **S4.5** | **Pipeline Orchestrator**           | Backend  | Create /services/pipeline.py that runs: download → extract → LLM parse → store for each BD. Use Celery for async processing. Track progress in a pipeline_runs table. Log errors per filing.                                                                                                                                                                                                                                                     | Pipeline processes 50 filings/hour; progress tracked; errors logged                   | 6h       |
| **S4.6** | **Competitor Mapping Config**       | Backend  | Create competitor_providers table: name, aliases (array), priority. Seed with Pershing, Apex, Hilltop, RBC, Axos, Vision. When clearing_partner matches a competitor, set is_competitor=true on clearing_arrangements.                                                                                                                                                                                                                           | Known competitors are auto-flagged; list is editable without code changes             | 3h       |
| **S4.7** | **Clearing Columns in Master List** | Frontend | Add Clearing Partner column (text + red “COMPETITOR” badge if applicable) and Clearing Type column (navy/blue/gray pill badges per PRD §4.6) to Master List table. Add clearing type toggle filter and clearing partner dropdown filter.                                                                                                                                                                                                         | Clearing data renders with correct badges; filters work                               | 5h       |
| **S4.8** | **Dashboard Clearing Chart**        | Frontend | Build donut chart on Dashboard Home (right panel) showing clearing provider market share. Use Recharts or Chart.js. Clicking a segment filters Master List to that provider. Show percentages and counts.                                                                                                                                                                                                                                        | Chart renders with real data; click-through filtering works                           | 5h       |
| **S4.9** | **Pipeline Admin View**             | Frontend | Create a simple admin-only page showing pipeline status: total processed, success/fail counts, recent errors, re-run button for failed extractions.                                                                                                                                                                                                                                                                                              | Admin can see pipeline progress and retry failed extractions                          | 4h       |

**⚠ Dependencies & Blockers**

- OpenAI or Claude API key must be provisioned (Alchemy Dev email)
  before S4.3.

- Redis must be running for Celery task queue (added to docker-compose
  in Sprint 1).

- Some PDFs will be unparseable; these are flagged, not failures.

**✅ Sprint Deliverables (What the tester verifies)**

- Master List shows Clearing Partner and Clearing Type for 80%+ of BDs
  with X-17A-5 filings.

- Competitor clearing providers are flagged with red “COMPETITOR”
  badges.

- Dashboard donut chart shows clearing provider market share.

- Users can filter Master List by clearing type (Self-Clearing / Fully
  Disclosed).

- Admin can view pipeline status and retry failed extractions.

Test Checklist

|          |                                               |                                                          |           |
|----------|-----------------------------------------------|----------------------------------------------------------|-----------|
| **\#**   | **Test Case**                                 | **Expected Result**                                      | **Pass?** |
| **TC01** | Run the PDF pipeline for 20 BDs               | At least 16 (80%) produce a clearing_partner result      | ☐         |
| **TC02** | Check a firm known to clear through Pershing  | Clearing Partner shows “Pershing” with COMPETITOR badge  | ☐         |
| **TC03** | Check a self-clearing firm                    | Clearing Type badge shows “Self-Clearing” in navy pill   | ☐         |
| **TC04** | Filter Master List by “Fully Disclosed”       | Only fully-disclosed BDs appear                          | ☐         |
| **TC05** | Filter Master List by clearing partner “Apex” | Only Apex-clearing BDs appear                            | ☐         |
| **TC06** | View Dashboard donut chart                    | Chart shows correct proportions; segments are clickable  | ☐         |
| **TC07** | Check a BD with no X-17A-5 filing             | Clearing columns show “Unknown” with dashed badge        | ☐         |
| **TC08** | Open Pipeline Admin page as admin             | Shows counts and error log; re-run button visible        | ☐         |
| **TC09** | Open Pipeline Admin page as viewer            | Access denied or page hidden                             | ☐         |
| **TC10** | Add a new competitor via DB/admin             | New competitor auto-detected in subsequent pipeline runs | ☐         |

6\. Sprint 5 — Alerts + Firm Detail + Deficiency Tracking

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>SPRINT 5</strong></p>
<p>Alerts + Firm Detail + Deficiency Tracking</p>
<p><em>Duration: 5 days (Week 4–5)</em></p></td>
</tr>
</tbody>
</table>

Sprint Goal

Build the daily filing monitor for new Form BD and 17a-11 filings, the
dashboard alert feed, the full Firm Detail page (360° view), and the
deficiency notice tracking with the Alternative List.

Task Breakdown

|          |                                  |          |                                                                                                                                                                                                                                                                                                                                           |                                                                                     |          |
|----------|----------------------------------|----------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|----------|
| **ID**   | **Task**                         | **Type** | **Details**                                                                                                                                                                                                                                                                                                                               | **Acceptance Criteria**                                                             | **Est.** |
| **S5.1** | **Daily Filing Monitor**         | Backend  | Create /services/filing_monitor.py. Queries EDGAR for new Form BD and Form 17a-11 filings since last check. Stores new filings in filing_alerts table: bd_id, form_type, filed_at, summary, priority, is_read. Designed to run as a scheduled Celery beat task (every day 6 AM EST).                                                      | Running the monitor finds and stores new filings from previous day                  | 6h       |
| **S5.2** | **Deficiency (17a-11) Handler**  | Backend  | When a 17a-11 is detected: set BD health_status to at_risk, add is_deficient=true flag, move BD to alternative list (a filtered view, not a separate table). Create GET /api/v1/broker-dealers?list=alternative endpoint.                                                                                                                 | Deficient BDs appear in alternative list; health badge turns red                    | 4h       |
| **S5.3** | **Alert Feed API**               | Backend  | Create GET /api/v1/alerts (paginated, filterable by type, priority, date range, read/unread). Create PATCH /api/v1/alerts/{id}/read to mark as read. Create GET /api/v1/stats (all KPI counts).                                                                                                                                           | Alerts endpoint returns ordered feed; mark-as-read updates state                    | 4h       |
| **S5.4** | **Dashboard Alert Feed UI**      | Frontend | Build Activity Feed on Dashboard Home (left panel). Each entry: filing type color-coded badge, firm name (clickable), date (relative: “2 hours ago”), one-line summary. Show unread dot indicator. Infinite scroll or “Load more” button.                                                                                                 | Feed renders real alerts; clicking navigates to firm detail; unread indicator works | 5h       |
| **S5.5** | **Alerts Page (Full View)**      | Frontend | Build dedicated Alerts page with full filterable alert table: columns for Type, Firm, Date, Priority, Status (read/unread). Add filter bar for type and priority. “Mark all as read” button.                                                                                                                                              | Full alert table renders with filters; bulk mark-as-read works                      | 4h       |
| **S5.6** | **Firm Detail Page**             | Frontend | Build the full 360° Firm Detail page per PRD §4.7. Header bar with firm name, badges. Six cards: Financial Overview (net capital chart using Recharts), Clearing Arrangements, Executive Contacts (placeholder — enriched in Sprint 6), Filing History (sortable list with links to EDGAR), Registration & Compliance, Deficiency Status. | All six cards render with real data; financial chart displays YoY trend             | 10h      |
| **S5.7** | **Alternative List Tab**         | Frontend | Add a tab or toggle on Master List page: “Primary List” / “Alternative List”. Alternative List shows only deficient / at-risk firms. Default view is Primary List (excludes deficient firms per client directive).                                                                                                                        | Tab toggle works; deficient firms appear only in Alternative List                   | 3h       |
| **S5.8** | **KPI Card — Deficiency Alerts** | Frontend | Wire the “Deficiency Alerts” KPI card on Dashboard to show count of active 17a-11 filings. Red background, warning icon. Clicking navigates to Alerts page filtered by 17a-11.                                                                                                                                                            | KPI shows live count; click navigates correctly                                     | 2h       |

**⚠ Dependencies & Blockers**

- Sprints 2–4 must be complete (BD data, financial data, clearing data
  all in database).

- Celery beat scheduler must be configured for daily 6 AM EST execution.

- Filing monitor requires EDGAR to have recent filings; test with known
  recent filing dates.

**✅ Sprint Deliverables (What the tester verifies)**

- Daily filing monitor detects new Form BD and 17a-11 filings and
  creates alerts.

- Dashboard Activity Feed shows real-time alert entries.

- Firm Detail page provides a complete 360° view with financial charts.

- Deficient firms are excluded from the Primary list and shown in the
  Alternative List.

- All four Dashboard KPI cards show live data.

Test Checklist

|          |                                           |                                                                      |           |
|----------|-------------------------------------------|----------------------------------------------------------------------|-----------|
| **\#**   | **Test Case**                             | **Expected Result**                                                  | **Pass?** |
| **TC01** | Trigger filing monitor manually           | New filings appear in filing_alerts table and dashboard feed         | ☐         |
| **TC02** | Simulate a 17a-11 filing for a test BD    | BD moves to Alternative List; health badge turns red; KPI increments | ☐         |
| **TC03** | Click a firm name in the alert feed       | Navigates to correct Firm Detail page                                | ☐         |
| **TC04** | View Firm Detail — Financial card         | Net capital trend chart renders with correct historical data         | ☐         |
| **TC05** | View Firm Detail — Clearing card          | Clearing partner and type displayed correctly                        | ☐         |
| **TC06** | View Firm Detail — Filing History         | All filings listed chronologically; EDGAR links work                 | ☐         |
| **TC07** | Toggle to Alternative List on Master List | Only deficient/at-risk firms shown                                   | ☐         |
| **TC08** | Toggle back to Primary List               | Deficient firms are excluded                                         | ☐         |
| **TC09** | Mark an alert as read                     | Unread indicator disappears; status updates in Alerts page           | ☐         |
| **TC10** | Check KPI card “Deficiency Alerts”        | Count matches database; click navigates to filtered alerts           | ☐         |

7\. Sprint 6 — Contact Enrichment + Lead Scoring + Export

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>SPRINT 6</strong></p>
<p>Contact Enrichment + Lead Scoring + Export</p>
<p><em>Duration: 5 days (Week 5–6)</em></p></td>
</tr>
</tbody>
</table>

Sprint Goal

Integrate Apollo/ZoomInfo for executive contact enrichment, implement
the weighted lead scoring model, add the lead priority indicators
(Hot/Warm/Cold) to the Master List, build the controlled data export
feature, and complete the High-Value Leads KPI card.

Task Breakdown

|          |                                      |          |                                                                                                                                                                                                                                                                                                      |                                                                              |          |
|----------|--------------------------------------|----------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|----------|
| **ID**   | **Task**                             | **Type** | **Details**                                                                                                                                                                                                                                                                                          | **Acceptance Criteria**                                                      | **Est.** |
| **S6.1** | **Contact Enrichment Service**       | Backend  | Create /services/contacts.py. Use Apollo.io People API (or ZoomInfo) to search by company name + executive name (from Form BD principals). Extract: email, phone, LinkedIn URL, title. Store in executive_contacts table: bd_id, name, title, email, phone, linkedin_url, source, enriched_at.       | Contact data populated for 60%+ of listed executives                         | 6h       |
| **S6.2** | **On-Demand Enrichment**             | Backend  | Create POST /api/v1/broker-dealers/{id}/enrich endpoint. When a user views a firm detail page, trigger contact enrichment if data is stale (\>90 days old). Return enriched contacts in response.                                                                                                    | Viewing a firm triggers enrichment if stale; fresh data returned             | 3h       |
| **S6.3** | **Lead Scoring Engine**              | Backend  | Implement calculate_lead_score(bd_id) in scoring.py per PRD §6: Net Capital Growth (35%), Clearing Arrangement (30%), Financial Health (20%), Recency of Registration (15%). Store lead_score (0–100) and lead_priority (hot/warm/cold) on broker_dealers table. Create batch recalculation command. | Every BD with sufficient data has a lead score; Hot/Warm/Cold assigned       | 5h       |
| **S6.4** | **Lead Priority UI**                 | Frontend | Add Lead Priority column to Master List: gold stars for Hot (★★★), blue for Warm (★★), gray for Cold (★). Add gold left-border to Hot Lead rows. Add filter for priority level. Sortable by score.                                                                                                   | Star ratings render correctly; Hot leads have gold border; filter/sort works | 4h       |
| **S6.5** | **Executive Contacts Card**          | Frontend | Populate the Executive Contacts card on Firm Detail page with enriched data. Show: name, title, email (clickable mailto:), phone, LinkedIn link. Contact info visible only to logged-in users (no export).                                                                                           | Contact card shows enriched data; email/phone clickable; no data in exports  | 4h       |
| **S6.6** | **Data Export Backend**              | Backend  | Create POST /api/v1/export endpoint. Accepts filter params (same as Master List). Returns CSV with ONLY permitted fields per PRD §7.1 (no emails/phones). Limit: 100 records max per export. Log export in audit_log. Rate limit: 3 exports per user per day. Add watermark footer row.              | CSV contains only allowed fields; limits enforced; audit logged              | 5h       |
| **S6.7** | **Export UI**                        | Frontend | Build Export page with: current filter summary, record count preview, “Export CSV” button, remaining exports today (X of 3). Show confirmation dialog before export. Display download link when ready.                                                                                               | Export flow works end-to-end; limits shown; CSV downloads correctly          | 4h       |
| **S6.8** | **Dashboard KPI — High-Value Leads** | Frontend | Wire the “High-Value Leads” KPI card to show count of Hot leads. Gold background, target icon. Clicking navigates to Master List filtered by Hot priority.                                                                                                                                           | KPI shows live Hot lead count; click-through works                           | 2h       |
| **S6.9** | **Settings Page**                    | Frontend | Build Settings page: editable competitor list (add/remove), scoring weight sliders (with save), and a manual “Refresh Data” button (triggers data pipeline). Admin-only access.                                                                                                                      | Competitor list editable; weights adjustable; refresh triggers pipeline      | 4h       |

**⚠ Dependencies & Blockers**

- Apollo.io or ZoomInfo API key must be provisioned (Alchemy Dev email,
  client pays).

- All prior sprints must be complete (BD data, financials, clearing
  data, alerts).

- Lead scoring requires clearing_type, health_status, and financial data
  to be populated.

**✅ Sprint Deliverables (What the tester verifies)**

- Executive contacts enriched with email, phone, LinkedIn for 60%+ of
  principals.

- Every BD has a lead score (0–100) and priority rating (Hot/Warm/Cold).

- Master List shows star-based lead priority with gold borders on Hot
  leads.

- CSV export works with restricted fields and enforced limits.

- Settings page allows competitor list and scoring weight configuration.

- All four Dashboard KPI cards are fully live.

Test Checklist

|          |                                                                                         |                                                                  |           |
|----------|-----------------------------------------------------------------------------------------|------------------------------------------------------------------|-----------|
| **\#**   | **Test Case**                                                                           | **Expected Result**                                              | **Pass?** |
| **TC01** | View Firm Detail for a BD with executives listed                                        | Contact card shows enriched email/phone/LinkedIn                 | ☐         |
| **TC02** | View Firm Detail for a BD never enriched                                                | Enrichment triggers on-demand; data appears after loading        | ☐         |
| **TC03** | Check lead score for a BD with competitor clearing + healthy finances + growing capital | Score is 75–100 (Hot); gold stars and border shown               | ☐         |
| **TC04** | Check lead score for a BD with unknown clearing + no financial data                     | Score is low (Cold); single gray star                            | ☐         |
| **TC05** | Sort Master List by lead score descending                                               | Hot leads appear first                                           | ☐         |
| **TC06** | Filter Master List by “Hot” priority                                                    | Only Hot leads shown                                             | ☐         |
| **TC07** | Export CSV with default filters                                                         | CSV downloads with exactly 9 permitted columns; no emails/phones | ☐         |
| **TC08** | Attempt 4th export in one day                                                           | Error: “Export limit reached (3/day)”                            | ☐         |
| **TC09** | Check audit_log after export                                                            | Export event logged with user ID, timestamp, record count        | ☐         |
| **TC10** | Edit competitor list in Settings and save                                               | New competitor appears in Master List badges on next refresh     | ☐         |
| **TC11** | Export CSV and open file                                                                | Footer watermark row identifies the source platform              | ☐         |

8\. Sprint 7 — GCP Deployment + Production Hardening

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>SPRINT 7</strong></p>
<p>GCP Deployment + Production Hardening</p>
<p><em>Duration: 5 days (Week 6–7)</em></p></td>
</tr>
</tbody>
</table>

Sprint Goal

Deploy the complete application to Google Cloud Platform. Configure
production database, secrets, HTTPS, domain, monitoring, and CI/CD
pipeline. Conduct a full end-to-end regression test. This sprint
produces the live production system.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><strong>No New Features</strong></td>
</tr>
<tr class="even">
<td><p>This sprint is exclusively about deployment, hardening, and
production readiness.</p>
<p>No new features are added. Only infrastructure, security,
performance, and monitoring work.</p>
<p>All bug fixes from regression testing are handled here.</p></td>
</tr>
</tbody>
</table>

Task Breakdown

|           |                                  |          |                                                                                                                                                                                                                    |                                                                            |          |
|-----------|----------------------------------|----------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|----------|
| **ID**    | **Task**                         | **Type** | **Details**                                                                                                                                                                                                        | **Acceptance Criteria**                                                    | **Est.** |
| **S7.1**  | **GCP Project Setup**            | Infra    | Create GCP project (or use existing). Enable APIs: Cloud Run, Cloud SQL, Cloud Storage, Secret Manager, Cloud Scheduler, Cloud Build. Set up billing alerts. Configure IAM roles.                                  | GCP project created with all required APIs enabled and billing alerts set  | 3h       |
| **S7.2**  | **Cloud SQL (PostgreSQL)**       | Infra    | Provision Cloud SQL PostgreSQL 15 instance. Configure private IP, SSL connections, automated backups (daily), and maintenance window (Sunday 3 AM EST). Run Alembic migrations against production DB.              | Production database running; migrations applied; backup schedule confirmed | 4h       |
| **S7.3**  | **Secret Manager**               | Infra    | Store all secrets in GCP Secret Manager: BetterAuth secret key, OpenAI API key, Apollo API key, database connection string, Redis URL. Update FastAPI and Next.js to read from Secret Manager in production.       | No secrets in source code or env files; all read from Secret Manager       | 3h       |
| **S7.4**  | **FastAPI Cloud Run Deployment** | Infra    | Containerize FastAPI with Dockerfile. Deploy to Cloud Run with: min 1 / max 5 instances, 2GB memory, 2 vCPU, Cloud SQL connection, Secret Manager access. Configure custom domain and HTTPS.                       | API accessible at custom domain over HTTPS; Swagger docs accessible        | 5h       |
| **S7.5**  | **Next.js Deployment**           | Infra    | Deploy Next.js to Vercel (simplest) or Cloud Run. Configure environment variables for production API URL. Set up custom domain and HTTPS. Configure caching and CDN.                                               | Frontend accessible at custom domain over HTTPS; loads in \<2 seconds      | 4h       |
| **S7.6**  | **Cloud Storage (GCS)**          | Infra    | Create GCS bucket for PDF cache. Configure lifecycle rules (delete after 90 days). Update PDF pipeline to use GCS instead of local storage.                                                                        | PDFs stored in GCS; lifecycle rules active; pipeline reads from GCS        | 2h       |
| **S7.7**  | **Cloud Scheduler Jobs**         | Infra    | Configure Cloud Scheduler: daily 6 AM EST filing monitor, bi-monthly full data refresh, monthly X-17A-5 pipeline trigger. Each job calls a FastAPI endpoint with a service account.                                | All three scheduled jobs configured and verified with test runs            | 3h       |
| **S7.8**  | **CI/CD Pipeline**               | Infra    | Set up GitHub Actions (or Cloud Build): on push to main → run tests → build Docker image → deploy to Cloud Run. Add staging environment for pre-production testing.                                                | Push to main auto-deploys; staging environment accessible                  | 4h       |
| **S7.9**  | **Monitoring & Alerting**        | Infra    | Configure GCP Cloud Monitoring dashboards: API latency, error rates, Cloud SQL metrics, Cloud Run instance count. Set up Sentry for error tracking. Configure PagerDuty/email alerts for downtime or error spikes. | Monitoring dashboard shows real-time metrics; test alert fires correctly   | 3h       |
| **S7.10** | **Security Hardening**           | Infra    | Configure CORS for production domain only. Add rate limiting (100 req/min per user). Ensure session timeout (30 min inactivity). Verify RBAC on all endpoints. Run OWASP ZAP scan or similar.                      | No CORS issues; rate limiting works; unauthorized endpoints return 403     | 4h       |
| **S7.11** | **Full Regression Test**         | QA       | Execute ALL test checklists from Sprints 1–6 against the production environment. Document any failures. Fix all critical and high-severity bugs before sign-off.                                                   | All test cases from all sprints pass in production environment             | 6h       |
| **S7.12** | **Production Data Load**         | Data     | Run full data pipeline against production: initial BD load, FOCUS reports, PDF clearing pipeline, contact enrichment. Verify data quality and completeness. Compare prod counts to dev.                            | Production DB has same data quality as dev; KPI cards show real numbers    | 4h       |

**⚠ Dependencies & Blockers**

- All Sprints 1–6 must be complete and tested in local/dev environment.

- GCP billing account must be active with appropriate spending limits.

- Custom domain must be purchased/configured for DNS setup.

- BetterAuth is self-hosted; ensure production BETTER_AUTH_SECRET is set
  in Secret Manager.

**✅ Sprint Deliverables (What the tester verifies)**

- Application is live and accessible at a custom HTTPS domain.

- All scheduled jobs (daily filing monitor, bi-monthly refresh, monthly
  PDF pipeline) are running.

- Monitoring dashboards show real-time system health.

- CI/CD pipeline auto-deploys from main branch.

- Full regression test passes in production environment.

- Production database is populated with real data.

Test Checklist (Production)

|          |                                                                |                                                                  |           |
|----------|----------------------------------------------------------------|------------------------------------------------------------------|-----------|
| **\#**   | **Test Case**                                                  | **Expected Result**                                              | **Pass?** |
| **TC01** | Access the production URL                                      | Login page loads over HTTPS with valid certificate               | ☐         |
| **TC02** | Log in with production BetterAuth credentials (email/password) | Dashboard loads with real data in all KPI cards                  | ☐         |
| **TC03** | Browse Master List with 3,000+ BDs                             | Table loads in \<2 seconds; search/filter/sort work              | ☐         |
| **TC04** | Click a firm → Firm Detail page                                | All six cards render with real data; page loads in \<1.5 seconds | ☐         |
| **TC05** | Check Alerts feed                                              | Recent filings appear; click-through works                       | ☐         |
| **TC06** | Export CSV                                                     | File downloads with correct restricted fields and watermark      | ☐         |
| **TC07** | Trigger a manual data refresh from Settings                    | Pipeline runs; data updates in database                          | ☐         |
| **TC08** | Check Cloud Scheduler logs                                     | Scheduled jobs show successful recent executions                 | ☐         |
| **TC09** | Check Sentry for errors                                        | No unhandled exceptions in last 24 hours                         | ☐         |
| **TC10** | Attempt access without login                                   | Redirected to login page (no unauthenticated access)             | ☐         |
| **TC11** | Check Cloud Run metrics                                        | Instances auto-scale; no out-of-memory errors                    | ☐         |
| **TC12** | Verify backup exists in Cloud SQL                              | Automated backup from today/yesterday is present                 | ☐         |

9\. Pre-Launch Master Checklist

Before declaring the product “production ready,” every item below must
be checked off:

|        |                                                                         |              |           |
|--------|-------------------------------------------------------------------------|--------------|-----------|
| **\#** | **Checkpoint**                                                          | **Category** | **Done?** |
| **1**  | All 3,000+ active BDs loaded in production database                     | Data         | ☐         |
| **2**  | Financial health badges (green/amber/red) display for 60%+ of BDs       | Data         | ☐         |
| **3**  | Clearing partner mapped for 80%+ of BDs with X-17A-5 filings            | Data         | ☐         |
| **4**  | Contact enrichment populated for 60%+ of executives                     | Data         | ☐         |
| **5**  | Lead scores calculated for all BDs with sufficient data                 | Scoring      | ☐         |
| **6**  | Daily filing monitor runs at 6 AM EST via Cloud Scheduler               | Pipeline     | ☐         |
| **7**  | PDF pipeline processes new X-17A-5 filings monthly                      | Pipeline     | ☐         |
| **8**  | All four Dashboard KPI cards show live data                             | UI           | ☐         |
| **9**  | Dashboard activity feed shows real alerts                               | UI           | ☐         |
| **10** | Dashboard clearing donut chart renders real data                        | UI           | ☐         |
| **11** | Master List search returns results in \<500ms                           | Performance  | ☐         |
| **12** | Firm Detail page loads in \<1.5 seconds                                 | Performance  | ☐         |
| **13** | CSV export enforces field restrictions, record limits, and daily limits | Security     | ☐         |
| **14** | No API secrets in source code or environment files                      | Security     | ☐         |
| **15** | HTTPS with valid certificate on custom domain                           | Security     | ☐         |
| **16** | BetterAuth login works with correct RBAC (admin/viewer)                 | Security     | ☐         |
| **17** | CI/CD pipeline auto-deploys from main branch                            | DevOps       | ☐         |
| **18** | Cloud Monitoring dashboards and Sentry configured                       | DevOps       | ☐         |
| **19** | Automated database backups confirmed                                    | DevOps       | ☐         |
| **20** | CORS restricted to production domain only                               | Security     | ☐         |

**END OF DOCUMENT**


---

# RESOLVED ARCHITECTURAL ALIGNMENTS (DO NOT REMOVE)

## Authentication Standardization
- System uses BetterAuth session-based authentication ONLY.
- FastAPI validates session via shared session store or signed cookies.
- JWT injection removed from frontend API client.

## Database Ownership
- BetterAuth manages `users` and `sessions`.
- Custom schema references BetterAuth user IDs only (no duplicate users table).

## API Routing Consistency
- All backend routes are prefixed with `/api/v1/`.
- Swagger docs available at `/api/v1/docs`.

## Financial Health Calculation Fix
- Introduced `required_min_capital` field (derived from FOCUS/XBRL or rule-based estimation).
- Health logic now computable.

## PDF Processing Unification
- ALL PDF processing (FOCUS + X-17A-5) uses LLM pipeline.
- No Python PDF parsing anywhere.

## Scheduler Architecture
- Single approach: Celery + Redis (local/dev + prod).
- GCP Cloud Scheduler triggers FastAPI endpoint that enqueues Celery jobs.

## Deployment Standardization
- Backend: Cloud Run (GCP)
- Frontend: Cloud Run (NOT Vercel) for unified secret management

## Secret Management
- All services read secrets from GCP Secret Manager via runtime injection.

## Data Volume Consistency
- System designed for 3,000–5,000 BDs (scales dynamically).

## Master List Behavior
- Primary List excludes deficient firms.
- Alternative List contains deficient firms (consistent across system).

---

