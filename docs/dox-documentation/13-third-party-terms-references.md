# 13 — Third-Party Terms: Reference Links for Counsel

[← Glossary](12-glossary.md) | [Index](README.md) | [Next: Open-Source Licenses →](14-open-source-licenses.md)

---

This document gives counsel direct pointers to the governing documents of every external service DOX uses ([Doc 04](04-third-party-services.md) describes the technical integrations; [Doc 03](03-data-sources-and-provenance.md) the collection mechanics). Each URL below was checked on **2026-06-12**; where a site refuses automated retrieval, that is stated rather than substituting an unverified link. Quoted language is from the provider's page as retrieved that day — counsel should rely on the live documents, which change over time.

## 13.1 Government / regulator sources

### SEC (EDGAR, EFTS, IAPD)
- **Accessing EDGAR data (fair-access guidance):** `https://www.sec.gov/os/accessing-edgar-data` — the page the system's 10 req/s limit and declared User-Agent practice follow. *(SEC serves this to browsers; it returned 403 to our document fetcher — open in a browser.)*
- **EDGAR dissemination / privacy & security policy:** `https://www.sec.gov/privacy`
- EDGAR filings are U.S. public records; the practical obligations are the access-rate and identification requirements above, which the system implements ([Doc 03](03-data-sources-and-provenance.md) §3.1).

### FINRA (BrokerCheck) — **priority review item**
- **BrokerCheck Terms of Use:** `https://brokercheck.finra.org/terms`
- **FINRA "Permitted Uses" of BrokerCheck data:** `https://www.finra.org/investors/investing/working-with-investment-professional/about-brokercheck/permitted-uses` *(retrieved 2026-06-12)* — permits personal/professional research, judicial/arbitral use, regulatory compliance, and states that for "investor protection, academic, compliance or regulatory purposes, you may copy and compile BrokerCheck data, including by using data mining or similar tools" that "do not interfere with the proper working of BrokerCheck." Stated restrictions include: "You may not alter or modify the factual content of the BrokerCheck data" and **"You may not use BrokerCheck data for unsolicited marketing of goods or services."**
- **Why this is the priority:** DOX compiles BrokerCheck-derived firm data and its users send unsolicited business-development emails informed by that data. Whether DOX's use fits the permitted categories — and how the unsolicited-marketing restriction applies to outreach that is *informed by* (though not mailed to addresses *from*) BrokerCheck data — is squarely a counsel question. The PRD contemplated a commercial FINRA API agreement (~$1,650/mo) as an alternative footing. See [Doc 11](11-legal-considerations-for-counsel.md) §11.3.

### DTCC / OCC (clearing-agency directories)
- **DTCC Terms of Use:** `https://www.dtcc.com/terms-of-use` *(site returns 403 to automated fetchers — consistent with [Doc 03](03-data-sources-and-provenance.md) §3.5; open in a browser)*
- **OCC Terms of Use:** `https://www.theocc.com/terms-of-use` *(same — the OCC site blocks scripted access)*
- Context: membership directories were captured manually and are refreshed manually; counsel should review each site's content-reuse language against that practice.

## 13.2 Commercial data providers

### Apollo.io (Zenleads Inc.)
- **Terms of Service:** `https://www.apollo.io/terms-of-service` *(verified 2026-06-12; "last updated February 5, 2026")*
- **Privacy policy:** `https://www.apollo.io/privacy-policy`
- Review focus: license scope over delivered data (storage, retention after termination, permitted use for email outreach), webhook/PII handling, seat vs. credit terms.

### People Data Labs
- **Legal hub (Privacy Center):** `https://privacy.peopledatalabs.com/`
  - Services Subscription Agreement: `https://privacy.peopledatalabs.com/policies?name=services-subscription-agreement`
  - **Acceptable Data Use Policy:** `https://privacy.peopledatalabs.com/policies?name=acceptable-data-use-policy`
  - Privacy Policy: `https://privacy.peopledatalabs.com/policies?name=privacy-policy`
- Review focus: PDL's acceptable-use policy is the key document — it governs what enriched person data may be used for (including outreach) and data-retention/deletion duties on subscription end.

### Hunter.io
- **Terms of Service:** `https://hunter.io/terms-of-service` *(verified 2026-06-12)*
- Privacy: `https://hunter.io/privacy-policy`
- Review focus: API usage restrictions and any obligations attached to discovered-email data.

### Snov.io
- **Terms and Conditions:** `https://snov.io/t_and_c` *(verified 2026-06-12)*
- Privacy: `https://snov.io/privacy-policy`

