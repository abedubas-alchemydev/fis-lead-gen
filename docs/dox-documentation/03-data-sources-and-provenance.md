# 03 — Data Sources & Provenance

[← System Architecture](02-system-architecture.md) | [Index](README.md) | [Next: Third-Party Services →](04-third-party-services.md)

---

This document inventories **every source of data** in DOX: what is collected, from where, by what mechanism, at what rate, under what identification, and where it lands in the database. Sources fall into three classes:

- **A. U.S. government / regulator sources** (SEC, FINRA) — public regulatory disclosures; the backbone of the dataset.
- **B. Self-regulatory / clearing-agency directories** (DTCC family, OCC) — public membership lists.
- **C. Commercial and open-web sources** — licensed APIs and firms' own public websites, used only to *augment* contact details and websites for firms already identified from class A. (Per-provider contractual detail is in [Document 04](04-third-party-services.md).)

A recurring design principle, important for any accuracy or licensing analysis: **every materially derived field carries provenance** — the source (`matched_source`, `discovery_source`, `website_source`, `source_file`), a confidence score where extraction was probabilistic, and, for filings-derived facts, a link back to the source document on the regulator's own site.

---

## 3.1 SEC EDGAR (broker-dealer filings)

| | |
|---|---|
| Operator | U.S. Securities and Exchange Commission |
| Code | `backend/app/services/edgar.py`, `pdf_downloader.py`, `filing_monitor.py`, `focus_reports.py`; acquisition also in `brokercheck_extractor/acquisition/sec_edgar_client.py` |
| Endpoints | `www.sec.gov/cgi-bin/browse-edgar` (company search, SIC 6211 — Security Brokers & Dealers); `data.sec.gov/submissions/CIK{n}.json` (per-firm filing index); `www.sec.gov/Archives/edgar/data/...` (documents); bulk `submissions.zip` fallback |
| Identification | Declared `User-Agent` with contact email, per SEC's automated-access guidance. Configured via `SEC_USER_AGENT`; the current default string is `"Alchemy Dev compliance@alchemy.dev"` (legacy branding — flagged in [Doc 11](11-legal-considerations-for-counsel.md)) |
| Rate & retry | Client-side throttle of 10 requests/second (`EDGAR_RATE_LIMIT_PER_SECOND`), matching SEC's published fair-access ceiling; HTTP 429 responses honor the `Retry-After` header with exponential backoff; per-CIK results cached in-process for 1 hour; the bulk ZIP is cached locally for 7 days |

**Forms ingested and what is taken from each:**

| Form | Nature | Extracted | Stored in |
|------|--------|-----------|-----------|
| **Form BD** | Broker-dealer registration / amendments | Registration events drive alerts; registration & formation dates parsed from the PDF for newly discovered firms | `filing_alert`, `broker_dealers` |
| **Form X-17A-5** ("FOCUS report") | Annual audited financial report | Net capital, excess net capital, total assets, required minimum capital, report date; clearing-arrangement language; "person to contact" block (name/title/phone/email); auditor name | `financial_metrics`, `clearing_arrangements`, `executive_contacts` (source `focus_report`), `broker_dealers` snapshot columns |
| **Form 17a-11** | Net-capital deficiency notice | Deficiency event and date (firm flagged `is_deficient`, demoted to the "Alternative" list) | `filing_alert`, `broker_dealers` |

Numeric and classification extraction from PDFs is performed by Google Gemini under structured-output schemas with **minimum-confidence gates** (financials 0.65, clearing 0.7); below-threshold extractions are stored as `needs_review`, not presented as fact. The source filing URL is preserved with the extraction so any figure can be traced to the SEC original.

## 3.2 SEC EDGAR full-text search ("EFTS")

| | |
|---|---|
| Code | `backend/app/services/form4_watcher.py`, `form4_xml_parser.py`, `thirteen_f_filter.py` |
| Endpoint | `efts.sec.gov/LATEST/search-index` |
| Uses | (1) **Form 4** insider transactions — daily watcher over a 7-day overlapping lookback, parsed from the filing XML into `form4_transactions` (issuer, reporting person and role flags, transaction code/date/shares/price/value), with a minimum-transaction-value floor (default $50,000) and idempotent dedupe keys; (2) **Form 13F-HR** — enumerating recent 13F filers (120-day lookback, partitioned into 7-day windows) to flag which registered advisers are institutional money managers |
| Rate | ~8 requests/second client-side delay — under SEC's 10/s ceiling |

