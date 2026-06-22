# 09 — User Guide

[← Security](08-security.md) | [Index](README.md) | [Next: Operations & Environments →](10-operations-and-environments.md)

---

A feature-by-feature guide to using DOX, written for an end user. Access to each area depends on the permissions an administrator has granted to your account ([Doc 08](08-security.md) §8.2); if a section is missing from your sidebar, you don't have that feature enabled.

> **About the screenshots.** All screenshots were captured from the staging environment on 2026-06-12 using a temporary documentation account. The repeating faint text visible across every page ("Docs Screenshots · …") is the **identity watermark** — a security feature that stamps the viewing user's identity over the licensed dataset ([Doc 08](08-security.md) §8.4). Every user sees their own identity there.

![DOX landing page](images/01-landing.png)

## 9.1 Signing in

1. **Create an account** at `/signup` (email + password, or continue with Google / Yahoo — Outlook sign-in is marked "coming soon"). New accounts start as read-only *Viewers* and are **pending until an administrator approves them** — you will see a "pending approval" notice and receive access once activated.
2. **Sign in** at `/login`. DOX allows **one active session per account**: signing in on a second device signs the first one out (and emails you an alert with the new sign-in's details).
3. Use **Settings → My Account** to change your password (password accounts only — this also signs out other devices), and **Settings → Email Accounts** to link the mailbox you'll send outreach from.

![Sign-in screen](images/02-login.png)

## 9.2 Dashboard (`/dashboard`)

Your landing page: KPI cards (Total Active BDs, New BDs in 30 days, Deficiency Alerts, High-Value Leads), a reverse-chronological activity feed of recent filings (click through to the firm), a clearing-provider market-share donut (click a segment to filter the Broker Dealers list), and a data-freshness banner showing when pipelines last ran.

![Dashboard with KPI cards, activity feed and clearing distribution](images/03-dashboard.png)

## 9.3 Broker Dealers (`/master-list`)

The core research surface: every active U.S. broker-dealer in a searchable, sortable table.

![Broker Dealers list with filters and badges](images/04-broker-dealers-list.png)

- **Columns:** Firm name (+ location), CIK, clearing arrangement (partner name), clearing type (Fully Disclosed / Self-Clearing / Omnibus / Non-Carrying / Unknown), clearing-agency memberships (OCC, DTC, NSCC, FICC badges), financial health (green/amber/red), prospect priority (Hot/Warm/Cold stars), net capital, YoY growth; optional registration date, last filing, 3-yr CAGR.
- **Filters:** free-text search (name/CIK/CRD), state, health, priority, clearing partner (multi-select), clearing type, types of business, net-capital range, registration-date range. A `?segment=high_value` deep link highlights High-Value Participants (net capital $5M–$100M or OTC corporate-equity retail).
- **List modes:** *Primary* (default — healthy firms), *Alternative* (firms with a 17a-11 capital-deficiency filing), *All*.
- Everything (filters, sort, page) lives in the URL, so views can be bookmarked and shared; the sidebar link preserves your working state.
- **Missing values** show an ⓘ explanation of *why* they're missing (firm doesn't disclose, no filing available, low-confidence extraction, not yet extracted…), so blank never means "we didn't look."

### Firm detail (`/master-list/{id}`)

![Broker-dealer detail page](images/05-broker-dealer-detail.png)

A 360° profile: financial overview with history and trend; clearing arrangement with a link to the **source SEC filing**; executive contacts (names/titles from filings, plus enriched email/phone/LinkedIn with per-field source badges); discovered-emails section; full filing history (filterable, links to EDGAR); registration & compliance snapshot; clearing-agency memberships. Actions: **Refresh all** (re-pull missing data from SEC/FINRA on demand), **Enrich** contacts (runs the provider chain; phone numbers can arrive a minute or two later via Apollo's callback), **Find emails** (domain scan), **Outreach** (compose an email to a contact), **Add to favorites**, **Copy domain**, link to the FINRA BrokerCheck PDF. Prev/Next walk the firm list you came from.

## 9.4 Investment Advisers (`/advisor-list`)

The adviser-side mirror: ~3,100 13F-filing advisers by default (toggle to all registered advisers). Columns include CRD, regulatory AUM, advisory activities, client types, and contact-channel icons. Supports the same URL-state filtering, favorites, detail pages (AUM, principals, Form ADV filings, alternative business names), enrichment, and outreach. *(The old "Institutional Investors" page was merged here; old links redirect.)*

![Investment Advisors list](images/06-advisor-list.png)

## 9.5 Investors — insider transactions (`/investors`)

A feed of SEC **Form 4** insider transactions: tabs for Buyers (A), Sellers (D), and All; filter by name, ticker, state, date window, and transaction value; sort by any column. Per row: **Enrich** fetches the person's business contact details on demand; **Outreach** opens the composer with a just-in-time email lookup (if no real address is found, there's nothing to send to — the system never invents one).

![Investors insider-transaction feed](images/07-investors.png)

## 9.6 Alerts (`/alerts`)

