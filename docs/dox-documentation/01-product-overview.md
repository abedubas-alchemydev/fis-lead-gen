# 01 — Product Overview

[← Index](README.md) | [Next: System Architecture →](02-system-architecture.md)

---

## 1.1 What DOX is

DOX is a **broker-dealer and investment-adviser intelligence platform** — a lead-generation and market-research tool for companies that sell **securities clearing services**. It continuously aggregates public regulatory data about every active U.S. broker-dealer ("BD") and SEC-registered investment adviser ("IA" / "RIA"), and answers, for a sales organization, three questions:

1. **Who exists?** Every active U.S. broker-dealer (~3,000 firms as of June 2026) and registered investment adviser (~17,000 ingested; the ~3,100 that file Form 13F holdings reports are surfaced by default), with identifiers (CRD, CIK, SEC file number), location, registration dates, and lines of business.
2. **Who is worth calling?** Each firm is profiled with financial metrics extracted from its audited annual filings (net capital, excess net capital, total assets, year-over-year growth), classified by its **clearing arrangement** (self-clearing, fully disclosed through a clearing partner, omnibus, non-carrying, or unknown), checked against clearing-agency membership directories (OCC, DTC, NSCC, FICC), and scored into Hot / Warm / Cold lead priorities using configurable weights.
3. **Who do I talk to, and how?** Firm officer and executive contacts are assembled from regulatory filings and, where licensed commercial providers can corroborate them, enriched with business email addresses, phone numbers, and LinkedIn profiles. Users can compose and send individual outreach emails from their **own linked mailbox** (Gmail, Outlook, or Yahoo), with every send recorded in an audit trail.

An embedded AI assistant, **Doxie**, lets users ask natural-language questions over the same data ("which self-clearing firms in Texas grew net capital last year?"), summarize regulatory filings, and draft outreach emails — always with a human confirmation step before anything is sent.

## 1.2 Who it is for

Per the product requirements document (`Documentation/Old One/UPDATED_PRD.md`, April 2026), DOX was commissioned for a **clearing-services provider's sales organization** (the PRD names Fidelity Information Services / FIS as the client context) seeking to win clearing business from competing providers (Pershing, Apex, Hilltop, RBC, Axos, Vision and others). The intended user base is deliberately small — **one to three senior sales executives per client** — under a subscription model. The architecture was specified to be repackageable for other clearing-industry customers later.

The dollar context matters for understanding the product's design: a single closed clearing deal can be worth millions of dollars, so the product optimizes for **depth and accuracy on a few thousand firms** rather than breadth across millions of records.

## 1.3 The problem it replaces

Before DOX, this research was manual: an analyst would search SEC EDGAR for a firm's annual audited report (Form X-17A-5), read the PDF to find its net capital and its clearing arrangement, cross-check FINRA BrokerCheck for the firm's profile and officers, and repeat for thousands of firms — with no alerting when a firm filed something new. DOX automates that loop:

- **Daily filing monitoring** raises in-app alerts when a firm files a new Form BD (registration), Form X-17A-5 (annual audited report), or Form 17a-11 (net-capital deficiency notice).
- **Automated PDF extraction** (Google Gemini, with confidence thresholds and a human-review fallback) pulls financial figures and clearing language out of the filings.
- **A nightly job** discovers newly registered broker-dealers and adds them to the list.
- **Lead scoring** ranks the result so the sales team starts at the top.

## 1.4 Feature summary

| Area | Features |
|------|----------|
| **Research** | Broker Dealers list (filterable by financial health, clearing partner/type, net capital range, state, business types, registration date); firm detail pages with financials, clearing arrangements (linked to source SEC filings), officers/contacts, filing history, clearing-agency memberships; Investment Advisers list with AUM, advisory activities, 13F-filer filter; Form 4 insider-transaction feed (`/investors`) |
| **Monitoring** | Dashboard KPIs; filing alerts feed (Form BD, 17a-11, X-17A-5) with priorities and read state; real-time alert stream; data-freshness reporting |
| **Contacts & enrichment** | Officer rosters from filings; on-demand enrichment via a provider chain (Apollo → People Data Labs → Hunter → Snov → name-lookup → public-web fallback); Email Extractor (domain scans via Hunter/Snov/theHarvester with optional SMTP verification); per-provider extraction analytics for administrators |
| **Outreach** | Per-user OAuth-linked sender mailboxes (Gmail / Microsoft / Yahoo); AI-drafted or hand-written one-to-one emails; drafts; To/Cc/Bcc; full sent-history audit; per-user signature; "Vault" document folders that ground AI drafts in the user's own service material |
| **AI assistant (Doxie)** | 42 registered tools over the application's own data (search, profiles, filings summaries, aggregates, favorites, alerts, outreach drafting with confirmed send, vault retrieval, web research for unfamiliar terms); semantic firm search over vector embeddings; conversation history |
| **Administration** | User management with role + per-feature permissions and an approval gate for new signups; scoring-weight configuration; competitor/clearing-provider list management; pipeline triggers and run monitoring; extraction analytics |

## 1.5 What DOX deliberately does **not** do

These are product decisions, currently enforced in code, that are relevant to understanding its risk posture:

- **No bulk data export.** The CSV export feature described in the original PRD is **disabled** (`DATA_EXPORT_ENABLED = false` in `frontend/lib/feature-flags.ts`); the `/export` route redirects to the dashboard. Users consume the data inside the application only.
- **No bulk email.** Outreach is one email at a time (optionally with Cc/Bcc), composed or reviewed by the user, sent from the user's own mailbox under that user's OAuth consent. There is no campaign engine, no mail-merge blast, no scheduling, and no third-party bulk SMTP.
- **No anonymous access.** Every page except the landing/login pages requires an authenticated session; new accounts are inert until an administrator approves them.
- **No consumer data.** The dataset is firms and the business roles of people at those firms (officers, executives, registered persons, Form 4 insiders). The system does not ingest consumer/household data.
- **No scraping of logged-in or paywalled sources.** Public-web collection is limited to firms' own public websites and search-engine results obtained through a licensed API (SerpAPI). LinkedIn pages are **not** crawled; LinkedIn profile URLs are obtained from Google search results or from licensed data providers.

## 1.6 Current deployment status

Two environments exist (detailed in [Document 10](10-operations-and-environments.md)): a **staging** environment used for client demos and day-to-day validation, and a **production** environment. Certain demo conveniences are currently active and documented in code with revert notes (e.g., enrichment cooldowns removed so demo buttons are always clickable); these are flagged in [Document 11](11-legal-considerations-for-counsel.md) so they are not mistaken for the long-term posture.

---

[← Index](README.md) | [Next: System Architecture →](02-system-architecture.md)