## 3.3 SEC IAPD (Investment Adviser Public Disclosure)

| | |
|---|---|
| Code | `backend/app/services/iapd.py`, `investment_advisors.py`, `advisor_refresh_orchestrator.py`; backfill scripts |
| Sources | (1) SEC's **monthly Investment Adviser compilation ZIP** (~17,000 rows × 448 Form ADV columns; link discovered at runtime from the SEC index page; cached 7 days); (2) **Form ADV PDFs** per firm from `reports.adviserinfo.sec.gov`; (3) the **adviserinfo firm-summary API** |
| Extracted | Firm identity (CRD, SEC#, CIK, names, location), registration status, website, regulatory assets under management (discretionary / non-discretionary / total), client counts and types, advisory activities, officers/owners, other business names (DBAs) |
| Stored in | `investment_advisors`, `advisor_contacts`, `advisor_filings` |

Column mapping is by **header name**, not position, so an SEC layout change degrades to logged `None` values rather than silently mis-mapped data.

## 3.4 FINRA BrokerCheck

| | |
|---|---|
| Operator | Financial Industry Regulatory Authority |
| Code | `backend/app/services/finra.py`; `brokercheck_extractor/` (standalone pipeline); `scripts/standalone_extract_new_bds.py` (nightly discovery) |
| Endpoints | `api.brokercheck.finra.org/search/firm` (the JSON search API that backs FINRA's own BrokerCheck site); `files.brokercheck.finra.org/firm/firm_{crd}.pdf` (the public firm-report PDF) |
| Extracted | Firm discovery (CRD, name, SEC file number, city/state, scope); from the PDF: registration/termination/formation dates, officers & directors with positions and ownership percentages, types of business, clearing-arrangement statements, introducing arrangements, disclosure counts |
| Rate & politeness | Deliberately conservative: 2 requests/second cap plus an additional 0.5s inter-request delay, 4 retries with exponential backoff; the nightly new-firm probe is bounded (~500 sequential CRD probes/night); PDF re-parsing is skipped when the document's SHA-256 hash is unchanged |
| Identification | Requests send standard browser-style headers (Chrome User-Agent, `Accept-Encoding: identity`, origin/referer) because the endpoint sits behind a CDN gateway that rejects non-browser clients. The system does **not** authenticate to, log into, or bypass any access control — these are the same unauthenticated requests a visitor's browser makes. The practice is flagged for counsel in [Doc 11](11-legal-considerations-for-counsel.md) §4 |

The BrokerCheck PDF pipeline (`brokercheck_extractor/`) is a four-tier hybrid: deterministic text parsing (pdfplumber → PyMuPDF → OCR) for ~95% of documents; an always-on Gemini Flash cross-validation; Gemini Pro escalation on disagreement (~5–10%); and a human-review queue for the ambiguous tail (~1–2%). Every write is idempotent (`ON CONFLICT` keyed on CRD).

## 3.5 Clearing-agency membership directories (DTCC family and OCC)

| | |
|---|---|
| Agencies | OCC, DTC, NSCC, FICC-GOV, FICC-MBS (`CLEARING_AGENCIES` in `backend/app/models/clearing_agency_membership.py`) |
| Mechanism | **Operator-committed CSV files** in `backend/data/clearing_directories/` (`occ_members.csv`, `dtc_participants.csv`, `nscc_members.csv`, `ficc_gov_members.csv`, `ficc_mbs_members.csv`), loaded by `scripts/import_clearing_agency_memberships.py` |
| Why not live API | The DTCC and OCC websites refuse automated fetches (HTTP 403). The directories are public pages; they were captured manually (a person in a normal browser) and committed as data files, refreshed by the operator rather than scraped on a schedule. Flagged for counsel in [Doc 11](11-legal-considerations-for-counsel.md) §4 |
| Matching & provenance | Directory entries are matched to firms by normalized name / DBA / alias; each membership row records `member_name_raw`, `source_file`, `source_version`, `match_method` (`exact_normalized` / `dba` / `alias` / `manual`) and `match_confidence`; ambiguous matches are routed to `needs_review` instead of being auto-applied; a `clearing_membership_checked_at` stamp distinguishes "checked — not a member" from "never checked" |

## 3.6 Firms' own public websites

Used in two narrow ways, both only after a firm is already in the dataset:

1. **Website resolution** (`backend/app/services/website_resolver.py`): when a firm has no website on file, candidates from Apollo's organization search and Google results (via SerpAPI) are validated — blocklisted domains rejected (LinkedIn, news/aggregator/profile sites, sec.gov, finra.org, etc.), reachability checked, and the domain or page title must actually match the firm — before a URL is stored with `website_source`.
2. **Public-page contact fallback** (`contact_discovery/web_fallback.py`, `site_crawler.py`, and the email-extractor `web_scraper` step): fetches pages of the **firm's own site** looking for *literally published* emails and phone numbers. Policy is conservative by construction: an email attaches to a person only if its local-part encodes that person's name; generic inboxes (info@, sales@, compliance@, ~40 patterns) never attach to a person; a phone number attaches only if it appears within 160 characters of the person's name or matched email. The whole path is feature-flagged (`WEB_FALLBACK_ENABLED`, `WEB_FALLBACK_PHONES_ENABLED`).

No login-walled, paywalled, or robots-restricted crawling is implemented; fetches are direct HTTP GETs of public pages at low volume (single pages per firm on demand, not a spider).

## 3.7 Open-source intelligence (Email Extractor scans)

The user-initiated Email Extractor (`backend/app/services/email_extractor/`) discovers published email addresses for a domain through: **Hunter.io** and **Snov.io** domain searches (licensed APIs), the firm's own site (site crawler, §3.6 policy), and **theHarvester**, an open-source OSINT tool configured with passive sources only (`crtsh` certificate-transparency logs, `rapiddns`, `otx`, `duckduckgo` — `THEHARVESTER_SOURCES`). Optional verification performs **SMTP RCPT-level checks** (connecting to the recipient mail server without sending mail) in small batches (≤25, concurrency 1, identifying HELO host) — see [Doc 11](11-legal-considerations-for-counsel.md) §6.

## 3.8 Commercial enrichment providers

Apollo.io, People Data Labs, Hunter.io, Snov.io and SerpAPI augment contacts and websites under paid API agreements. The full per-provider record — endpoints called, exactly what is transmitted and received, configuration names — is [Document 04](04-third-party-services.md); the personal-data view of the same flows is [Document 05](05-personal-data-and-privacy.md).

## 3.9 Pipelines and schedules (how data stays current)

| Pipeline | Trigger / cadence | What it does |
|----------|-------------------|--------------|
| Daily filing monitor | Daily (admin- or scheduler-triggered) | Checks each firm's EDGAR submissions for new Form BD / X-17A-5 / 17a-11 (30-day window); raises alerts; auto-queues financial extraction for watched firms |
| Form 4 watcher | Daily | Ingests new insider transactions (7-day overlapping window, idempotent) |
| Nightly new-BD extractor | Cloud Scheduler `fis-extract-new-bds-nightly` (4 AM ET) → Cloud Run Job | Probes FINRA for CRDs above the current maximum; inserts new firms (with Form BD dates); embeds them into the semantic index |
| Per-firm "Refresh all" | User button | Parallel sub-pipelines fill only missing data: financials, website, registration dates, clearing, contacts, FOCUS contact |
| Gap-fill jobs | Cloud Run Jobs (batch) | The same fill-only-gaps logic across the whole list, bounded per run |
| Clearing pipeline / FINRA detail refresh | Admin-triggered | Re-extracts and re-classifies clearing arrangements |
| Embedding backfill | Admin endpoint / nightly hook | Keeps the semantic-search index in sync (content-hash dedupe) |

All pipeline executions are recorded in `pipeline_run` rows (status, counts, parent/child structure), surfaced in the admin UI and via Doxie's `get_data_freshness` tool.

## 3.10 Accuracy controls (summary)

- LLM extractions carry confidence scores and are gated by minimums; failures and low confidence become `needs_review`, never silent facts.
- "Unknown" is an explicit, categorized state (`firm_does_not_disclose`, `no_filing_available`, `low_confidence_extraction`, `pdf_unparseable`, `provider_error`, `not_yet_extracted`) shown to users with tooltips, rather than blank or guessed values.
- Filings-derived figures link to the SEC source document.
- Contact fields record which provider produced them and at what confidence; the admin Extraction Analytics page (`/settings/extractions`) makes per-provider attribution inspectable firm by firm.
- All ingestion is idempotent and re-runnable (conflict-keyed upserts, dedupe keys, PDF content hashing).

---

[← System Architecture](02-system-architecture.md) | [Index](README.md) | [Next: Third-Party Services →](04-third-party-services.md)