The filing-monitor output: new Form BD registrations (High), Form 17a-11 deficiency notices (Critical), X-17A-5 filings / clearing-partner changes (High), net-capital threshold crosses (Medium). Filter by form type and priority; mark read individually or in bulk; click through to the firm. New alerts stream in live while the page is open.

![Filing alerts feed](images/08-alerts.png)

## 9.7 Email Extractor (`/email-extractor`)

Discovers **published** email addresses for a firm's domain. Enter a domain (or click "Find emails" on a firm page) → a scan runs in the background (sources: Hunter, Snov, the firm's own site, passive OSINT) → results list addresses with their source and any person attribution. Per address you can **Enrich** (the Apollo→PDL→Hunter→Snov→name-lookup→web chain fills name/title/company/LinkedIn/phone where a provider corroborates them; "Not found" rows get a **Re-enrich** retry), **Enrich all** for the batch, and optionally run **SMTP verification** to test deliverability of selected addresses. Recent scans are listed with status; failed scans can be retried.

![Email Extractor](images/09-email-extractor.png)

## 9.8 Outreach (`/outreach/sent`) and Contacts (`/outreach/contacts`)

**Create** tab: pick a recipient (search firm contacts, drill into favorites, or type a **One-off** address), choose the sending mailbox (your linked Gmail/Microsoft/Yahoo account), optionally pick the Vault service folder the message is about, then either write the email or let the AI draft it from the firm profile + your service material — and edit before sending. Cc/Bcc supported; your saved signature is appended.
**Drafts** tab: messages you (or Doxie) saved but didn't send — edit, send, or delete.
**Sent History** tab: every send with recipient, subject, time, status (sent/failed with reason), and full body on click. Admins can see all users' history; you see your own. Deleting hides a row from the list but the record is retained for audit.

![Outreach workspace](images/10-outreach-workspace.png)

**Contacts** (`/outreach/contacts`): browse every enriched contact grouped by firm; expand a firm for its people (email/phone/LinkedIn with source + confidence popovers); **Enrich all** to fill gaps; per-contact **Find phone**.

![Contacts browser grouped by firm](images/11-outreach-contacts.png)

> You must link a mailbox before sending (Settings → Email Accounts). The first send will also ask for the provider's "send" permission — that's the incremental OAuth consent.

## 9.9 Vault (`/vault`)

Folders describing **what you sell** (e.g., "Clearing Outsourcing"), each with a description, outreach instructions, an optional default sending account, and uploaded reference documents (PDF/Office/text, ≤10 MB each). The AI uses the folder's material to ground outreach drafts and to answer `ask Doxie about my vault` questions. Files process briefly after upload (extracting/ready/failed with retry); downloads are time-limited links.

![Vault — a new account's empty state, prompting for the first service](images/12-vault.png)

## 9.10 Doxie — the assistant (chat bubble, any page)

Ask in plain English: *"self-clearing firms in Texas over $10M net capital"*, *"summarize Apex's latest FOCUS filing"*, *"who do we know at Acme Securities?"*, *"draft an intro email to their CFO about clearing services."* Doxie searches the same data you see (respecting your permissions), cites app deep-links, can add favorites, mark alerts read, start email scans, and look up unfamiliar industry terms on the web (remembering definitions for next time). For email: it shows you a **draft card** — you review/edit, and nothing sends until you explicitly confirm. Voice input is available via the microphone button (transcription happens in your browser). "New chat" archives the current conversation; the history browser reopens past ones.

![Doxie answering a question with a tool-grounded result and deep link](images/13-doxie-chat.png)

## 9.11 Favorites & Visited (`/my-favorites`, `/visited-firms`)

Multi-list bookmarking across firm types (create/rename/delete lists; a default "All Saved" list always exists), usable as outreach recipient sources. Visited Firms is an automatic recently-viewed trail.

## 9.12 Settings

| Page | Who | What |
|---|---|---|
| `/settings` | Admin | Lead-scoring weight sliders (must total 100%), competitor/clearing-provider list (drives the "competitor" logic), pipeline triggers (Refresh Data, FINRA detail refresh) with run history |
| `/settings/users` (+ detail, activities, saved-firms) | Admin | Approve pending signups; set role and per-feature permissions; review a user's activity audit and saved firms; removed-users record |
| `/settings/extractions` | Admin | Extraction analytics: which provider produced which emails/phones/websites, firm-by-firm drill-down |
| `/settings/email-accounts` | All | Link/unlink sending mailboxes |
| `/settings/account` | All | Change password |

![Admin settings — scoring weights, competitor providers, pipelines](images/14-settings-admin.png)

![Extraction analytics — per-provider attribution](images/15-extraction-analytics.png)

## 9.13 Things the app intentionally won't let you do

- **Export data** (CSV/download) — disabled product-wide.
- **Copy/print page content** outside form fields — the UI watermarks and blocks it (the dataset is licensed; see [Doc 08](08-security.md) §8.4).
- **Send outreach without a linked mailbox** or to a contact with no real email address.
- **Use admin features as a viewer** — and individual features can be switched off per account.

---

[← Security](08-security.md) | [Index](README.md) | [Next: Operations & Environments →](10-operations-and-environments.md)
