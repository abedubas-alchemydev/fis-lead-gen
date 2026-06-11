"use client";

import clsx from "clsx";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Loader2,
  Mail,
  Send,
} from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  composeSendOutreach,
  deleteOutreachDraft,
  getOutreachDraft,
} from "@/lib/api";
import type { SavedOutreachDraftDetail } from "@/lib/types";

import { markDraftSent, type DoxieOutreachDraft } from "./chatbot-draft";

// Where the saved drafts live in the app — same deep-link the backend
// outreach tools emit (OUTREACH_DRAFTS_URL in chatbot_urls.py).
const OUTREACH_DRAFTS_ROUTE = "/outreach/sent?tab=drafts";

// Collapsed body preview height: ~10 lines at text-[12px]/leading-5.
const BODY_COLLAPSED_MAX_H = "max-h-[200px]";
const BODY_EXPANDED_MAX_H = "max-h-[420px]";

type SendPhase = "idle" | "loading" | "confirm" | "sending" | "sent";

function recipientLabel(r: { email: string; name?: string | null }): string {
  return r.name?.trim() ? r.name : r.email;
}

// Friendly copy for the send path's failure modes. The 412s are the ones
// worth special copy: the fix (linking Gmail / Outlook / Yahoo) lives on
// the Outreach page, not in chat.
function sendErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 412) {
      if ((err.detail || "").endsWith("_scope_required")) {
        return (
          "Your linked mailbox hasn't granted send permission yet. " +
          "Re-connect Gmail, Outlook, or Yahoo from the Outreach page, " +
          "then try again."
        );
      }
      return (
        "No linked mailbox yet — connect Gmail, Outlook, or Yahoo on the " +
        "Outreach page, then try again."
      );
    }
    if (err.status === 404) {
      return "This draft no longer exists — it may have already been sent or deleted.";
    }
    if (err.status === 502) {
      return "The email provider rejected the send. Try again in a moment.";
    }
    if (err.status === 503) {
      return "Email sending isn't configured on this environment. Contact an administrator.";
    }
    return err.detail || err.message;
  }
  return "Couldn't send the draft. Please try again.";
}

/**
 * Interactive draft card rendered under an assistant bubble whose turn
 * produced an outreach draft. Additive to the plain-text flow: the user
 * can still read the draft in the reply and type "yes, send it" — this
 * card is the click path.
 *
 * "Send now" mirrors the composer's send-a-saved-draft flow byte for
 * byte: fetch the canonical saved draft, compose-send its exact fields,
 * then best-effort delete the draft row (same as Doxie's
 * send_outreach_draft tool — no signature is appended).
 */
