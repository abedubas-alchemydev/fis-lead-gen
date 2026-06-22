# 12 — Glossary

[← Legal Considerations](11-legal-considerations-for-counsel.md) | [Index](README.md) | [Next: Third-Party Terms →](13-third-party-terms-references.md)

---

## Securities-industry terms (as used in DOX)

| Term | Meaning |
|---|---|
| **Broker-dealer (BD)** | A firm registered with the SEC (and typically FINRA) to buy and sell securities for customers or its own account. DOX's primary entity. |
| **Investment adviser (IA / RIA)** | A firm registered with the SEC under the Investment Advisers Act that advises clients on securities for compensation. Tracked from SEC IAPD data. |
| **Dual-registered firm** | A firm registered as both a BD and an IA (matched on shared CRD in DOX). |
| **CRD number** | Central Registration Depository identifier assigned by FINRA to firms and registered persons. DOX's primary firm key. |
| **CIK** | Central Index Key — the SEC EDGAR identifier for a filer. |
| **SEC file number** | The registration file number (e.g., 8-XXXXX for BDs). |
| **Form BD** | The broker-dealer registration form (and amendments). A new Form BD signals a new market entrant. |
| **Form X-17A-5 / FOCUS report** | The annual audited financial report broker-dealers must file (Financial and Operational Combined Uniform Single report). Source of net-capital figures and clearing-arrangement language. |
| **Form 17a-11** | Notification filed when a BD's net capital falls below required thresholds ("deficiency notice"). Drives Critical alerts and the Alternative list. |
| **Form ADV** | The investment-adviser registration form; source of IA profiles (AUM, activities, owners). |
| **Form 13F / 13F-HR** | Quarterly holdings report filed by institutional investment managers over $100M. "13F filer" marks an IA as an institutional manager. |
| **Form 4** | Statement of changes in beneficial ownership — insider transactions by officers/directors/10% owners. Powers the `/investors` feed. |
| **Net capital / excess net capital** | The SEC Rule 15c3-1 liquidity measure for BDs; excess = capital above the required minimum. Core health metrics in DOX. |
| **Clearing** | Post-trade settlement of securities transactions — the service the product's users sell. |
| **Self-clearing** | A firm that clears its own (and possibly others') trades and holds customer funds/securities. High-value prospect for clearing outsourcing. |
| **Fully disclosed (introducing)** | A firm that introduces its customers to a clearing firm, which carries the accounts. Its **clearing partner** is the incumbent provider/competitor. |
| **Omnibus** | A clearing arrangement where a firm clears through another on a combined (omnibus) account basis — in DOX's canonical definitions, a firm with multiple arrangements that also clears for other firms. |
| **Non-carrying** | A BD that does not carry customer accounts or funds. |
| **Clearing agency memberships** | Membership in OCC (Options Clearing Corporation), DTC (Depository Trust Company), NSCC (National Securities Clearing Corporation), FICC-GOV / FICC-MBS (Fixed Income Clearing Corporation divisions). Direct membership corroborates self-clearing. |
| **SEC EDGAR** | The SEC's public filing system (Electronic Data Gathering, Analysis, and Retrieval). |
| **EFTS** | EDGAR Full-Text Search system (efts.sec.gov). |
| **IAPD** | Investment Adviser Public Disclosure — the SEC's public IA database. |
| **FINRA BrokerCheck** | FINRA's public database of BD firms and registered persons. |
| **SRO** | Self-regulatory organization (e.g., FINRA). |
| **AUM / RAUM** | (Regulatory) assets under management, from Form ADV. |

## Product terms

| Term | Meaning |
|---|---|
| **DOX** | The product documented here. ("Alchemy" = the development organization; legacy strings only.) |
| **Doxie** | The in-app AI assistant (Gemini-powered, 42 tools). |
| **Broker Dealers list** | The main firm table at `/master-list` (formerly "Master List" — old name retired from the UI). |
| **Primary / Alternative list** | Healthy firms vs. firms with a 17a-11 deficiency filing (a distinct sales strategy). |
| **High-Value Participant (HVP)** | Segment: net capital between $5M and $100M, or OTC corporate-equity retail business. |
| **Lead score / priority** | Weighted 0–100 score (net-capital growth, clearing arrangement, financial health, registration recency — admin-tunable) bucketed Hot / Warm / Cold. |
| **Competitor provider** | A clearing provider on the configurable competitor list; firms clearing through one are takeaway targets. |
| **Enrichment** | Filling a contact's email/phone/LinkedIn via the provider chain (Apollo → PDL → Hunter → Snov → name-lookup → web fallback), gap-fill only, with per-field provenance. |
| **Contact discovery chain** | The forward search (name + firm → channels): `apollo_match`, `hunter`, `snov` in parallel with a confidence floor. |
| **Email Extractor** | The domain-scan feature discovering published addresses (Hunter/Snov/site/OSINT) with optional SMTP verification. |
| **Re-enrich** | Retry enrichment on a "Not found" row. |
| **One-off** | A free-form outreach recipient (typed email, not a stored contact). House style: never "ad-hoc" in UI copy. |
| **Vault** | Per-user folders of service documents that ground AI outreach drafts (RAG). |
| **Draft card** | The interactive in-chat email draft Doxie produces; sending requires explicit confirmation. |
| **Gap-fill** | Pipelines/enrichment that only fill missing fields, never overwrite existing data. |
| **Pipeline run** | A tracked execution of any ingestion/enrichment job (`pipeline_run` table; statuses queued/running/success/failure). |
| **Unknown reason** | The categorized explanation stored when a field can't be populated (firm_does_not_disclose, no_filing_available, low_confidence_extraction, pdf_unparseable, provider_error, not_yet_extracted). |
| **Watched firms** | Firms for which a new X-17A-5 filing auto-triggers financial extraction. |
| **Feature permissions** | Per-user feature keys (MASTER_LIST, VAULT, …) that gate pages, endpoints, and Doxie tools. |
| **Viewer / Admin** | The two roles; viewers are read-mostly and feature-gated, admins manage users, settings, and pipelines. |

## Technical terms

| Term | Meaning |
|---|---|
| **Better Auth** | The authentication framework used by the frontend (sessions, OAuth, hooks). |
| **Cloud Run (service / job)** | GCP serverless containers — services serve HTTP; jobs run batch scripts. |
| **Cloud Scheduler** | GCP cron service triggering the nightly job via OIDC-authenticated calls. |
| **Neon** | The managed PostgreSQL host (separate staging and production instances). |
| **pgvector** | Postgres extension storing embedding vectors for semantic search and Vault RAG. |
| **Embedding** | A numeric vector (768-dim here, `gemini-embedding-001`) representing text for similarity search. |
| **RAG** | Retrieval-augmented generation — retrieving relevant stored text (Vault chunks) into an AI prompt. |
| **SSE** | Server-Sent Events — the streaming transport for live alerts and Doxie chat. |
| **OIDC** | OpenID Connect — used for Google-signed service-to-service auth and OAuth sign-in. |
| **HMAC** | Keyed hash used to sign/verify session tokens. |
| **Alembic** | The database-migration tool (78 revisions). |
| **theHarvester** | Open-source OSINT tool used with passive sources for the Email Extractor. |
| **SerpAPI** | Licensed API returning Google search results. |
| **Files API (Gemini)** | Google's upload channel for large PDFs (20–45 MB) instead of inline payloads. |

---

[← Legal Considerations](11-legal-considerations-for-counsel.md) | [Index](README.md)
