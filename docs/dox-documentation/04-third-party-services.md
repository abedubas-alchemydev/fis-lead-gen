# 04 — Third-Party Services Inventory

[← Data Sources](03-data-sources-and-provenance.md) | [Index](README.md) | [Next: Personal Data & Privacy →](05-personal-data-and-privacy.md)

---

This document inventories **every external service the system communicates with**, what data crosses the boundary in each direction, and how each integration is keyed and configured. It is the reference for reviewing provider terms of service. (Government sources — SEC, FINRA — are covered in [Document 03](03-data-sources-and-provenance.md) and not repeated here.)

## 4.1 Summary table

| Service | Category | Purpose in DOX | Data we send (highlights) | Data we receive (highlights) | Credentials (env names) |
|---|---|---|---|---|---|
| **Apollo.io** | B2B contact data | Contact enrichment (person match by email or name+firm), org lookup, website resolution, async phone reveal | Person names, business email addresses, firm names/domains, CRD in queries; webhook URL | Names, titles, business emails, phone numbers, LinkedIn URLs, org websites, Apollo person IDs, email-status confidence | `APOLLO_API_KEY`, `APOLLO_WEBHOOK_SECRET`, `PUBLIC_BASE_URL` |
| **People Data Labs** | B2B contact data | Reverse-email person enrichment | Business email addresses | Names, titles, employers, LinkedIn URLs, alternate emails, phones (with likelihood score) | `PDL_API_KEY`, `PDL_MIN_LIKELIHOOD` |
| **Hunter.io** | Email discovery | Domain email search; reverse-email person lookup | Domains; business email addresses | Published email addresses; names/titles/LinkedIn for a given email | `HUNTER_API_KEY`, `HUNTER_LIMIT` |
| **Snov.io** | Email discovery | Domain email search; name→email search; reverse-email profile | Domains; person first/last names; email addresses | Email addresses; names, titles, employers, LinkedIn | `SNOV_CLIENT_ID`, `SNOV_CLIENT_SECRET` |
| **SerpAPI** | Licensed search-results API | Google results for website resolution, LinkedIn-profile discovery, Doxie web research | Search queries containing firm names and sometimes person names (e.g., `"Jane Smith" Acme site:linkedin.com/in`) | Organic search results, knowledge-graph snippets | `SERPAPI_API_KEY` |
| **Google Gemini API** | LLM / embeddings | Regulatory-PDF extraction, clearing classification, Doxie chat, outreach drafting, embeddings | Filing PDFs (public documents), firm text, chat messages, tool results (may include contact PII), Vault document text | Structured extractions with confidence, chat replies, drafts, 768-dim vectors | `GEMINI_API_KEY` (+ model/timeout settings) |
| **OpenAI API** | LLM (optional alternative) | Same extraction roles if `LLM_PROVIDER=openai` (not the active default) | As above when enabled | As above | `OPENAI_API_KEY` |
| **Anthropic API** | LLM (config slot only) | `ANTHROPIC_API_KEY` setting exists; no active call path in the current default configuration | — | — | `ANTHROPIC_API_KEY` |
| **Google (Gmail API)** | Email send (user-linked) | Outreach sends from a user's own Gmail | The user's outgoing email (recipients, subject, body) under the user's OAuth token | Message ID confirmation | `GOOGLE_CLIENT_ID`/`SECRET` (OAuth app) |
| **Microsoft Graph** | Email send (user-linked) | Outreach sends from a user's own Outlook/Microsoft mailbox | Same as above | Send confirmation | `MICROSOFT_CLIENT_ID`/`SECRET` |
| **Yahoo Mail (SMTP XOAUTH2)** | Email send (user-linked) | Outreach sends from a user's own Yahoo mailbox | Same as above | SMTP acceptance | `YAHOO_CLIENT_ID`/`SECRET` |
| **SMTP (Gmail app-password)** | Transactional email | System emails only: signup verification, password reset, admin-approval requests, new-sign-in alerts | Recipient address and the transactional message | — | `SMTP_HOST/PORT/USER/PASSWORD`, `EMAIL_FROM` (frontend env) |
| **Google Cloud Storage** | Object storage | Vault file bytes; 5-minute signed download URLs | User-uploaded documents | — | `VAULT_STORAGE_BUCKET` (service identity) |
| **theHarvester** (local tool, passive sources) | OSINT | Email-extractor domain scans via `crtsh`, `rapiddns`, `otx`, `duckduckgo` | Domain names (to those public sources) | Published email addresses | `THEHARVESTER_SOURCES` |
| **ZoomInfo** | B2B contact data | **Config slot only** (`ZOOMINFO_API_KEY` exists in settings); no active integration in the codebase | — | — | `ZOOMINFO_API_KEY` |

Notes: all provider keys live in GCP Secret Manager and are injected at deploy time (see [Doc 08](08-security.md) §8.5). Every provider is optional in code — an unset key disables that provider and the chains skip it.

## 4.2 Apollo.io (the primary contact provider)

