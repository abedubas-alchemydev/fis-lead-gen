"use client";

import { ArrowLeft, MessageCircle } from "lucide-react";
import { useEffect, useState } from "react";

import {
  listDoxieConversations,
  loadDoxieConversationMessages,
  reopenDoxieConversation,
  type DoxieConversationSummary,
  type DoxieHistoryMessage
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";

import { ChatbotMessage } from "./chatbot-message";

export interface ChatbotHistoryProps {
  // Returns to the live chat view without touching the active thread.
  onBack: () => void;
  // Fired after a successful reopen — the widget refetches the (now
  // restored) active thread, then the panel flips back to the chat view.
  onConversationReopened: () => void | Promise<void>;
  // Threaded through to ChatbotMessage so deep-links inside an archived
  // transcript still collapse the panel on navigation.
  onInternalNavigate?: () => void;
}

function HistorySubheader({
  label,
  onBack,
  backLabel
}: {
  label: string;
  onBack: () => void;
  backLabel: string;
}) {
  return (
    <div className="flex items-center gap-2 border-b border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2">
      <button
        type="button"
        onClick={onBack}
        aria-label={backLabel}
        title={backLabel}
        className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40"
      >
        <ArrowLeft size={16} strokeWidth={2} />
      </button>
      <span className="truncate text-xs font-semibold text-[var(--text,#0f172a)]">{label}</span>
    </div>
  );
}

// Conversation history browser rendered in place of the chat body.
// Two internal views: the conversation list, and a read-only transcript
// of one conversation with a "Continue this chat" action that reopens it
// as the active thread.
export function ChatbotHistory({
  onBack,
  onConversationReopened,
  onInternalNavigate
}: ChatbotHistoryProps) {
  const [conversations, setConversations] = useState<DoxieConversationSummary[] | null>(null);
  const [listError, setListError] = useState(false);
  // Forces the list effect to re-run after a failed load.
  const [listAttempt, setListAttempt] = useState(0);

  const [selected, setSelected] = useState<DoxieConversationSummary | null>(null);
  const [transcript, setTranscript] = useState<DoxieHistoryMessage[] | null>(null);
  const [transcriptError, setTranscriptError] = useState(false);
  const [isReopening, setIsReopening] = useState(false);
  const [reopenError, setReopenError] = useState(false);

  // Load the conversation list on mount (and on retry).
  useEffect(() => {
    let cancelled = false;
    setConversations(null);
    setListError(false);
    void (async () => {
      try {
        const rows = await listDoxieConversations();
        if (!cancelled) setConversations(rows);
      } catch (error) {
        if (cancelled) return;
        // eslint-disable-next-line no-console
        console.warn("Doxie conversation list load failed", error);
        setListError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [listAttempt]);

  // Load the transcript whenever a conversation is opened.
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setTranscript(null);
    setTranscriptError(false);
    void (async () => {
      try {
        const history = await loadDoxieConversationMessages(selected.id);
        if (!cancelled) setTranscript(history.messages);
      } catch (error) {
        if (cancelled) return;
        // eslint-disable-next-line no-console
        console.warn("Doxie transcript load failed", error);
        setTranscriptError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  async function handleContinue() {
    if (!selected || isReopening) return;
    setIsReopening(true);
    setReopenError(false);
    try {
      await reopenDoxieConversation(selected.id);
      // The widget reloads the active thread, then the panel flips back
      // to the chat view (this component unmounts).
      await onConversationReopened();
    } catch (error) {
      // eslint-disable-next-line no-console
      console.warn("Doxie conversation reopen failed", error);
      setReopenError(true);
      setIsReopening(false);
    }
  }

  // ── Transcript view ──────────────────────────────────────────────────
  if (selected) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <HistorySubheader
          label={selected.preview}
          backLabel="Back to conversation list"
          onBack={() => {
            setSelected(null);
            setReopenError(false);
          }}
        />
        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {transcriptError ? (
            <p className="text-center text-xs text-[var(--danger,#dc2626)]">
              Couldn&apos;t load this conversation. Go back and try again.
            </p>
          ) : transcript === null ? (
            <p className="text-center text-xs text-[var(--text-muted,#94a3b8)]">
              Loading conversation…
            </p>
          ) : transcript.length === 0 ? (
            <p className="text-center text-xs text-[var(--text-muted,#94a3b8)]">
              Nothing was said in this conversation.
            </p>
          ) : (
            transcript.map((message) => (
              <ChatbotMessage
                key={message.id}
                role={message.role}
                content={message.content}
                // Persisted turns fully streamed on their original turn —
                // render markdown + deep-links immediately.
                isFinalized
                onInternalNavigate={onInternalNavigate}
              />
            ))
          )}
        </div>
        <div className="border-t border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-3">
          {reopenError ? (
            <p className="mb-2 text-center text-xs text-[var(--danger,#dc2626)]">
              Couldn&apos;t reopen this conversation. Please try again.
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => void handleContinue()}
            disabled={isReopening || transcriptError}
            className="w-full rounded-xl bg-[var(--doxie,#6366f1)] px-3 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-[var(--doxie-2,#8b5cf6)] disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40 focus:ring-offset-2"
          >
            {isReopening ? "Reopening…" : "Continue this chat"}
          </button>
        </div>
      </div>
    );
  }

  // ── List view ────────────────────────────────────────────────────────
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <HistorySubheader label="Conversation history" backLabel="Back to chat" onBack={onBack} />
      <div className="flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {listError ? (
          <div className="space-y-2 pt-6 text-center">
            <p className="text-xs text-[var(--danger,#dc2626)]">
              Couldn&apos;t load your conversations.
            </p>
            <button
              type="button"
              onClick={() => setListAttempt((attempt) => attempt + 1)}
              className="text-xs font-medium text-[var(--doxie,#6366f1)] underline underline-offset-2 hover:text-[var(--doxie-2,#8b5cf6)] focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40"
            >
              Try again
            </button>
          </div>
        ) : conversations === null ? (
          <p className="pt-6 text-center text-xs text-[var(--text-muted,#94a3b8)]">
            Loading conversations…
          </p>
        ) : conversations.length === 0 ? (
          <p className="pt-6 text-center text-xs text-[var(--text-muted,#94a3b8)]">
            No past conversations yet.
          </p>
        ) : (
          conversations.map((conversation) => (
            <button
              key={conversation.id}
              type="button"
              onClick={() => setSelected(conversation)}
              className="w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-left transition hover:border-[var(--doxie,#6366f1)]/40 hover:bg-[var(--surface-2,#f1f6fd)] focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-[var(--text-muted,#94a3b8)]">
                  {formatRelativeTime(conversation.updated_at)}
                </span>
                {conversation.is_active ? (
                  <span className="shrink-0 rounded-full bg-[var(--doxie,#6366f1)]/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--doxie,#6366f1)]">
                    Active
                  </span>
                ) : null}
              </div>
              <p className="mt-1 truncate text-sm font-medium text-[var(--text,#0f172a)]">
                {conversation.preview}
              </p>
              <p className="mt-0.5 inline-flex items-center gap-1 text-xs text-[var(--text-muted,#94a3b8)]">
                <MessageCircle size={12} strokeWidth={2} />
                {conversation.message_count}{" "}
                {conversation.message_count === 1 ? "message" : "messages"}
              </p>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
