**PRODUCT REQUIREMENTS DOCUMENT**

**Client Clearing Lead Gen Engine**

Broker-Dealer Intelligence Platform

|                     |               |
|---------------------|---------------|
| **Version:**        | 1.0           |
| **Date:**           | April 6, 2026 |
| **Classification:** | Confidential  |
| **Prepared By:**    | Alchemy Dev   |

1\. Executive Summary

The Client Clearing Lead Gen Engine is an enterprise-grade intelligence
platform that aggregates publicly available SEC and FINRA data to
identify, profile, and score every active Broker-Dealer in the United
States. The platform maps clearing relationships, monitors financial
health indicators, and surfaces high-priority sales leads for firms
offering settlement and clearing services.

This document is being developed in collaboration with Fidelity (FIS).
All other clearing providers are considered competitors and therefore
valid lead targets.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><strong>Business Context</strong></td>
</tr>
<tr class="even">
<td><p>A single closed lead in this space can be worth millions of
dollars.</p>
<p>FIS recently closed a $22 million deal through similar
intelligence.</p>
<p>The platform will serve 1–3 high-value enterprise clients under a
lean subscription model.</p>
<p>Architecture must be repackageable for future sale to firms like
Pershing.</p></td>
</tr>
</tbody>
</table>

1.1 Strategic Objectives

- **Target Acquisition:** Identify broker-dealers currently using
  competitor clearing firms (Pershing, Apex, etc.) and flag new entrants
  via Form BD.

- **Risk & Opportunity Assessment:** Pull FOCUS Reports and Annual
  Audits to evaluate net capital, financial health, and growth
  trajectory.

- **Automated Intelligence:** Replace hours of manual EDGAR research
  with automated alerts for new BD registrations, deficiency notices,
  and clearing arrangement changes.

- **Act as the CRM:** Qualify leads and deliver them within our
  platform. Clients stay in our ecosystem rather than exporting data and
  leaving.

2\. Product Scope & Boundaries

2.1 In Scope

- All currently active U.S. Broker-Dealer registrations

- New and pending Form BD filings (daily monitoring)

- Clearing relationship mapping from X-17A-5 Part III annual audited
  reports

- Financial health scoring from FOCUS Reports (net capital, YoY growth)

- Deficiency notice tracking (Form 17a-11)

- Executive/principal contact enrichment via third-party APIs
  (Apollo/ZoomInfo)

- Dashboard-based alert system for new filings and triggers

- Restricted data export (controlled CSV with limited fields)

- Global rule-based alert configuration

- Lead scoring model based on weighted net capital growth and clearing
  status

2.2 Out of Scope (Phase 1)

- Email and Slack notification integrations

- Per-user configurable alert rules (global rules only)

- Historical data beyond current active registrations

- Direct CRM integrations (Salesforce, HubSpot, etc.)

- Mobile-native applications

3\. Data Architecture & Sources

All data sources are publicly available under FOIA. There are no privacy
restrictions on mirroring this data in our repository. Developers must
respect SEC rate limits (10 req/sec) and FINRA API terms of service.

3.1 Data Source Registry

|                         |                   |               |                   |            |                                                                         |
|-------------------------|-------------------|---------------|-------------------|------------|-------------------------------------------------------------------------|
| **Data Source**         | **Form / Filing** | **Access**    | **Cost**          | **Format** | **Intelligence Extracted**                                              |
| SEC EDGAR API           | Form BD           | REST API      | Free              | HTML/Text  | New BD registrations, clearing arrangements (Item 8)                    |
| SEC EDGAR Archives      | X-17A-5 Part III  | HTTP Download | Free              | PDF        | Clearing partner name from Notes to Financial Statements                |
| SEC EDGAR API           | FOCUS Report      | REST API      | Free              | PDF/XBRL   | Net capital, excess capital, financial health metrics                   |
| SEC EDGAR API           | Form 17a-11       | REST API      | Free              | HTML/Text  | Capital deficiency notices (high-priority leads)                        |
| FINRA BrokerCheck       | Firm Profiles     | Web Scraping  | Free              | JSON       | Firm names, CRD numbers, registration status, branches                  |
| FINRA Official API      | Registration Data | OAuth 2.0 API | \$1,650/mo        | JSON       | Full BD firm list, registration details (optional Phase 2)              |
| Apollo / ZoomInfo       | Contact Data      | REST API      | Paid              | JSON       | Executive emails, phone numbers, LinkedIn profiles                      |
| LLM API (OpenAI/Claude) | PDF Processing    | REST API      | ~\$0.02–0.10/file | PDF→JSON   | Direct PDF-to-structured-data extraction (clearing partner, type, date) |