**Endpoints used** (`backend/app/services/contact_discovery/apollo_match.py`, `email_extractor/enrichment/apollo.py`, `website_resolver.py`):
- `POST /v1/people/match` — person enrichment. Two input shapes: reverse-email (the discovered email) or forward (first/last name + organization + optional domain). `reveal_personal_emails: true` is set; when phone reveal is configured, `reveal_phone_number: true` plus a `webhook_url` is sent.
- `POST /api/v1/organizations/enrich` and organization search — firm-level lookups (phone, LinkedIn, website) used for corroboration and website resolution (alias-aware, capped at 4 queries per firm).

**Asynchronous phone reveal:** Apollo returns phone numbers via callback rather than synchronously. DOX registers `{PUBLIC_BASE_URL}/api/v1/webhooks/apollo/{APOLLO_WEBHOOK_SECRET}/phone-reveal`; the handler (`endpoints/webhooks_apollo.py`) does a constant-time comparison of the path secret and matches the payload to contacts by `apollo_person_id` (returns 404 on a bad secret so the endpoint is not enumerable). Because thin matches can yield non-resolvable ("ephemeral") person IDs, some reveals never correlate — a known limitation.

**Confidence model:** Apollo's `email_status` maps to an internal confidence (verified→90, likely→75, unverified→60, guessed→45); a floor (`CONTACT_DISCOVERY_MIN_CONFIDENCE`, default 60) gates what is stored. A "director LinkedIn fallback" re-query accepts a LinkedIn URL only when Apollo's own person ID matches the original hit (prevents wrong-person grafts).

**Metering caveats:** lead-credit consumption is visible only in Apollo's dashboard (their API does not report it), and exhausted credits surface as HTTP 422. Provider attribution in our own DB (`discovery_source='apollo_match'`, etc.) is the working usage measure, surfaced on `/settings/extractions`.

## 4.3 People Data Labs

`POST /v5/person/enrich` with the email address and a server-side `min_likelihood` floor (default 6) so only confident matches are returned/billed. Receives full name, title, employer, LinkedIn, alternate personal/work emails, mobile/other phones. A documented 404 is treated as a clean no-match. PDL sits second in the email-enrichment chain because it is the one paid API that reliably returns person-level phones and multiple emails.

## 4.4 Hunter.io and Snov.io

- **Hunter:** `GET /v2/people/find` (reverse email) and domain search for the Email Extractor. Receives names/titles/LinkedIn/alternate emails.
- **Snov:** OAuth client-credentials token endpoint, then `POST /v1/get-profile-by-email` (reverse email; returns name/title/company/LinkedIn, no phone) and `get-emails-from-names` (async, polled) for name→email discovery.

## 4.5 SerpAPI

A licensed API that returns Google search results. Call sites: website resolution (firm-name queries), LinkedIn profile discovery (`"{first} {last}" {firm} site:linkedin.com/in` — results are parsed for `/in/` URLs with org-confirmation logic; ambiguous same-name results are rejected rather than guessed), and Doxie's `research_term` web research. **Person names appear in queries** for the LinkedIn case; this is search-engine querying, not crawling of LinkedIn itself.

## 4.6 Google Gemini API

Three distinct roles (full AI detail in [Document 07](07-ai-features-and-llm-data-flows.md)):

1. **Structured extraction** (`gemini-2.5-flash` by default; Files API for PDFs 20–45 MB): financial figures, clearing data and classification, firm aliases, adviser profiles, FOCUS contact blocks, filing summaries. Inputs are public regulatory PDFs and firm text; temperature 0.1; outputs are schema-validated JSON with confidence.
2. **Doxie chat & outreach drafting** (`gemini-2.5-flash`): conversation messages, page context, and tool results — which can include firm contacts' names/emails/phones — are transmitted to generate replies and drafts.
3. **Embeddings** (`gemini-embedding-001`): firm summary strings and Vault document chunks are embedded into 768-dimension vectors stored in pgvector.

The Gemini API's data-handling terms (training-use, retention) should be confirmed against Google's current paid-API terms — flagged in [Doc 11](11-legal-considerations-for-counsel.md) §7.

## 4.7 User-linked email providers (outreach transport)

Outreach emails are sent **as the user, from the user's own mailbox**, under OAuth scopes the user grants explicitly and incrementally (the send scope is requested only when the user first tries to send, not at login):

| Provider | Scope | Send mechanism |
|---|---|---|
| Google | `gmail.send` | Gmail API `users/me/messages/send` (RFC 822) |
| Microsoft | `Mail.Send` | Graph `POST /v1.0/me/sendMail` |
| Yahoo | `mail-w` | SMTP `smtp.mail.yahoo.com:587` + STARTTLS + XOAUTH2 |

Missing link/scope produces HTTP 412 with a re-consent prompt; transient Gmail failures retry (max 2, backoff ≤8s). There is no system-owned bulk sending identity. Tokens are stored in the `account` table (see [Doc 08](08-security.md) §8.6 regarding at-rest handling).

## 4.8 Failure and error philosophy

Provider calls are wrapped so that one provider's failure never blocks the chain: discovery providers return null on error; enrichment steps raise a typed `EnrichmentError` that marks the row retryable; user-facing errors are provider-agnostic. Timeouts are short (10–12s discovery; 120s LLM) and retries bounded.

---

[← Data Sources](03-data-sources-and-provenance.md) | [Index](README.md) | [Next: Personal Data & Privacy →](05-personal-data-and-privacy.md)
