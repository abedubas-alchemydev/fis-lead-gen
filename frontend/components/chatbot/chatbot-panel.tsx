"use client";

import { Paperclip, PlusCircle, Send, X } from "lucide-react";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  type ChangeEvent,
  type KeyboardEvent,
} from "react";

import { ChatbotMessage, type ChatMessage } from "./chatbot-message";
import { ChatbotAttachButton, ChatbotVoiceButton } from "./chatbot-input-controls";

export interface ChatbotPanelProps {
  messages: ReadonlyArray<ChatMessage>;
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onClose: () => void;
  onNewChat?: () => void;
  isSending?: boolean;
  isLoadingHistory?: boolean;
  // A file the user attached (paperclip) that hasn't been filed yet — shown
  // as a chip; the widget runs the "which folder?" flow on the next send.
  pendingFileName?: string | null;
  onAttachFile?: (file: File) => void;
  onClearPendingFile?: () => void;
  // Fires when the user clicks an in-app link rendered in a Doxie reply.
  // Lets the widget collapse the panel so the user can actually see the
  // destination page (otherwise the chat panel keeps floating over it).
  onInternalNavigate?: () => void;
}

export interface ChatbotPanelHandle {
  focusInput: () => void;
}

export const ChatbotPanel = forwardRef<ChatbotPanelHandle, ChatbotPanelProps>(function ChatbotPanel(
  {
    messages,
    input,
    onInputChange,
    onSend,
    onClose,
    onNewChat,
    isSending = false,
    isLoadingHistory = false,
    pendingFileName = null,
    onAttachFile,
    onClearPendingFile,
    onInternalNavigate,
  },
  ref,
) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useImperativeHandle(ref, () => ({
    focusInput: () => inputRef.current?.focus(),
  }));

  // Auto-scroll to the latest message when the conversation grows.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);

  const canSend = input.trim().length > 0 || Boolean(pendingFileName);

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!isSending && canSend) onSend();
    }
  }

  function handleChange(event: ChangeEvent<HTMLTextAreaElement>) {
    onInputChange(event.target.value);
  }

  const sendDisabled = isSending || !canSend;

  return (
    <div
      id="chatbot-panel"
      role="dialog"
      aria-modal="false"
      aria-labelledby="chatbot-panel-title"
      className="animate-scale-in fixed bottom-24 right-6 z-50 flex h-[520px] max-h-[calc(100vh-7rem)] w-[380px] max-w-[calc(100vw-3rem)] flex-col overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] shadow-2xl"
    >
      <header className="flex items-center justify-between border-b border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3">
        <div className="flex flex-col">
          <h2 id="chatbot-panel-title" className="text-sm font-semibold text-[var(--text,#0f172a)]">
            Chat with Doxie
          </h2>
          <p className="text-xs text-[var(--text-muted,#94a3b8)]">Your in-app AI assistant</p>
        </div>
        <div className="flex items-center gap-1">
          {onNewChat ? (
            <button
              type="button"
              onClick={onNewChat}
              aria-label="Start a new chat"
              title="Start a new chat"
              disabled={isSending}
              className="grid h-9 w-9 place-items-center rounded-lg text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40"
            >
              <PlusCircle size={18} strokeWidth={2} />
            </button>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close chat with Doxie"
            className="grid h-9 w-9 place-items-center rounded-lg text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40"
          >
            <X size={18} strokeWidth={2} />
          </button>
        </div>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {isLoadingHistory ? (
          <p className="text-center text-xs text-[var(--text-muted,#94a3b8)]">
            Loading your conversation…
          </p>
        ) : (
          messages.map((message) => (
            <ChatbotMessage
              key={message.id}
              role={message.role}
              content={message.content}
              pending={message.pending}
              error={message.error}
              toolStatus={message.toolStatus}
              isFinalized={message.isFinalized}
              onInternalNavigate={onInternalNavigate}
            />
          ))
        )}
        <div ref={endRef} />
      </div>

      <form
        className="flex flex-col gap-2 border-t border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!sendDisabled) onSend();
        }}
      >
        {pendingFileName ? (
          <div className="flex items-center gap-2 self-start rounded-lg bg-[var(--surface-2,#f1f6fd)] px-2 py-1 text-xs text-[var(--text,#0f172a)]">
            <Paperclip size={12} strokeWidth={2} className="shrink-0 text-[var(--text-dim,#475569)]" />
            <span className="max-w-[240px] truncate">{pendingFileName}</span>
            {onClearPendingFile ? (
              <button
                type="button"
                onClick={onClearPendingFile}
                aria-label="Remove attachment"
                title="Remove attachment"
                className="grid h-4 w-4 place-items-center rounded text-[var(--text-muted,#94a3b8)] transition hover:text-[var(--text,#0f172a)]"
              >
                <X size={12} strokeWidth={2} />
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="flex items-end gap-2">
          {onAttachFile ? (
            <ChatbotAttachButton onFile={onAttachFile} disabled={isSending} />
          ) : null}
          <ChatbotVoiceButton
            onTranscript={(text) => onInputChange(input ? `${input} ${text}` : text)}
            disabled={isSending}
          />
          <textarea
            ref={inputRef}
            value={input}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder={
              isSending
                ? "Doxie is thinking…"
                : pendingFileName
                  ? "Name a folder to file it in, or just send…"
                  : "Type a message…"
            }
            aria-label="Chat message"
            disabled={isSending}
            className="max-h-32 min-h-[40px] flex-1 resize-none rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm text-[var(--text,#0f172a)] placeholder:text-[var(--text-muted,#94a3b8)] focus:border-[var(--doxie,#6366f1)] focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/30 disabled:cursor-not-allowed disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={sendDisabled}
            aria-label="Send message"
            className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--doxie,#6366f1)] text-white shadow-sm transition hover:bg-[var(--doxie-2,#8b5cf6)] disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40 focus:ring-offset-2"
          >
            <Send size={16} strokeWidth={2} />
          </button>
        </div>
      </form>
    </div>
  );
});
