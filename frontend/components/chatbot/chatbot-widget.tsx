"use client";

import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ChatbotPanel, type ChatbotPanelHandle } from "./chatbot-panel";
import type { ChatMessage } from "./chatbot-message";

const WELCOME_MESSAGE: ChatMessage = {
  id: 0,
  role: "assistant",
  content: "Hi there! I'm Doxie 👋 I'm not quite ready to chat yet, but I'll be here soon.",
};

const STUB_REPLY = "Thanks! I can't reply for real yet, but I'll be here when chat goes live.";

function DoxieIcon({ size = 24, strokeWidth = 2 }: { size?: number; strokeWidth?: number }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3v2" />
      <circle cx="12" cy="2" r="1" fill="#fbbf24" stroke="none" />
      <rect x="4" y="6" width="16" height="13" rx="3" fill="rgba(255,255,255,0.1)" />
      <line x1="3" y1="11" x2="3" y2="14" />
      <line x1="21" y1="11" x2="21" y2="14" />
      <circle cx="9" cy="12" r="1.3" fill="#22d3ee" stroke="none" />
      <circle cx="15" cy="12" r="1.3" fill="#22d3ee" stroke="none" />
      <circle cx="9.4" cy="11.6" r="0.35" fill="#ffffff" stroke="none" />
      <circle cx="15.4" cy="11.6" r="0.35" fill="#ffffff" stroke="none" />
      <circle cx="7" cy="15.5" r="0.6" fill="#fb7185" stroke="none" opacity="0.7" />
      <circle cx="17" cy="15.5" r="0.6" fill="#fb7185" stroke="none" opacity="0.7" />
      <path d="M9 16q3 2 6 0" />
    </svg>
  );
}

export function ChatbotWidget() {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<ReadonlyArray<ChatMessage>>([WELCOME_MESSAGE]);
  const [input, setInput] = useState("");

  const fabRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<ChatbotPanelHandle>(null);
  const nextIdRef = useRef(1);

  const closePanel = useCallback(() => {
    setIsOpen(false);
    // Return focus to the FAB so keyboard users land somewhere predictable.
    fabRef.current?.focus();
  }, []);

  // Escape closes the panel when it's open.
  useEffect(() => {
    if (!isOpen) return;
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        closePanel();
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [isOpen, closePanel]);

  // Focus the input when the panel opens.
  useEffect(() => {
    if (isOpen) panelRef.current?.focusInput();
  }, [isOpen]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (trimmed.length === 0) return;
    setMessages((prev) => [
      ...prev,
      { id: nextIdRef.current++, role: "user", content: trimmed },
      { id: nextIdRef.current++, role: "assistant", content: STUB_REPLY },
    ]);
    setInput("");
  }, [input]);

  return (
    <>
      <button
        ref={fabRef}
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-label={isOpen ? "Close chat with Doxie" : "Open chat with Doxie"}
        aria-expanded={isOpen}
        aria-controls="chatbot-panel"
        className="fixed bottom-6 right-6 z-40 grid h-14 w-14 place-items-center rounded-full bg-gradient-to-br from-[var(--accent,#6366f1)] to-[var(--accent-2,#8b5cf6)] text-white shadow-lg shadow-[var(--accent,#6366f1)]/30 transition hover:scale-105 hover:brightness-110 hover:shadow-xl active:scale-95 focus:outline-none focus:ring-2 focus:ring-[var(--accent,#6366f1)]/40 focus:ring-offset-2"
      >
        {isOpen ? <X size={24} strokeWidth={2} /> : <DoxieIcon size={32} strokeWidth={2} />}
      </button>

      {isOpen ? (
        <ChatbotPanel
          ref={panelRef}
          messages={messages}
          input={input}
          onInputChange={setInput}
          onSend={handleSend}
          onClose={closePanel}
        />
      ) : null}
    </>
  );
}
