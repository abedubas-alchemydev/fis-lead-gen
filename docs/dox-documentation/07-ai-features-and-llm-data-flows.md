# 07 — AI Features & LLM Data Flows

[← Outreach & Email Compliance](06-outreach-and-email-compliance.md) | [Index](README.md) | [Next: Security →](08-security.md)

---

DOX uses large language models in two distinct ways: **pipeline extraction** (turning regulatory PDFs into structured data) and the **Doxie assistant** (interactive chat over the application's own data). Both run on **Google's Gemini API**; this document specifies exactly what data crosses to Google in each, and the controls around AI-initiated actions.

## 7.1 Provider and models

| Role | Model (default) | Config |
|---|---|---|
| Structured PDF/text extraction | `gemini-2.5-flash` (temperature 0.1) | `GEMINI_PDF_MODEL`; Files API used for PDFs 20–45 MB (`LLM_USE_FILES_API`, 23h cache); >45 MB rejected |
| Doxie chat & outreach drafting | `gemini-2.5-flash` (temperature 0.7, max 4096 output tokens) | `GEMINI_CHAT_MODEL`, `GEMINI_CHAT_MAX_OUTPUT_TOKENS` |
| Embeddings (semantic search, Vault RAG) | `gemini-embedding-001` (768-dim vectors per the schema) | used by `chatbot_semantic.py`, `vault_embeddings.py` |

`LLM_PROVIDER` can switch extraction to OpenAI (`gpt-4o` configured) — not the active default. An `ANTHROPIC_API_KEY` config slot exists with no active call path. The product requirements document predates this and names OpenAI/Claude; **the implemented provider is Gemini** (a documented PRD-vs-code mismatch).

## 7.2 Pipeline extraction (no end-user interaction)

`backend/app/services/gemini_responses.py` exposes schema-constrained extraction calls used by the ingestion pipelines:

| Extraction | Input sent to Google | Output |
|---|---|---|
| Financial data | X-17A-5/FOCUS PDF (public SEC filing) | net capital, excess, totals, report date + confidence + evidence excerpt |
| Clearing data / classification | FOCUS PDF, or text (FINRA operations text + FOCUS excerpt) | clearing partner/type or classification ∈ {fully_disclosed, self_clearing, omnibus, non_carrying, unknown} + confidence + rationale |
| Adviser profile | Form ADV PDF (public SEC filing) | officers, owners, client types, dates |
| FOCUS contact | FOCUS PDF (vision mode) | the filing's named contact person (name/title/phone/email — PII that the firm itself filed publicly) |
| Firm aliases | Firm name + CRD (text) | brand/parent aliases for website resolution |
| Filing summaries | Any public filing PDF | narrative summary (Doxie's summarize tools) |

Inputs are **public regulatory documents and firm-level text**; prompts contain no credentials. All structured outputs carry confidence scores gated by minimums (0.65 financial / 0.7 clearing) with `needs_review` fallback. The clearing-classifier prompt encodes the product's canonical definitions and prefers the audited FOCUS text over self-declared FINRA text on conflict.

## 7.3 Doxie — the in-app assistant

**Architecture** (`backend/app/services/chatbot.py`, endpoint `/api/v1/chatbot/messages[/stream]`): a Gemini function-calling loop over a registry of **42 tools** (40 in `chatbot_tools.py` + 2 in `chatbot_tools_analytics.py`), streamed to the browser by SSE with event types `thinking` / `tool_call` / `tool_result` / `done` / `error`.

**Safety brakes:** max 8 tool iterations per request; 5s per-tool timeout (30s for PDF/outreach/web tools, 15s research); 60s wall-clock budget per chat request; 8,000-char message and 40,000-char request caps; per-process 60s tool-result cache. Tools never raise into the loop — failures return structured error objects.

**System prompt:** defines the persona, scope ("answer briefly without speculating about firm data you have not been shown. Do not invent numbers, names, or filings"), tool-usage rules, the outreach confirmation regime, and a feature catalog. Injected per turn: the current date and the user's page context (route + viewed firm). *Known artifact:* the prompt still calls the platform "Alchemy" (legacy branding; flagged in [Doc 11](11-legal-considerations-for-counsel.md) §9).

**Authorization:** every tool checks the calling user's role/feature permissions server-side (admins pass; viewers need the matching feature key). Write-capable tools are limited to: favorites add/remove, marking alerts read, starting email-extractor scans, and the draft/send flow below.

### The 42 tools

*Search & profiles:* `search_broker_dealers`, `get_broker_dealer_profile`, `search_investment_advisors`, `get_investment_advisor_profile`, `search_institutional_investors`, `get_institutional_investor_profile`, `list_broker_dealers_by_filter`, `list_investment_advisors_by_filter`, `semantic_firm_search`, `find_dual_registered_firms`.
*Filings & alerts:* `list_filings_for_firm`, `summarize_broker_dealer_filing`, `summarize_brokercheck_pdf`, `summarize_investment_advisor_filing`, `summarize_institutional_investor_filing`, `search_form4_filings`, `summarize_form4_filing`, `get_recent_alerts`, `mark_alerts_read`.
*Contacts & outreach:* `list_firm_contacts`, `find_contact_by_email`, `find_contacts_by_domain`, `draft_outreach_email`, `save_outreach_draft`, `list_outreach_drafts`, `get_outreach_draft`, `send_outreach_draft`.
*Email extractor:* `run_email_extractor`, `list_email_scans`, `get_email_scan_results`.
*Vault (user documents):* `ask_vault`, `list_vault_folders`, `list_vault_files`, `get_vault_file`.
*Analytics:* `get_firm_aggregates`, `list_firms_by_clearing_partner`.
*App knowledge & learning:* `get_app_help`, `research_term` (web research via SerpAPI with a persisted shared glossary, `chatbot_learned_term`).
*Personal & status:* `favorite_firm`, `unfavorite_firm`, `list_my_favorites`, `get_data_freshness`.

### What reaches Google per chat request

The request payload to the Gemini API contains: the system prompt (+ date/page context), the **full conversation history** of the active thread, the tool declarations, and — as the loop iterates — **complete tool results**. Tool results can include personal data from the database: contact names, titles, business emails and phones; Form 4 insider names and transaction details; Vault document passages (user-uploaded content); firm financials and clearing data. User feature-permission lists, credentials, and API keys are **not** sent.

### AI-initiated email: the confirmed-send gate

`draft_outreach_email` produces a draft (rendered in the UI as an interactive card — only the three draft tools' results are whitelisted into the SSE stream, capped at 24k chars). The backend requires `confirm=true` on `send_outreach_draft`; the instruction regime permits that flag only after the user explicitly confirms in their latest message, and the UI card gives the user direct review/edit/send control. See [Doc 06](06-outreach-and-email-compliance.md) §6.4.

### History and auditability

Conversations persist per user (`chatbot_conversation`/`chatbot_message`: role, content, page context; one active conversation, archive-on-new, last 50 browsable). **Tool calls and their arguments are not persisted** — only the final assistant prose — so there is no after-the-fact audit of which tools a chat invoked (noted in [Doc 11](11-legal-considerations-for-counsel.md) §9). The send itself, if any, is independently recorded in `outreach_send`.

## 7.4 Semantic search and embeddings

`chatbot_semantic.py` embeds a compact text summary (≤3,500 chars) of **every broker-dealer and investment adviser** into `chatbot_firm_embedding` (pgvector; content-hash dedupe so unchanged firms aren't re-embedded; refreshed by an admin backfill endpoint, a backfill script, and a nightly hook in the new-BD extractor). Vault uploads are chunked and embedded into `vault_folder_chunk` on upload. Queries are embedded with the same model and matched by cosine similarity. The text embedded is firm-level business information; Vault chunks are whatever the user uploaded.

## 7.5 Voice input and file uploads

- **Voice:** implemented entirely in the browser via the native Web Speech API (`frontend/components/chatbot/chatbot-input-controls.tsx`). Speech-to-text happens in the user's browser/OS speech service; **no audio is transmitted to or processed by DOX servers or Gemini** — only the resulting text enters the chat. (Browser speech services may involve the browser vendor's own processing, per that vendor's terms.)
- **File uploads:** uploads exist only in the Vault feature (PDF/DOCX/PPTX/XLSX/TXT/MD/RTF/CSV/HTML/JSON; ≤10 MB/file, ≤100 MB/user, ≤20 files/folder). Files are stored in GCS; text is extracted, chunked, embedded (Gemini), and retrieved into outreach drafts and `ask_vault` answers. The chat itself does not accept ad-hoc file attachments.

## 7.6 AI risk controls — summary for review

- Human-in-the-loop on the only outward-facing AI action (email send), with server-side `confirm` requirement and full audit of sends.
- Anti-hallucination instructions plus structured tool grounding; "I don't have that" UI states rather than invented values; extraction confidence gates with human-review queues.
- Permission-checked tools; no tool can read another user's private data (vault/drafts/favorites are owner-scoped).
- Bounded loops, timeouts, and budgets against runaway behavior; provider errors degrade to apologies, not actions.
- Residual items for counsel: Gemini API data-use terms ([Doc 11](11-legal-considerations-for-counsel.md) §7), tool-call audit gap (§9), and legacy "Alchemy" prompt branding (§9).

---

[← Outreach & Email Compliance](06-outreach-and-email-compliance.md) | [Index](README.md) | [Next: Security →](08-security.md)