export function ChatbotDraftCard({
  draft,
  onInternalNavigate,
}: {
  draft: DoxieOutreachDraft;
  onInternalNavigate?: () => void;
}) {
  const router = useRouter();
  const [phase, setPhase] = useState<SendPhase>(draft.sentAt ? "sent" : "idle");
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The canonical saved draft fetched when the confirm dialog opens — the
  // dialog states exactly what the server would transmit.
  const [detail, setDetail] = useState<SavedOutreachDraftDetail | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const extraRecipients =
    Math.max(0, draft.to.length - 3) + draft.cc.length + draft.bcc.length;
  const bodyIsLong =
    draft.body.split("\n").length > 10 || draft.body.length > 600;
  const canSend = draft.draftId !== null && phase !== "sent";

  async function handleSendClick() {
    if (draft.draftId === null || phase === "loading" || phase === "sending") {
      return;
    }
    setPhase("loading");
    setError(null);
    try {
      // Always send the server's saved draft, never the chat snapshot —
      // same safety contract as the send_outreach_draft tool.
      const fetched = await getOutreachDraft(draft.draftId);
      if (!isMountedRef.current) return;
      setDetail(fetched);
      setPhase("confirm");
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(sendErrorMessage(err));
      setPhase("idle");
    }
  }

  async function handleConfirmSend() {
    if (draft.draftId === null || detail === null || phase === "sending") {
      return;
    }
    setPhase("sending");
    setError(null);
    try {
      await composeSendOutreach({
        to: detail.to,
        cc: detail.cc,
        bcc: detail.bcc,
        subject: detail.subject,
        body: detail.body,
        sender_account_id: detail.sender_account_id,
        folder_id: detail.folder_id,
      });
      // Sent — drop the draft row like the composer and the Doxie send
      // tool do. Best-effort: a delete hiccup must never turn a
      // successful send into an error state.
      try {
        await deleteOutreachDraft(draft.draftId);
      } catch {
        // swallow — the send already succeeded.
      }
      markDraftSent(draft);
      if (!isMountedRef.current) return;
      setDetail(null);
      setPhase("sent");
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(sendErrorMessage(err));
      setDetail(null);
      setPhase("idle");
    }
  }

  function handleCancelConfirm() {
    if (phase === "sending") return;
    setDetail(null);
    setPhase("idle");
  }

  function handleOpenInOutreach() {
    router.push(OUTREACH_DRAFTS_ROUTE as Route);
    // Collapse the floating panel so the destination page is visible.
    onInternalNavigate?.();
  }

  return (
    <div className="mt-1.5 w-[300px] max-w-full overflow-hidden rounded-2xl rounded-tl-md border border-[var(--doxie,#6366f1)]/25 bg-[var(--surface,#ffffff)] text-left shadow-sm">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-[var(--doxie,#6366f1)]/15 bg-gradient-to-r from-[var(--doxie,#6366f1)]/10 to-[var(--doxie-2,#8b5cf6)]/10 px-3 py-2">
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-gradient-to-br from-[var(--doxie,#6366f1)] to-[var(--doxie-2,#8b5cf6)] text-white">
          <Mail size={13} strokeWidth={2} aria-hidden />
        </span>
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#4338ca]">
          Outreach draft
        </span>
        <span
          className={clsx(
            "ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold",
            phase === "sent"
              ? "bg-emerald-500/15 text-emerald-700"
              : draft.draftId !== null
                ? "bg-[var(--doxie,#6366f1)]/12 text-[#4338ca]"
                : "bg-[var(--surface-2,#f1f6fd)] text-[var(--text-muted,#94a3b8)]",
          )}
        >
          {phase === "sent"
            ? "Sent"
            : draft.draftId !== null
              ? `Draft #${draft.draftId}`
              : "Not saved yet"}
        </span>
      </div>

      <div className="space-y-2 px-3 py-2.5">
        {/* Subject */}
        <p className="text-[13px] font-semibold leading-5 text-[var(--text,#0f172a)]">
          {draft.subject || (
            <span className="italic text-[var(--text-muted,#94a3b8)]">
              (no subject)
            </span>
          )}
        </p>

        {/* Recipient chips */}
        <div className="flex flex-wrap items-center gap-1">
          {draft.to.slice(0, 3).map((r) => (
            <span
              key={r.email}
              title={r.email}
              className="inline-flex max-w-full items-center gap-1 rounded-full border border-[var(--doxie,#6366f1)]/25 bg-[var(--doxie,#6366f1)]/8 px-2 py-0.5 text-[11px] font-medium text-[#4338ca]"
            >
              <span className="truncate">{recipientLabel(r)}</span>
            </span>
          ))}
          {extraRecipients > 0 ? (
            <span className="rounded-full bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[11px] text-[var(--text-muted,#94a3b8)]">
              +{extraRecipients} more
            </span>
          ) : null}
        </div>

        {/* Body preview — monospace-ish, scrollable, ~10 lines collapsed */}
        {draft.body ? (
          <div>
            <pre
              className={clsx(
                "overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-[var(--surface-2,#f1f6fd)] px-2.5 py-2 font-mono text-[12px] leading-5 text-[var(--text-dim,#475569)]",
                expanded ? BODY_EXPANDED_MAX_H : BODY_COLLAPSED_MAX_H,
              )}
            >
              {draft.body}
            </pre>
            {bodyIsLong ? (
              <button
                type="button"
                onClick={() => setExpanded((open) => !open)}
                className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-[var(--doxie,#6366f1)] transition hover:brightness-110 focus:outline-none focus:ring-1 focus:ring-[var(--doxie,#6366f1)]/40"
              >
                {expanded ? (
                  <ChevronUp size={12} strokeWidth={2} aria-hidden />
                ) : (
                  <ChevronDown size={12} strokeWidth={2} aria-hidden />
                )}
                {expanded ? "Show less" : "Show more"}
              </button>
            ) : null}
          </div>
        ) : (
          <p className="rounded-lg bg-[var(--surface-2,#f1f6fd)] px-2.5 py-2 text-[12px] italic leading-5 text-[var(--text-muted,#94a3b8)]">
            Body preview isn&rsquo;t available here — open the draft in
            Outreach to review it.
          </p>
        )}

        {/* Inline error (412 mailbox linking, 404 gone, provider errors) */}
        {error ? (
          <p
            role="alert"
            className="flex items-start gap-1.5 rounded-lg border border-red-500/25 bg-red-500/10 px-2.5 py-2 text-[12px] leading-5 text-[var(--danger,#dc2626)]"
          >
            <AlertCircle size={13} className="mt-0.5 shrink-0" aria-hidden />
            <span>{error}</span>
          </p>
        ) : null}

        {/* Actions */}
        <div className="flex items-center gap-2 pt-0.5">
          <button
            type="button"
            onClick={handleOpenInOutreach}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--doxie,#6366f1)]/30 bg-[var(--surface,#ffffff)] px-2.5 py-1.5 text-[12px] font-medium text-[var(--doxie,#6366f1)] transition hover:bg-[var(--doxie,#6366f1)]/8 focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40"
          >
            <ExternalLink size={13} strokeWidth={2} aria-hidden />
            Open in Outreach
          </button>
          <button
            type="button"
            onClick={handleSendClick}
            disabled={!canSend || phase === "loading" || phase === "sending"}
            title={
              draft.draftId === null
                ? "Save the draft first — ask Doxie to save it to your Drafts tab."
                : undefined
            }
            className={clsx(
              "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-semibold transition focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40",
              phase === "sent"
                ? "cursor-default bg-emerald-500/15 text-emerald-700"
                : "bg-gradient-to-br from-[var(--doxie,#6366f1)] to-[var(--doxie-2,#8b5cf6)] text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {phase === "sent" ? (
              <>
                <Check size={13} strokeWidth={2.5} aria-hidden />
                Sent
              </>
            ) : phase === "loading" || phase === "sending" ? (
              <>
                <Loader2 size={13} className="animate-spin" aria-hidden />
                {phase === "sending" ? "Sending…" : "Loading…"}
              </>
            ) : (
              <>
                <Send size={13} strokeWidth={2} aria-hidden />
                Send now
              </>
            )}
          </button>
        </div>
      </div>

      {(phase === "confirm" || phase === "sending") && detail ? (
        <ConfirmSendDialog
          detail={detail}
          sending={phase === "sending"}
          onCancel={handleCancelConfirm}
          onConfirm={() => void handleConfirmSend()}
        />
      ) : null}
    </div>
  );
}

// Modal confirm for the real send. ``fixed`` resolves against the chat
// panel (its scale-in animation leaves a fill-mode transform, making it
// the containing block), so the backdrop dims the panel rather than the
// whole page — a scoped confirm, layered above everything in it (z-60).
// Escape is captured before the widget's window listener so dismissing
// the dialog doesn't also close the whole chat.
function ConfirmSendDialog({
  detail,
  sending,
  onCancel,
  onConfirm,
}: {
  detail: SavedOutreachDraftDetail;
  sending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      // Capture phase + stopPropagation: the chat widget closes the whole
      // panel on a window-level (bubble) Escape — swallow it here so
      // Escape only dismisses this dialog.
      event.stopPropagation();
      if (!sending) onCancel();
    }
    window.addEventListener("keydown", onKeyDown, { capture: true });
    return () =>
      window.removeEventListener("keydown", onKeyDown, { capture: true });
  }, [onCancel, sending]);

  const primary = detail.to[0];
  const others =
    Math.max(0, detail.to.length - 1) + detail.cc.length + detail.bcc.length;
  const recipientText = primary
    ? `${recipientLabel(primary)}${primary.name?.trim() ? ` (${primary.email})` : ""}`
    : "the saved recipients";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="doxie-send-draft-title"
      className="fixed inset-0 z-[60] flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={() => {
          if (!sending) onCancel();
        }}
        className="absolute inset-0 bg-[rgba(15,23,42,0.45)] backdrop-blur-sm"
      />
      <div className="relative w-full max-w-[360px] rounded-2xl border border-[var(--doxie,#6366f1)]/25 bg-[var(--surface,#ffffff)] p-4 shadow-[0_24px_48px_-16px_rgba(15,23,42,0.35)]">
        <h2
          id="doxie-send-draft-title"
          className="text-[14px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]"
        >
          Send this email to {recipientText}
          {others > 0 ? ` and ${others} more` : ""}?
        </h2>
        <p className="mt-2 text-[12px] leading-5 text-[var(--text-dim,#475569)]">
          &ldquo;{detail.subject || "(no subject)"}&rdquo; will be sent from
          your linked mailbox exactly as saved. This can&rsquo;t be undone.
        </p>
        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={sending}
            className="rounded-lg border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={sending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-br from-[var(--doxie,#6366f1)] to-[var(--doxie-2,#8b5cf6)] px-3 py-1.5 text-[12px] font-semibold text-white transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40"
          >
            {sending ? (
              <>
                <Loader2 size={13} className="animate-spin" aria-hidden />
                Sending…
              </>
            ) : (
              <>
                <Send size={13} strokeWidth={2} aria-hidden />
                Send
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