3.2 Data Refresh Cadence

|                                |                               |                                                         |
|--------------------------------|-------------------------------|---------------------------------------------------------|
| **Data Type**                  | **Refresh Frequency**         | **Rationale**                                           |
| New Form BD Filings            | Daily (automated)             | Competitive advantage: catch new entrants first         |
| Form 17a-11 Deficiency Notices | Daily (automated)             | High-priority: firms in financial trouble               |
| Master BD List / Profiles      | Bi-monthly (every 2 weeks)    | Sufficient for general BD landscape                     |
| X-17A-5 Annual Reports         | Monthly (rolling window)      | Filed annually; monthly check captures new filings      |
| FOCUS Reports / Net Capital    | Bi-monthly (every 2 weeks)    | Aligns with general data refresh                        |
| Contact Enrichment             | On-demand + quarterly refresh | Triggered when user views a lead; bulk quarterly update |

3.3 PDF Parsing Pipeline (X-17A-5)

The clearing partner name is the single most valuable data point
extracted from PDFs. It is typically found in the Notes to Financial
Statements section of the annual audited report in a sentence such as:
“The Company has a clearing agreement with \[Partner Name\] on a fully
disclosed basis.”

Pipeline Steps:

1.  Query EDGAR Submissions API for all filings of type X-17A-5 using
    the edgartools Python library.

2.  Download the PDF attachment from the EDGAR archives (free, no API
    key required).

3.  Send the PDF file directly to the OpenAI API (GPT-4o vision) or
    Anthropic Claude API (PDF support via base64 encoding). These models
    natively read PDFs without any intermediate text extraction step. Do
    NOT use Python PDF libraries (PyMuPDF, pdfplumber, pytesseract,
    etc.) as they produce unreliable results on regulatory filings. The
    LLM handles both text-based and scanned PDFs natively.

4.  Use a structured prompt instructing the LLM to extract: clearing
    partner name, clearing type (fully disclosed / self-clearing /
    omnibus), and agreement date. Request JSON output for consistent
    parsing.

5.  Store the structured result in the database and flag any extraction
    failures for manual review.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><strong>Developer Note: PDF Processing via LLM API</strong></td>
</tr>
<tr class="even">
<td><p>Register for OpenAI (or Anthropic Claude) using the Alchemy Dev
email. Payment method will be provided by the client.</p>
<p>Send PDFs directly to the LLM API (GPT-4o vision endpoint or Claude
with base64 PDF). Do NOT use Python PDF parsing libraries (PyMuPDF,
pdfplumber, pytesseract, etc.) — they produce unreliable output on
regulatory filings.</p>
<p>The LLM handles text-based PDFs, scanned PDFs, and mixed-format
documents natively with no intermediate extraction step.</p>
<p>Cost is approximately $0.02–0.10 per filing depending on page
count.</p></td>
</tr>
</tbody>
</table>

4\. User Experience & Dashboard Design

The dashboard must be enterprise-grade, visually polished, and optimized
for the workflows of 1–3 high-value users who are senior sales
executives in the clearing industry. Every element must convey trust,
professionalism, and data richness.

4.1 Design System

Color Palette:

