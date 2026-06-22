# 11 — Legal Considerations for Counsel

[← Operations](10-operations-and-environments.md) | [Index](README.md) | [Next: Glossary →](12-glossary.md)

---

> **Framing.** This document is written by engineering, not lawyers. It consolidates — in one place, with citations into the rest of this documentation set — the facts about the system that we believe carry legal significance, and the open questions we think deserve counsel's judgment. It intentionally includes the unflattering items. Nothing here is a legal conclusion.

## 11.1 Corporate / commercial context

- Product: **DOX**, developed and operated by the Alchemy development organization for a clearing-services client (the PRD names FIS as the commissioning context; the architecture was specified as repackageable for other clearing-industry customers). Counsel may want the development/IP/licensing agreement between the parties reflected against what's documented here (who owns the dataset, the code, the enriched contact records).
- User base is intentionally tiny (1–3 enterprise users per client) and contractual; there is no self-service public signup beyond the admin-approved gate.

## 11.2 Government-data usage (SEC)

**Facts:** All SEC collection ([Doc 03](03-data-sources-and-provenance.md) §§3.1–3.3) uses the SEC's published public endpoints, identifies itself with a declared User-Agent containing a contact email per SEC automated-access guidance, throttles to the published 10 req/s fair-access ceiling, honors `Retry-After`, and caches aggressively. EDGAR/IAPD content is U.S.-government public record.
**Questions for counsel:** none specific — this is the strongest-footing source. One cosmetic item: the User-Agent string still reads "Alchemy Dev compliance@alchemy.dev"; if an entity change is relevant, update the declared identity (single env var).

## 11.3 FINRA data usage

**Facts:** DOX queries the same unauthenticated JSON search API and public firm-report PDFs that power FINRA's public BrokerCheck site, at deliberately low rates (2 req/s + 0.5s delay; nightly discovery bounded to ~500 probes), without circumventing any authentication. Because the endpoint sits behind a CDN that rejects non-browser clients, requests present ordinary browser-style headers. Extracted facts (registrations, officers, clearing language, disclosure counts) are stored with CRD-keyed provenance; the UI links users to FINRA's own PDF.
**Questions for counsel:** (a) FINRA's BrokerCheck terms of use and the legal weight of automated access to an otherwise-public, unauthenticated endpoint (including the browser-header practice); (b) whether FINRA data is subject to reuse restrictions and what posture to take. **Update (2026-06-12):** FINRA's published "Permitted Uses" page ([Doc 13](13-third-party-terms-references.md) §13.1) permits copying/compiling BrokerCheck data — including via data-mining tools that don't interfere with the service — for "investor protection, academic, compliance or regulatory purposes," but states "You may not use BrokerCheck data for unsolicited marketing of goods or services." How that restriction applies to a product whose users send unsolicited B2B outreach *informed by* BrokerCheck-derived intelligence is the single most pointed terms question in this document set; the commercial FINRA API agreement referenced in the PRD (~$1,650/mo) may be the cleaner footing.

## 11.4 Clearing-agency directories and other public sources

**Facts:** DTCC-family and OCC member directories are public web pages whose sites block automated fetching; the directories were captured **manually by a person in a browser** and committed as CSV data files with full provenance ([Doc 03](03-data-sources-and-provenance.md) §3.5). theHarvester uses passive public sources (certificate-transparency logs, DNS datasets, DuckDuckGo). Firms' own public websites are fetched page-wise (no spidering) for literally published contact details under conservative attribution rules; LinkedIn is **never crawled** — profile URLs come from licensed APIs or Google results via SerpAPI.
**Questions for counsel:** (a) acceptability of manual capture + internal reuse of the DTCC/OCC directory contents (public facts, but the sites assert terms); (b) any concern with name+firm queries to a search API returning LinkedIn URLs (we store the URL only, no profile content).

## 11.5 Personal data / privacy (third-party professionals)

**Facts** ([Doc 05](05-personal-data-and-privacy.md)): the system holds professional-capacity PII (names, titles, business emails/phones, LinkedIn URLs) for officers and insiders of U.S.-regulated firms, sourced from public filings, licensed B2B providers, and firms' own sites; Form 4 rows include the address **as publicly filed with the SEC**. PII is disclosed to enrichment providers as queries and to Google's Gemini API inside assistant/drafting flows. There is **no privacy policy or ToU published in-app, no DSAR workflow, no suppression list, and no retention schedule**; data export is disabled and access is licensed-user-only.
**Questions for counsel:** (a) applicability of CCPA/state privacy laws (B2B professional data, smallness of the business, "publicly available information" exemptions) and GDPR (no EU targeting, but no geo-screen either); (b) whether a privacy policy/ToU should be added to the app and the customer contract; (c) whether a minimal deletion/suppression capability should be built proactively; (d) contractual flow-down: do the B2B providers' terms (Apollo, PDL, Hunter, Snov) impose obligations on stored/derived data, retention after subscription end, or restrictions on using their data to send email?

