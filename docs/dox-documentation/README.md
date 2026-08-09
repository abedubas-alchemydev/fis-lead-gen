# DOX — Complete Product, Data & Security Documentation

**Prepared:** June 12, 2026
**Codebase reference:** branch `develop`, commit `d280012`
**Audience:** Legal counsel, management, and new team members
**Prepared by:** Engineering (generated from direct codebase review; every section cites the source files it describes)

---

## Purpose and scope

This documentation set describes, factually and comprehensively, what the DOX application is, how it works, where every piece of its data comes from, what personal data it handles, how it secures that data, and how its outbound-email and AI features operate. It was prepared specifically so that counsel can assess the product's legal posture from an accurate technical record rather than from marketing material.

Two framing notes for the reader:

1. **This is a factual technical record, not legal advice.** Where a topic has obvious legal relevance (data licensing, PII, email compliance, AI data flows), the documents state precisely what the system does and does not do, and [Document 11](11-legal-considerations-for-counsel.md) collects the open questions we believe deserve counsel's attention. No document here draws legal conclusions.
2. **Everything here is verifiable.** Claims are tied to file paths in the repository (e.g., `backend/app/services/outreach_send.py`). Counts and data volumes are stated as of the preparation date and grow over time (the system ingests new SEC/FINRA filings daily).

## Naming

The product is **DOX**. "Alchemy" / "alchemydev" is the name of the development organization and its GitHub/GCP accounts, not the product. A small number of internal strings still carry legacy branding (e.g., the AI assistant's system prompt and the SEC User-Agent string say "Alchemy"); these are flagged where they occur.

## Document map

| # | Document | What it covers |
|---|----------|----------------|
| 01 | [Product Overview](01-product-overview.md) | What DOX is, who it serves, the business problem, feature summary |
| 02 | [System Architecture](02-system-architecture.md) | Components, technology stack, data flow, environments |
| 03 | [Data Sources & Provenance](03-data-sources-and-provenance.md) | Every data source — government and commercial — how data is obtained, at what rate, and where it is stored |
| 04 | [Third-Party Services Inventory](04-third-party-services.md) | Every external service the system calls, what data is sent to and received from each |
| 05 | [Personal Data & Privacy](05-personal-data-and-privacy.md) | Complete PII inventory, data lifecycle, retention, and controls |
| 06 | [Outreach & Email Compliance](06-outreach-and-email-compliance.md) | How emails are composed and sent, the audit trail, and the current compliance posture (including gaps) |
| 07 | [AI Features & LLM Data Flows](07-ai-features-and-llm-data-flows.md) | The Doxie assistant, document-extraction pipelines, embeddings, and exactly what data reaches Google's Gemini API |
| 08 | [Security](08-security.md) | Authentication, authorization, session control, frontend hardening, audit logging, secrets, infrastructure |
| 09 | [User Guide](09-user-guide.md) | Feature-by-feature guide to using the application |
| 10 | [Operations & Environments](10-operations-and-environments.md) | Staging vs. production, CI/CD, scheduled jobs, database administration, backups |
| 11 | [Legal Considerations for Counsel](11-legal-considerations-for-counsel.md) | Consolidated list of facts and open questions with legal relevance |
| 12 | [Glossary](12-glossary.md) | Domain (securities-industry) and product terminology |
| 13 | [Third-Party Terms References](13-third-party-terms-references.md) | Direct links to every provider's governing terms (verified 2026-06-12), key clauses found, suggested review order |
| 14 | [Open-Source License Inventory](14-open-source-licenses.md) | Generated inventory of all production npm/pip dependencies and their licenses, copyleft review list |

## One-paragraph summary of the product

DOX is a web application that aggregates **publicly available regulatory data** about U.S. securities firms — broker-dealers and investment advisers — from SEC EDGAR, SEC IAPD, and FINRA BrokerCheck, enriches firm profiles with financial metrics extracted from regulatory filings, classifies each firm's securities-clearing arrangement, scores firms as sales prospects for clearing-services providers, optionally augments firm contact information through licensed commercial data providers (Apollo.io, People Data Labs, Hunter.io, Snov.io), and lets its small number of authorized users research those firms and send individual, user-reviewed business-development emails from the users' own linked mailboxes. An embedded AI assistant ("Doxie", powered by Google's Gemini API) answers questions over the same data. Data export is disabled; the product is operated as a closed research tool.
