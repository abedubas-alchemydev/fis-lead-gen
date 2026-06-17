// Outreach-draft capture for the Doxie chat thread.
//
// While a turn streams, the widget collects the mirrored ``result``
// payloads of the outreach draft tools (draft_outreach_email /
// save_outreach_draft / get_outreach_draft — the only tools the backend
// ever mirrors, see STREAM_RESULT_TOOLS in backend chatbot.py) into a
// DoxieOutreachDraft snapshot. When the turn finalizes, the snapshot is
// remembered here keyed by the message's final content, and
// ChatbotMessage looks it up at render time to show an interactive
// draft card.
//
// Why a module store keyed by content: ChatbotPanel passes ChatbotMessage
// an explicit prop list (role/content/pending/error/toolStatus/
// isFinalized) with no message id, and the panel is owned by a sibling
// change — so content is the only stable per-message handle available on
// both sides. Identical-content collisions would just show the same card
// twice (the data IS the same draft), which is harmless.
//
// Live-session only by design: reloaded history renders without cards
// (the store is empty after a refresh) and the plain-text chat flow —
// including the typed "yes, send it" confirmation path — is unaffected.

export interface DoxieDraftRecipient {
  email: string;
  name?: string | null;
}

export interface DoxieOutreachDraft {
  subject: string;
  // May be empty when only save_outreach_draft ran (its result carries no
  // body) — the card shows an "open it in Outreach" fallback then.
  body: string;
  to: DoxieDraftRecipient[];
  cc: string[];
  bcc: string[];
  // Set once save_outreach_draft / get_outreach_draft reported a saved
  // row — the precondition for the card's "Send now" action.
  draftId: number | null;
  folderId: number | null;
  // FE-session marker set after the card sent the draft, so a remounted
  // card (panel closed + reopened) keeps showing the sent state.
  sentAt: number | null;
}

const DRAFT_RESULT_TOOLS = new Set([
  "draft_outreach_email",
  "save_outreach_draft",
  "get_outreach_draft",
]);

export function isDraftResultTool(name: string): boolean {
  return DRAFT_RESULT_TOOLS.has(name);
}

// ── Safe field coercion (results are unknown JSON from the wire) ───────

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asId(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? value
    : null;
}

function asEmailList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function asRecipientList(value: unknown): DoxieDraftRecipient[] {
  if (!Array.isArray(value)) return [];
  const out: DoxieDraftRecipient[] = [];
  for (const item of value) {
    if (typeof item !== "object" || item === null) continue;
    const email = asString((item as Record<string, unknown>).email);
    if (!email) continue;
    out.push({ email, name: asString((item as Record<string, unknown>).name) });
  }
  return out;
}

// ── Tool-result → draft snapshot ───────────────────────────────────────

/**
 * Fold one whitelisted tool result into the turn's draft snapshot.
 *
 * - draft_outreach_email starts a FRESH draft (clears any prior draftId —
 *   a new composition is not the previously saved row).
 * - get_outreach_draft is a complete saved snapshot and replaces outright.
 * - save_outreach_draft carries no body, so it merges into the prior
 *   snapshot; the prior body is kept only when the subjects match (an
 *   edited-then-saved email shouldn't preview stale text — better no
 *   preview than the wrong one; the send path always transmits the
 *   server's saved draft regardless).
 *
 * Returns ``prior`` unchanged when the result doesn't parse into a
 * renderable draft.
 */
export function applyDraftToolResult(
  prior: DoxieOutreachDraft | null,
  toolName: string,
  result: Record<string, unknown>,
): DoxieOutreachDraft | null {
  if (toolName === "draft_outreach_email") {
    const subject = asString(result.subject);
    const body = asString(result.body);
    const toEmail = asString(result.to_email);
    if (!subject || !body || !toEmail) return prior;
    return {
      subject,
      body,
      to: [{ email: toEmail, name: asString(result.to_name) }],
      cc: [],
      bcc: [],
      draftId: null,
      folderId: asId(result.folder_id),
      sentAt: null,
    };
  }

  if (toolName === "get_outreach_draft") {
    const draftId = asId(result.draft_id);
    const subject = asString(result.subject);
    const to = asRecipientList(result.to);
    if (!draftId || subject === null || to.length === 0) return prior;
    return {
      subject,
      body: asString(result.body) ?? "",
      to,
      cc: asEmailList(result.cc),
      bcc: asEmailList(result.bcc),
      draftId,
      folderId: asId(result.folder_id),
      sentAt: null,
    };
  }

  if (toolName === "save_outreach_draft") {
    const draftId = asId(result.draft_id);
    const subject = asString(result.subject);
    const toEmail = asString(result.to);
    if (!draftId || subject === null || !toEmail) return prior;
    const sameAsPrior =
      prior !== null &&
      prior.subject === subject &&
      prior.to[0]?.email === toEmail;
    return {
      subject,
      body: sameAsPrior ? prior.body : "",
      to: [
        { email: toEmail, name: sameAsPrior ? prior.to[0]?.name ?? null : null },
      ],
      cc: sameAsPrior ? prior.cc : [],
      bcc: sameAsPrior ? prior.bcc : [],
      draftId,
      folderId: asId(result.folder_id),
      sentAt: null,
    };
  }

  return prior;
}

// ── Per-session store, keyed by finalized message content ──────────────

// Insertion-ordered; oldest entries are evicted past the cap so a very
// long chat session can't grow the map unboundedly. 30 draft turns kept
// is far more than a session realistically produces.
const draftsByMessageContent = new Map<string, DoxieOutreachDraft>();
const MAX_REMEMBERED_DRAFTS = 30;

export function rememberDraftForMessage(
  content: string,
  draft: DoxieOutreachDraft,
): void {
  if (!content) return;
  // Re-insert so the entry moves to the newest position.
  draftsByMessageContent.delete(content);
  draftsByMessageContent.set(content, draft);
  while (draftsByMessageContent.size > MAX_REMEMBERED_DRAFTS) {
    const oldest = draftsByMessageContent.keys().next().value;
    if (oldest === undefined) break;
    draftsByMessageContent.delete(oldest);
  }
}

export function draftForMessage(
  content: string,
): DoxieOutreachDraft | undefined {
  return draftsByMessageContent.get(content);
}

// The store holds the same object reference the card renders, so an
// in-place mark survives a card remount (panel closed and reopened).
export function markDraftSent(draft: DoxieOutreachDraft): void {
  draft.sentAt = Date.now();
}