## 11.6 Outbound email (CAN-SPAM and provider policy)

**Facts** ([Doc 06](06-outreach-and-email-compliance.md)): one-at-a-time, human-reviewed B2B emails sent from the **user's own mailbox** under user-granted OAuth scopes; full content/recipient audit retained; **no system-injected opt-out link, no suppression list, no send caps, no postal-address footer** — content compliance rests with the sending user; a separate transactional path covers account emails. Email Extractor verification performs SMTP RCPT probes without sending messages.
**Questions for counsel:** (a) CAN-SPAM allocation — confirm the "user is the sender; tool is a CRM" theory and whether the customer contract should say so explicitly; (b) whether to add opt-out capture + suppression as a product control even if not strictly required at this volume; (c) Google/Microsoft/Yahoo developer-policy compliance for the OAuth apps (Gmail send scope is "restricted" — verification/assessment status of the OAuth client should be confirmed); (d) any view on SMTP verification probes (common industry practice, but worth a position).

## 11.7 AI / LLM data flows

**Facts** ([Doc 07](07-ai-features-and-llm-data-flows.md)): Google Gemini receives public filing PDFs, firm text, user chat, **tool results that can include contact PII**, and user-uploaded Vault document text; embeddings of firm summaries and Vault chunks are computed by the same API. Human-in-the-loop confirmation gates the only outward AI action (email send). Extractions are confidence-gated with human review.
**Questions for counsel:** (a) confirm Google's current paid Gemini API data-use terms (training/retention commitments) and whether they satisfy the customer contract; (b) whether customer-facing disclosure of AI processing (and of PII flow to Google) is warranted; (c) AI-generated outreach content sits inside the user-review flow — confirm comfort that authorship/responsibility allocation follows the sending user.

## 11.8 AI action risk

**Fact:** the backend requires `confirm=true` on the assistant's send tool, but the *judgment* that the user actually confirmed lives in the model's instructions; a misbehaving model could in principle set the flag without genuine confirmation. The UI's draft card keeps the human in control in practice, and every send is audited.
**Question:** whether to harden this to a structural guarantee (e.g., send executable only from a UI action or a user-minted confirmation token), as a matter of risk preference.

## 11.9 Known internal inconsistencies & housekeeping (flagged so they don't surprise anyone)

1. **Legacy "Alchemy" branding** persists in the Doxie system prompt and the SEC User-Agent; product is DOX.
2. **PRD drift:** the PRD specifies OpenAI/Claude for extraction and a restricted CSV export; the implementation uses **Gemini** and **export is disabled entirely**. The PRD also describes export watermark/caps that therefore don't exist in the product today.
3. **Doxie tool calls are not persisted** (only final replies), limiting after-the-fact AI auditability; sends are independently audited.
4. **Demo-mode relaxations** are live on staging with in-code revert notes (enrichment cooldowns removed; always-enabled buttons on `/investors`); they are not the production posture and should be reverted before any compliance-sensitive demo of "production behavior."
5. **Plaintext OAuth tokens at the application layer** and the **dev-fallback auth secret** are the two notable at-rest/security-debt items ([Doc 08](08-security.md) §8.9).
6. **No retention policies** on audit/activity logs and backups ([Doc 05](05-personal-data-and-privacy.md) §5.6).
7. A **ZoomInfo** key slot and an **Anthropic** key slot exist in configuration with no active integration — listed so the configuration surface matches expectations.

## 11.10 What we'd suggest counsel review first

In rough order of likely materiality: (1) FINRA/BrokerCheck usage posture (§11.3); (2) provider ToS flow-down for Apollo/PDL/Hunter/Snov/SerpAPI, including email-use and retention clauses (§11.5d); (3) CAN-SPAM allocation + OAuth app policy status (§11.6); (4) privacy-policy/DSAR/suppression decisions (§11.5); (5) Gemini API terms and AI disclosure (§11.7).

---

[← Operations](10-operations-and-environments.md) | [Index](README.md) | [Next: Glossary →](12-glossary.md)
