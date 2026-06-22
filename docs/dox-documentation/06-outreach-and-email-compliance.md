# 06 — Outreach & Email Compliance

[← Personal Data & Privacy](05-personal-data-and-privacy.md) | [Index](README.md) | [Next: AI Features & LLM Data Flows →](07-ai-features-and-llm-data-flows.md)

---

This document describes the outbound-email system end to end, because it is the part of the product where commercial-email law (e.g., CAN-SPAM) is most directly relevant. It states plainly both what controls exist and which conventional compliance features are **absent**, so counsel can judge the posture accurately.

## 6.1 The model: individual emails from the user's own mailbox

The defining design fact: **DOX does not operate a sending platform.** There is no bulk-mail engine, no shared sending domain, no scheduling/sequencing, and no system-owned outbound identity for business mail. Each outreach email is:

1. composed (or AI-drafted and then reviewed) by a human user,
2. addressed to one recipient (plus optional Cc/Bcc the user typed),
3. transmitted by the user's **own linked mailbox** — Gmail, Microsoft, or Yahoo — under an OAuth send scope that the user granted personally,
4. recorded in full in an audit table.

Practically, the email leaves the user's normal corporate/personal mail account exactly as if they had sent it from their mail client; deliverability, provider sending limits, and the provider's own anti-abuse rules apply to the user's account. The system's send volume is human-scaled (one composer action per email).

## 6.2 Composition paths

| Path | How the content is produced |
|---|---|
| Manual | User types subject/body in the composer (`frontend/components/outreach/create-outreach-tab.tsx`) |
| AI-drafted (endpoint) | `POST /outreach/draft` (and advisor/investor/one-off variants) calls Gemini with firm context (name, location, clearing partner, operations text), the contact's name/title, the user's selected Vault "service" folder (description, instructions, retrieved document passages), producing a `{subject, body}` the user can edit. Prompt constraints (`backend/app/services/outreach.py`): subject < 70 chars, no fake "Re:", 80–140 words, three short plain-text paragraphs, no invented facts |
| AI-drafted (Doxie) | The assistant's `draft_outreach_email` tool produces a draft into an interactive card; sending requires the separate confirmed-send step (§6.4) |

Recipients come from enriched firm contacts (only contacts that actually have an email — the API rejects contacts without one), from favorites lists drill-down, or as **"One-off"** free-form addresses the user types. A just-in-time lookup on the Form 4 feed finds a real email at send time or yields none — placeholders are never fabricated.

## 6.3 Transport details

`backend/app/services/outreach_send.py` + `email_providers/{google,microsoft,yahoo}.py`:

- **Sender resolution**: explicit account choice → folder default → first linked account with the send scope → first linked account (which then triggers a scope prompt). Zero linked accounts → HTTP 412 `*_account_not_linked`; missing scope → HTTP 412 `*_scope_required` with an incremental-consent prompt (send scopes are deliberately **not** requested at login — least-privilege consent).
- **Recipient hygiene**: To/Cc/Bcc are normalized and de-duplicated case-insensitively across buckets (earliest bucket wins) before send (`dedupe_recipients`).
- **Bcc semantics**: each transport hides Bcc correctly (Gmail strips it server-side; Graph uses `bccRecipients`; the Yahoo SMTP path derives envelope recipients then strips the header), while the audit row records the Bcc list.
- **Retries**: only transient Gmail statuses (408/409/429/5xx) retry, max twice with capped backoff.
- **Signature**: the user's saved signature (`user_outreach_settings.signature`) is appended client-side to the body; its content is entirely user-authored.

## 6.4 The Doxie confirmed-send gate

When the AI assistant is involved, sending is a **two-step human-confirmed flow**: the model may create and save a draft, must show the user the full To/Cc/Bcc/subject/body, and may call `send_outreach_draft` only with `confirm=true`, which the backend requires; the instruction regime ties that flag to an explicit user confirmation in the user's latest message. Drafts are first-class rows (`outreach_draft`) the user can also open, edit, and send from the Outreach workspace. (The residual risk that a model sets `confirm=true` without genuine confirmation is noted in [Doc 11](11-legal-considerations-for-counsel.md) §8.)

## 6.5 The audit trail

Every send attempt — success or failure — writes exactly one `outreach_send` row containing: the sending user, the firm and contact linkage (polymorphic across BD/adviser/investor, or free-form recipient name/email), the **exact subject and full body as sent**, all recipients including Bcc, the provider and sender address, the provider message ID, status (`sent`/`failed`) and a machine-readable error code on failure, and the timestamp. User-facing "delete" only sets `archived_at` (soft delete); rows are retained. Administrators can view all users' sends; users see their own. This table is the discovery/compliance record of outbound mail.

## 6.6 Compliance posture — explicit statement of present and absent controls

**Present:**
- Human review before every send (composer or confirmed-send gate).
- One-to-one sending from the user's own authenticated mailbox; no bulk channel exists at all.
- Full content + recipient audit trail, retained through soft delete.
- Server-side refusal to send to contacts with no real email; no placeholder addresses.
- B2B-only recipient universe by construction (officers/insiders of regulated firms, business addresses).
- Provider-level controls inherited from Gmail/Microsoft/Yahoo (their sending limits and abuse detection apply per user account).

**Absent (factually not implemented — decisions for counsel/product, not oversights in documentation):**
- **No automatic unsubscribe/opt-out link** is injected into messages, and no mechanism exists to receive or record an opt-out.
- **No suppression / do-not-contact list**: nothing prevents emailing a person again after they object, except the user's own judgment; there is no flag on contact rows.
- **No physical postal address or "advertisement" identification** is auto-appended (CAN-SPAM content elements are left to the user's typed content/signature).
- **No application-level send caps or rate limits** (provider quotas are the only volume control).
- **No pre-send verification requirement** (verification tooling exists in the Email Extractor but is not enforced on the send path).

The practical compliance theory today is that DOX functions like a CRM assisting individual business correspondence rather than a commercial bulk-mail platform, with the **sending user responsible for message-content compliance**. Whether that allocation is acceptable, and whether any of the absent controls should be added (opt-out capture and suppression being the most conventional), is consolidated as a counsel question in [Doc 11](11-legal-considerations-for-counsel.md) §6.

## 6.7 Transactional email (separate path)

System emails — signup verification, password reset, admin-approval requests, new-sign-in alerts — go through a dedicated SMTP path (`gmail_sender.py` / frontend Better Auth mailer with `SMTP_*` settings), addressed only to the application's own users as part of operating their accounts. This path never sends marketing or outreach content and is intentionally separate from the OAuth outreach transports.

## 6.8 OAuth provider policy note

Because sends execute through Google/Microsoft/Yahoo APIs under user consent, the integration is subject to those providers' API/platform policies (e.g., Google's API Services User Data Policy, including Limited-Use requirements for Gmail scopes, and each provider's app-verification regime for the OAuth client). Current scopes are minimal (`gmail.send`, `Mail.Send`, `mail-w`) and requested incrementally. Confirming the OAuth apps' verification status and policy fit is flagged in [Doc 11](11-legal-considerations-for-counsel.md) §6.

---

[← Personal Data & Privacy](05-personal-data-and-privacy.md) | [Index](README.md) | [Next: AI Features & LLM Data Flows →](07-ai-features-and-llm-data-flows.md)