■ Primary Navy (#0A1F3F) — Headers, navigation, primary backgrounds

■ Accent Blue (#1B5E9E) — Interactive elements, links, selection states

■ Gold (#E8A838) — Highlight badges, premium elements, call-to-action
buttons

■ Success Green (#27AE60) — Healthy status, positive indicators, growth
metrics

■ Warning Amber (#F39C12) — Moderate risk, attention-required states

■ Critical Red (#E74C3C) — Deficiency alerts, unhealthy financial
status, urgent items

■ Light Gray (#ECF0F1) — Backgrounds, card surfaces, alternating table
rows

Typography:

- Primary Font: Inter (Google Fonts) — modern, highly legible at all
  sizes

- Fallback: -apple-system, BlinkMacSystemFont, Segoe UI, Arial

- Heading sizes: H1 = 28px bold, H2 = 22px semibold, H3 = 18px medium

- Body: 14px regular, Small/Labels: 12px

- Monospace (for CIK/CRD numbers): JetBrains Mono or Fira Code

4.2 Authentication & Access Control

Implement credentialed access using BetterAuth
(https://better-auth.com). BetterAuth is a TypeScript-native,
framework-agnostic authentication library that integrates directly with
Next.js. The system should support:

- Email/password login with MFA (optional but recommended)

- Role-based access: Admin (full control) and Viewer (read-only,
  restricted export)

- Session timeout after 30 minutes of inactivity

- Audit logging of all user actions (login, search, export, view
  details)

4.3 Navigation Structure

|                |                                               |                                                   |
|----------------|-----------------------------------------------|---------------------------------------------------|
| **Nav Item**   | **Description**                               | **Key Features**                                  |
| Dashboard Home | Executive overview with KPIs and alerts       | KPI cards, new filings feed, activity charts      |
| Master List    | Searchable table of all active Broker-Dealers | Search, filter, sort, inline status badges        |
| Firm Detail    | Deep-dive profile for a single BD             | Financials, clearing info, contacts, filings      |
| Alerts         | New Hits section with filing alerts           | Filterable alert feed with priority badges        |
| Export         | Controlled lead export                        | Limited CSV with pre-defined fields only          |
| Settings       | Global rule configuration                     | Alert triggers, scoring weights, refresh settings |

4.4 Dashboard Home Screen

The home screen is the first thing a user sees after login. It must
immediately surface actionable intelligence.

KPI Cards (Top Row):

|                   |                                |                 |                         |
|-------------------|--------------------------------|-----------------|-------------------------|
| **Card**          | **Metric**                     | **Color**       | **Icon**                |
| Total Active BDs  | Count of active broker-dealers | Navy background | Building/Office icon    |
| New BDs (30 days) | Form BD filings in last 30d    | Blue background | Sparkline + ↑ indicator |
| Deficiency Alerts | Active 17a-11 filings          | Red background  | Warning triangle icon   |
| High-Value Leads  | Firms matching ideal criteria  | Gold background | Target/bullseye icon    |

Activity Feed (Left Panel):

A reverse-chronological feed of recent filings and system events. Each
entry shows: filing type badge (color-coded), firm name, date, and a
one-line summary. Clicking an entry navigates to the firm detail page.

Clearing Distribution Chart (Right Panel):

A donut chart showing the market share of clearing providers (Pershing,
Apex, self-clearing, etc.) with counts and percentages. Clicking a
segment filters the master list to firms using that provider.

4.5 Master List View

The core working view. A data-rich table with advanced filtering,
sorting, and inline visual indicators.

Table Columns:

|                  |                   |                          |              |                |           |
|------------------|-------------------|--------------------------|--------------|----------------|-----------|
| **Column**       | **Source**        | **Display**              | **Sortable** | **Filterable** | **Width** |
| Firm Name        | FINRA / EDGAR     | Clickable link to detail | Yes (A-Z)    | Search         | 20%       |
| CIK / CRD        | SEC / FINRA       | Monospace font           | Yes          | Search         | 10%       |
| Clearing Partner | X-17A-5 PDF parse | Text + competitor badge  | Yes          | Dropdown       | 15%       |
| Clearing Type    | X-17A-5 / Form BD | Badge: Self / Disclosed  | Yes          | Toggle         | 10%       |
| Financial Health | FOCUS Report      | Color badge              | Yes          | Dropdown       | 10%       |
| Net Capital      | FOCUS Report      | Formatted currency       | Yes          | Range slider   | 10%       |
| YoY Growth       | Calculated        | ↑↓ with % and color      | Yes          | Range slider   | 8%        |
| Location         | Form BD           | City, State              | Yes          | Dropdown       | 10%       |
| Last Filing      | EDGAR             | Relative date            | Yes          | Date range     | 7%        |

4.6 Color-Coded Status System

All status indicators must use a consistent, enterprise-standard color
system across the entire platform:

Financial Health Badges:

|            |                   |                                                                           |
|------------|-------------------|---------------------------------------------------------------------------|
| **Status** | **Badge Color**   | **Criteria**                                                              |
| Healthy    | ● Green (#27AE60) | Net capital \> 120% of required minimum AND positive YoY growth           |
| OK         | ● Amber (#F39C12) | Net capital 100–120% of required minimum OR flat/slight decline in growth |
| At Risk    | ● Red (#E74C3C)   | Net capital \< 100% of minimum OR filed Form 17a-11 deficiency notice     |

Clearing Type Badges:

|                   |                      |                                                                |
|-------------------|----------------------|----------------------------------------------------------------|
| **Type**          | **Badge Style**      | **Significance**                                               |
| Self-Clearing     | Navy pill badge      | HIGH VALUE: Target for outsourcing pitch                       |
| Fully Disclosed   | Blue pill badge      | PRIMARY TARGET: Using a competitor; target for switch campaign |
| Omnibus           | Gray pill badge      | Lower priority but still a potential lead                      |
| Unknown / Pending | Dashed outline badge | PDF not yet parsed or extraction failed; flagged for review    |

Lead Priority Indicators:

|              |                              |                                                                |
|--------------|------------------------------|----------------------------------------------------------------|
| **Priority** | **Visual**                   | **Definition**                                                 |
| Hot Lead ★★★ | Gold star + gold left border | Healthy financials + competitor clearing + growing net capital |
| Warm Lead ★★ | Blue star + blue left border | Meets 2 of 3 ideal criteria                                    |
| Cold Lead ★  | Gray star + no border        | Meets 1 or 0 ideal criteria                                    |

4.7 Firm Detail Page

Accessed by clicking a firm name in the Master List. This page provides
a comprehensive 360-degree view of a single broker-dealer.

Layout Sections:

1.  **Header Bar:** Firm name, CIK/CRD numbers, FINRA membership status
    badge, financial health badge, clearing type badge, and lead
    priority stars. All in a single navy-background bar.

2.  **Financial Overview Card:** Net capital (current and historical),
    excess capital, YoY growth trend line (sparkline or small chart),
    financial health status with color-coded background.

3.  **Clearing Arrangements Card:** Current clearing partner name,
    clearing type, source document link (to original X-17A-5 PDF on
    EDGAR), date of last audit report, history of clearing partners if
    multiple years available.

4.  **Executive Contacts Card:** Principals and executives from Form BD
    with enriched contact data (email, phone, LinkedIn) from
    Apollo/ZoomInfo. Each contact shows name, title, and direct contact
    methods.

5.  **Filing History Card:** Chronological list of all SEC filings for
    this entity. Each entry shows form type, date, and a link to the
    original document on EDGAR. Filterable by form type.

6.  **Registration & Compliance Card:** FINRA membership status, state
    registrations, any disciplinary actions or disclosures, SIC code,
    business type.

5\. Alert System

Alerts are dashboard-only for Phase 1. No email or Slack integrations.

5.1 Alert Triggers

|                             |                       |              |                                         |
|-----------------------------|-----------------------|--------------|-----------------------------------------|
| **Trigger**                 | **Source**            | **Priority** | **Action**                              |
| New Form BD Filing          | EDGAR daily scan      | ● High       | Add to Master List + alert feed         |
| Form 17a-11 Deficiency      | EDGAR daily scan      | ● Critical   | Move to “Alternative List” + alert feed |
| New X-17A-5 Filing          | EDGAR monthly scan    | ● Medium     | Trigger PDF parsing pipeline            |
| Clearing Partner Change     | PDF parse comparison  | ● High       | Update clearing map + alert feed        |
| Net Capital Threshold Cross | FOCUS Report analysis | ● Medium     | Update health badge + alert feed        |

5.2 Deficiency Notice Handling

Per client direction, firms filing Form 17a-11 deficiency notices should
NOT appear in the primary leads list. Instead:

- **Exclude** from the main Master List view (or visually demote them).

- **Move** to a separate “Alternative List” tab for firms that may need
  financial help.

- **The primary focus** remains on healthy, growing firms that are
  clearing through competitors.

6\. Lead Scoring Model

The scoring model identifies the “ideal client”: a financially healthy,
growing broker-dealer currently clearing through a competitor, who could
be shown how to increase revenue by transitioning to self-clearing or
our clearing arrangements.

6.1 Scoring Factors

|                          |            |                                                          |                                                                   |
|--------------------------|------------|----------------------------------------------------------|-------------------------------------------------------------------|
| **Factor**               | **Weight** | **Scoring Logic**                                        | **Rationale**                                                     |
| Net Capital Growth (YoY) | 35%        | \>10% = 10pts, 5–10% = 7pts, 0–5% = 4pts, \<0% = 1pt     | Weighted net capital growth is the primary indicator (per client) |
| Clearing Arrangement     | 30%        | Competitor = 10pts, Self-clearing = 8pts, Unknown = 3pts | BDs clearing through competitors are the ideal target             |
| Financial Health         | 20%        | Healthy = 10pts, OK = 5pts, At Risk = 0pts               | We target healthy firms; deficient firms go to alt list           |
| Recency of Registration  | 15%        | \<90 days = 10pts, \<1yr = 6pts, \>1yr = 3pts            | New entrants are more open to clearing partnerships               |

**Total Score Range: 0–100.** Hot Lead = 75–100 \| Warm Lead = 45–74 \|
Cold Lead = 0–44

7\. Data Export & Ecosystem Lock-In

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><strong>Critical Business Requirement</strong></td>
</tr>
<tr class="even">
<td><p>We act as the CRM. We qualify leads and deliver them within our
platform.</p>
<p>We must be RESTRICTIVE with data exports. We do NOT want clients
downloading a CSV and leaving.</p>
<p>Clients must stay in our ecosystem to get full value.</p></td>
</tr>
</tbody>
</table>

7.1 Permitted Export Fields

The following fields, and ONLY these fields, may be exported to CSV:

- Broker Name

- CIK Identifier

- Financial Health Status (Healthy / OK / At Risk)

- Net Capital Growth (Year-over-Year percentage)

- Current Clearing Arrangements (partner name + type)

- Location (City, State)

- Last Filing Date

- FINRA Membership Status

- Executive/Principal Contact Names (names only, NOT email/phone)

7.2 Export Restrictions

- No email addresses, phone numbers, or LinkedIn URLs in exports.

- Export limited to 100 records per download.

- All exports logged with user ID, timestamp, and record count.

- Watermark or footer in exported CSV identifying the source platform.

- Rate limit: maximum 3 exports per user per day.

8\. Technical Architecture

8.1 Infrastructure

|                    |                                                                                                                |
|--------------------|----------------------------------------------------------------------------------------------------------------|
| **Component**      | **Specification**                                                                                              |
| Hosting            | Google Cloud Platform (GCP) — per client directive                                                             |
| Frontend           | React.js (Next.js recommended) with Tailwind CSS or Chakra UI                                                  |
| Backend API        | Python (FastAPI or Django REST Framework)                                                                      |
| Database           | PostgreSQL (Cloud SQL on GCP) for structured data                                                              |
| Job Scheduler      | Cloud Scheduler + Cloud Functions (or Celery + Redis for complex pipelines)                                    |
| PDF Storage        | Google Cloud Storage (GCS) bucket for cached PDFs                                                              |
| Authentication     | BetterAuth (TypeScript-native, self-hosted, integrates with Next.js + PostgreSQL)                              |
| LLM Integration    | OpenAI GPT-4o Vision API or Anthropic Claude API — PDFs sent directly to the LLM (no Python parsing libraries) |
| Contact Enrichment | Apollo.io or ZoomInfo API (setup via Alchemy Dev email, client pays)                                           |
| EDGAR Access       | edgartools Python library + direct SEC REST APIs (free, User-Agent header only)                                |
| FINRA Access       | BrokerCheck scraping (free) for Phase 1; official API (\$1,650/mo) optional Phase 2                            |
| Monitoring         | GCP Cloud Monitoring + Logging; Sentry for error tracking                                                      |

8.2 Data Pipeline Architecture

The system operates three parallel pipelines:

Pipeline A — Daily Filing Monitor:

1.  Cloud Scheduler triggers a Cloud Function every morning at 6:00 AM
    EST.

2.  Function queries EDGAR Submissions API for new Form BD and 17a-11
    filings since last run.

3.  New filings are parsed, entities extracted, and inserted into
    PostgreSQL.

4.  Dashboard alert feed is updated; KPI counters are recalculated.

Pipeline B — PDF Parsing Pipeline:

1.  Monthly job scans for new X-17A-5 filings.

2.  PDFs are downloaded to GCS bucket for caching.

3.  PDF is sent directly to OpenAI GPT-4o vision API (or Claude API with
    base64 PDF). No intermediate Python text extraction is used.

4.  LLM returns structured JSON with clearing partner, clearing type,
    and confidence score.

5.  Structured clearing data is stored; extraction confidence score is
    recorded.

6.  Low-confidence extractions are flagged for manual review in the
    dashboard.

Pipeline C — Bi-Monthly Full Refresh:

Runs every two weeks. Updates the full Master List with current FINRA
data, refreshes financial metrics from FOCUS reports, recalculates lead
scores, and updates health badges.

9\. Competitive Landscape & Targeting

This platform is being built in collaboration with Fidelity (FIS).
Therefore, FIS/Fidelity is NOT a competitor for targeting purposes. All
other clearing providers are fair game.

9.1 Competitor Clearing Providers to Track

|                            |                    |                   |
|----------------------------|--------------------|-------------------|
| **Provider**               | **Type**           | **Priority**      |
| Pershing (BNY Mellon)      | Clearing / Custody | ● High Priority   |
| Apex Clearing              | Clearing / Custody | ● High Priority   |
| Hilltop Securities         | Clearing           | ● Medium Priority |
| RBC Correspondent Services | Clearing / Custody | ● Medium Priority |
| Axos Clearing              | Clearing           | ● Medium Priority |
| Vision Financial Markets   | Clearing           | ● Standard        |
| Self-Clearing Firms        | Internal           | ● High Value      |

**Note:** The competitor list should be configurable in the Settings
panel so new providers can be added without code changes.

10\. Account Setup & Credential Requirements

All third-party accounts and API credentials should be registered using
the Alchemy Dev email. The client will provide payment methods for any
paid services. The developer should not be out of pocket.

|                       |                                   |                    |                                                 |
|-----------------------|-----------------------------------|--------------------|-------------------------------------------------|
| **Service**           | **Account Type**                  | **Cost**           | **Setup Notes**                                 |
| SEC EDGAR             | User-Agent header only            | Free               | Set header to Alchemy Dev contact info          |
| FINRA Gateway         | New credentials needed            | Free (BrokerCheck) | Previous creds expired; start fresh             |
| OpenAI / Claude API   | API key                           | Pay-per-use        | Register with Alchemy Dev email                 |
| Apollo / ZoomInfo     | API key                           | Paid subscription  | Register with Alchemy Dev email                 |
| BetterAuth            | Self-hosted (Next.js integration) | Free (open-source) | No external account needed; runs within the app |
| Google Cloud Platform | Project + billing                 | Pay-per-use        | Use existing GCP environment                    |

11\. Non-Functional Requirements

11.1 Performance

- Master List page load: \< 2 seconds for up to 5,000 records.

- Search/filter response: \< 500ms.

- Firm Detail page load: \< 1.5 seconds.

- PDF parsing pipeline: process up to 50 filings per hour.

- Daily filing check: complete within 15 minutes.

11.2 Security

- All traffic over HTTPS (TLS 1.2+).

- Authentication via BetterAuth with email/password + optional MFA.

- Role-based access control (Admin, Viewer).

- Audit logging for all user actions.

- API keys stored in GCP Secret Manager, never in source code.

- Rate limiting on all API endpoints.

11.3 Scalability

The system is designed for 1–3 concurrent users initially. However, the
architecture must be clean and modular so it can be repackaged and sold
to other firms (e.g., Pershing). Design for eventual multi-tenant
support.

11.4 Availability

Target 99.5% uptime. The platform is a business intelligence tool, not a
trading system; brief maintenance windows during off-hours are
acceptable.

12\. Delivery & Timeline

The client requires the fastest possible turnaround. The goal is to get
a prototype or sample data to the client immediately so billing can
begin. Revenue generation is the priority.

12.1 Recommended Phased Approach

While the client is open to any workflow management, the following
phased approach is recommended to deliver value incrementally:

|           |                              |                                                                                |              |
|-----------|------------------------------|--------------------------------------------------------------------------------|--------------|
| **Phase** | **Deliverable**              | **Scope**                                                                      | **Timeline** |
| Phase 1   | Master List + Auth           | Login, searchable BD table, FINRA data, basic filters, KPI cards               | Week 1–2     |
| Phase 2   | Financial Health + Alerts    | FOCUS report integration, health badges, daily filing alerts, 17a-11 tracking  | Week 2–3     |
| Phase 3   | Clearing Map (PDF Pipeline)  | X-17A-5 download + LLM parsing, clearing partner mapping, clearing type badges | Week 3–4     |
| Phase 4   | Contact Enrichment + Scoring | Apollo/ZoomInfo integration, lead scoring model, firm detail page, export      | Week 4–5     |
| Phase 5   | Polish + Production          | Enterprise UI polish, charts, competitor analytics, GCP production deploy      | Week 5–6     |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><strong>Client Directive on Budget</strong></td>
</tr>
<tr class="even">
<td><p>Development costs are front-loaded. Keep everything as
cost-effective and simple as possible.</p>
<p>Get a prototype or sample data to the client ASAP to start
billing.</p>
<p>Once revenue is flowing, we scale. There is a substantial finder’s
fee on the back end for closed deals.</p>
<p>Current model: lean subscription pricing.</p></td>
</tr>
</tbody>
</table>

13\. Acceptance Criteria

The platform is considered complete when the following criteria are met:

6.  A user can log in via BetterAuth (email/password) and see the
    Dashboard Home with live KPI cards.

7.  The Master List displays all currently active U.S. Broker-Dealers
    with correct CIK/CRD identifiers.

8.  Each firm displays a color-coded Financial Health badge
    (Green/Amber/Red) derived from FOCUS Report data.

9.  The Clearing Partner column is populated for at least 80% of firms
    with X-17A-5 filings via the PDF parsing pipeline.

10. Clearing Type badges (Self-Clearing / Fully Disclosed / Omnibus /
    Unknown) render correctly.

11. New Form BD filings from the previous business day appear in the
    alert feed by 9:00 AM EST.

12. Form 17a-11 deficiency notices are detected daily and firms are
    moved to the Alternative List.

13. The Firm Detail page shows financial data, clearing arrangements,
    executive contacts, and filing history.

14. Contact enrichment (Apollo/ZoomInfo) populates email and phone for
    at least 60% of listed executives.

15. Lead scores are calculated and firms are ranked by Hot / Warm / Cold
    priority.

16. CSV export includes only the permitted fields and respects the
    100-record and 3-per-day limits.

17. The competitor clearing provider list is configurable without code
    changes.

18. All pages load within the performance thresholds defined in Section
    11.

19. The system is deployed on GCP and accessible via a custom domain
    with HTTPS.

14\. Appendix

14.1 Glossary

|                    |                                                                                                            |
|--------------------|------------------------------------------------------------------------------------------------------------|
| **Term**           | **Definition**                                                                                             |
| BD / Broker-Dealer | A firm registered with the SEC and FINRA to buy/sell securities on behalf of customers or its own account. |
| CIK                | Central Index Key — unique SEC identifier for every EDGAR filer.                                           |
| CRD                | Central Registration Depository — FINRA’s unique identifier for firms and individuals.                     |
| Form BD            | Uniform Application for Broker-Dealer Registration. The “birth certificate” of a BD.                       |
| X-17A-5 Part III   | Annual audited financial report filed by broker-dealers with the SEC.                                      |
| FOCUS Report       | Financial and Operational Combined Uniform Single report. Contains net capital data.                       |
| Form 17a-11        | Notification of capital deficiency filed by a broker-dealer.                                               |
| Fully Disclosed    | A clearing arrangement where the introducing BD’s customers are known to the clearing firm.                |
| Self-Clearing      | A BD that handles its own trade settlement and custody without a third-party clearing firm.                |
| FOIA               | Freedom of Information Act — ensures public access to government data.                                     |
| FIS                | Fidelity Information Services — the collaborating client for this platform.                                |
| EDGAR              | Electronic Data Gathering, Analysis, and Retrieval — the SEC’s filing system.                              |

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