### SerpApi
- **Legal hub (ToS, privacy, SLA):** `https://serpapi.com/legal` *(verified 2026-06-12)* — notably includes SerpApi's "U.S. Legal Shield" position on lawful scraping of search results; counsel may want a view on how much weight to give that.

## 13.3 AI / cloud platforms

### Google Gemini API
- **Gemini API Additional Terms of Service:** `https://ai.google.dev/gemini-api/terms` *(verified 2026-06-12)* — the page distinguishes tiers: for **paid services** "Google doesn't use your prompts…or responses to improve our products," while unpaid-tier content may be used for product improvement including machine learning. DOX uses a billed API key; confirming the account is on the paid tier (and that this term satisfies the client contract) is the action item ([Doc 11](11-legal-considerations-for-counsel.md) §11.7).

### Google (OAuth / Gmail sending)
- **API Services User Data Policy (incl. Limited Use):** `https://developers.google.com/terms/api-services-user-data-policy` *(verified 2026-06-12)* — requires limiting use of Google user data to user-facing features and prohibits transfer to data brokers/advertisers. The `gmail.send` scope is a **restricted scope**; the OAuth client's verification/assessment status should be confirmed.
- Google APIs Terms of Service: `https://developers.google.com/terms`

### Microsoft (Graph sendMail)
- **Microsoft APIs Terms of Use:** `https://learn.microsoft.com/en-us/legal/microsoft-apis/terms-of-use` *(verified 2026-06-12; "Last Updated: October 2025")* — notable clauses for our usage: data minimization (request no more than needed), no scraping/database-building beyond the intended scenario, **no use or transfer of API data for advertising/marketing purposes**, no resale/redistribution, and §5 privacy obligations (consents, retention/deletion, a privacy statement "as protective as the Microsoft Privacy Statement"). DOX requests only `Mail.Send` and stores no Graph-sourced data beyond its own send audit — but the privacy-statement requirement intersects with the no-in-app-privacy-policy gap ([Doc 11](11-legal-considerations-for-counsel.md) §11.5).

### Yahoo (mail OAuth/SMTP)
- **Yahoo Developer API Terms of Use:** `https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html`
- Yahoo Developer Network guidelines: `https://legal.yahoo.com/us/en/yahoo/guidelines/ydn/index.html`; sender/OAuth documentation: `https://senders.yahooinc.com/developer/`
- Search-result summaries of these terms include a rule that user data from Yahoo APIs not be stored beyond 24 hours except where indefinite storage is explicitly permitted; DOX stores Yahoo OAuth tokens (to send on the user's behalf) and its own sent-mail audit. Whether token storage falls inside the permitted exception is a specific question for counsel.

### Infrastructure processors
- **Google Cloud Platform terms:** `https://cloud.google.com/terms` (Cloud Run, GCS, Secret Manager, Scheduler)
- **Neon (PostgreSQL host):** `https://neon.com/terms-of-service` *(verified 2026-06-12 — now presented as a Databricks "Neon Platform Services Product Specific Schedule"; Neon was acquired by Databricks, so the governing paper is the Databricks MCSA + this schedule)*; DPA available via the same site.
- **GitHub:** `https://docs.github.com/en/site-policy/github-terms/github-terms-of-service` (code hosting, Actions CI).

## 13.4 Open-web / OSINT sources

- **theHarvester** (local OSINT tool): repository and license — `https://github.com/laramies/theHarvester` (the tool itself is open source; the passive sources it queries — crt.sh certificate-transparency logs, RapidDNS, AlienVault OTX, DuckDuckGo — are public datasets with their own terms).
- Firms' own websites: fetched directly at low volume for literally published contact details ([Doc 03](03-data-sources-and-provenance.md) §3.6); no specific terms reviewed per-site — counsel may wish a general position on this practice.

## 13.5 Suggested review order

1. **FINRA BrokerCheck terms** (§13.1) — permitted-use fit and the unsolicited-marketing restriction.
2. **PDL Acceptable Data Use Policy and Apollo ToS** (§13.2) — outreach-use and retention clauses for enriched contact data.
3. **Google User Data Policy + restricted-scope verification; Microsoft API ToU §5; Yahoo 24-hour rule** (§13.3) — the three mailbox integrations.
4. **Gemini paid-tier confirmation** (§13.3).
5. DTCC/OCC content-reuse language (§13.1) and SerpApi posture (§13.2).

---

[← Glossary](12-glossary.md) | [Index](README.md) | [Next: Open-Source Licenses →](14-open-source-licenses.md)
