# 05 — Personal Data & Privacy

[← Third-Party Services](04-third-party-services.md) | [Index](README.md) | [Next: Outreach & Email Compliance →](06-outreach-and-email-compliance.md)

---

This document is the complete inventory of **personal data** the system stores or transmits, organized by data-subject category, with lifecycle (collection → use → disclosure → retention) for each. "Personal data" is used here in the broad regulatory sense — any information relating to an identified or identifiable natural person — even where the data is strictly business-card-level information about people acting in professional capacities.

## 5.1 Two populations of data subjects

1. **Application users** (the customer's own staff, ~1–3 per client, plus administrators): account, session, activity, and linked-mailbox data.
2. **Industry professionals** (third parties): officers, directors, executives, and registered persons of broker-dealers and investment advisers; SEC Form 4 reporting persons (corporate insiders); and individuals whose business emails are discovered on public sources. The system holds **professional-capacity** information about these people; it has no relationship with them.

## 5.2 Inventory — application users

| Data | Where stored | Source | Purpose |
|---|---|---|---|
| Name, email, avatar URL, role, status, per-feature permissions | `user` | Signup / admin | Identity, access control |
| Hashed password (credentialed accounts) | `account` (Better Auth) | User | Login. Hashing handled by Better Auth |
| Session token, expiry, **IP address**, **user-agent**, last activity | `session` | Login | Session validity, "online" display, security alerts |
| OAuth access/refresh/id tokens, granted scopes, linked mailbox address | `account` | User's OAuth consent | Sending outreach from the user's mailbox. **Stored plaintext at the application layer** (see [Doc 08](08-security.md) §8.6) |
| Login/logout/security events (with IP, user-agent, session id) | `audit_log` | System | Security audit (see §5.7) |
| Navigation, searches, feature usage | `user_activity` | System | Admin activity review (`/settings/users/{id}/activities`) |
| Firm pages visited | `user_visit` | System | "Visited firms" convenience feature |
| Outreach signature | `user_outreach_settings` | User | Appended to outgoing emails |
| Vault folders and uploaded documents (+ extracted text and embeddings) | `vault_folder`, `vault_folder_file` (bytes in GCS), `vault_folder_chunk` | User upload | Grounding AI outreach drafts; content is whatever the user uploads — could itself contain personal data |
| Sent emails: subject, full body, To/Cc/Bcc, sender address, status | `outreach_send` | User action | Compliance/audit record of every send (soft-deleted, never physically purged from UI deletion) |
| Drafts (recipients, subject, body) | `outreach_draft` | User / Doxie | Saved unsent emails |
| Doxie conversations (user + assistant messages, page context) | `chatbot_conversation`, `chatbot_message` | User | Assistant history (last 50 conversations browsable) |

## 5.3 Inventory — industry professionals (third-party data subjects)

| Data | Where stored | Source(s) |
|---|---|---|
| Officer/director/executive names, titles, ownership % | `broker_dealers.direct_owners`/`executive_officers` (JSONB), `executive_contacts`, `advisor_contacts`, `investor_contacts` | SEC and FINRA filings (public regulatory disclosures) |
| Business email addresses | contact tables, `discovered_email` | Commercial providers (Apollo, PDL, Hunter, Snov), firm websites (literal published addresses only), OSINT (certificate logs, etc.), FOCUS-report contact blocks |
| Business/mobile phone numbers | contact tables (`phone`, `phones` JSONB), `discovered_email.enriched_phone` | Apollo (async reveal), PDL, FOCUS-report contact blocks, firm websites (name-proximity rule) |
| LinkedIn profile URLs | contact tables | Apollo, PDL, Hunter, Snov, Google search results via SerpAPI |
| Form 4 reporting persons: name, CIK, role flags (director/officer/10% owner), **address as filed**, transaction details; optional enriched email/phone/LinkedIn | `form4_transactions`, `reporting_owner` | SEC EDGAR (public filings); enrichment on user request |
| Provenance and confidence per contact field | `discovery_source`, `discovery_confidence`, `apollo_person_id`, `enriched_at` | System |

**Collection principles currently enforced in code:**
- Web-sourced emails/phones must be *literally published* on the firm's own site and attributable to the person by name pattern or proximity; generic inboxes never attach to a person; nothing is guessed or synthesized (`web_fallback.py` policy).
- Provider results below confidence floors are discarded.
- Enrichment is **gap-fill**: existing values are not overwritten by later, lower-quality sources, and re-enrichment never erases previously found channels (non-destructive merge, `enrichment/orchestrator.py`).
- If no real email exists for a person, the UI shows none (no placeholder generation) and outreach to that person is blocked server-side (`recipient_no_email`).

## 5.4 Disclosures of personal data to processors/third parties

| Recipient | What personal data | Trigger |
|---|---|---|
| Apollo / PDL / Hunter / Snov | A professional's name and/or business email, with firm name/domain, **as the query** | User-initiated or scheduled enrichment of that contact |
| SerpAPI (Google results) | Person name + firm name in a search query (LinkedIn discovery) | Enrichment fallback |
| Google Gemini API | Chat content; tool results that can include contact names/emails/phones and Form 4 insider details; Vault document text; user-typed messages | Doxie usage; outreach draft generation; embeddings |
| Google (Gmail API) / Microsoft Graph / Yahoo | The outgoing email itself (recipient addresses, subject, body, signature) | User sends outreach |
| SMTP relay (transactional) | User's own email address; verification/reset/approval content | Account lifecycle events |
| Mail servers of scanned domains | Candidate addresses during SMTP verification (RCPT probe; no message sent) | User runs Email Extractor verification |

No personal data is sold, syndicated, or exported in bulk; `DATA_EXPORT_ENABLED=false` removes even the restricted CSV export the PRD originally specified.

## 5.5 Cross-border and residency

All first-party storage (Neon PostgreSQL, GCS, Cloud Run) is provisioned in U.S. regions (`us-central1`). The commercial providers and Google APIs are U.S. companies; their own processing locations are governed by their terms. The data subjects are overwhelmingly U.S.-based securities-industry professionals; the system has no EU-targeting features. (GDPR/CCPA applicability is a counsel question — [Doc 11](11-legal-considerations-for-counsel.md) §5.)

## 5.6 Retention and deletion — current factual state

| Data | Current behavior |
|---|---|
| Firm and contact data | Retained indefinitely; refreshed/corrected by pipelines; no automated purge |
| `outreach_send` | Retained indefinitely by design (user "delete" sets `archived_at`; rows persist for audit) |
| `audit_log`, `user_activity` | No retention policy implemented; grows unbounded |
| Doxie conversations | Archived (soft) on "New chat"; no automated deletion |
| Vault files | User-deletable (file delete removes the GCS object and chunks); folder delete cascades |
| Users | Admin can deactivate/remove (removed-users tab retains an audit record); related rows cascade per FK rules |
| Backups | Operator-made logical dumps exist (`db-backups/`, see [Doc 10](10-operations-and-environments.md)); no documented rotation |

**There is no data-subject request (DSAR) tooling** — no self-service or admin workflow to locate/export/erase a third-party professional across all tables, and no suppression list preventing re-ingestion after a deletion. Both facts are flagged for counsel ([Doc 11](11-legal-considerations-for-counsel.md) §5).

## 5.7 Transparency and user-facing notices

- The application watermarks the UI with the viewing user's identity and blocks copy/print (a *data-protection* control aimed at the licensed dataset — [Doc 08](08-security.md) §8.4).
- Data provenance is shown in the UI (source badges, confidence tooltips, "unknown reason" explanations), so users can see that a contact field came from, e.g., Apollo vs. a FOCUS filing.
- **There is currently no published privacy policy or terms-of-use text inside the application**, and no notice mechanism toward the third-party professionals in the dataset. Flagged in [Doc 11](11-legal-considerations-for-counsel.md) §5.

## 5.8 Security of personal data

Covered in depth in [Document 08](08-security.md): authenticated access only, role + per-feature authorization, single-active-session, approval-gated signup, TLS in transit, secrets in GCP Secret Manager, audit logging, and the two known at-rest caveats (plaintext OAuth tokens at the application layer; managed-platform disk encryption relied upon for the database).

---

[← Third-Party Services](04-third-party-services.md) | [Index](README.md) | [Next: Outreach & Email Compliance →](06-outreach-and-email-compliance.md)
