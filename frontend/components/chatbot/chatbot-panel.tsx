"use client";

import { PlusCircle, Send, X } from "lucide-react";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  type ChangeEvent,
  type KeyboardEvent,
} from "react";

import { ChatbotMessage, type ChatMessage } from "./chatbot-message";

export interface ChatbotPanelProps {
  messages: ReadonlyArray<ChatMessage>;
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onClose: () => void;
  onNewChat?: () => void;
  isSending?: boolean;
  isLoadingHistory?: boolean;
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

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!isSending && input.trim().length > 0) onSend();
    }
  }

  function handleChange(event: ChangeEvent<HTMLTextAreaElement>) {
    onInputChange(event.target.value);
  }

  const sendDisabled = isSending || input.trim().length === 0;

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
            />
          ))
        )}
        <div ref={endRef} />
      </div>

      <form
        className="flex items-end gap-2 border-t border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!sendDisabled) onSend();
        }}
      >
        <textarea
          ref={inputRef}
          value={input}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          rows={1}
          placeholder={isSending ? "Doxie is thinking…" : "Type a message…"}
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
      </form>
    </div>
  );
});
