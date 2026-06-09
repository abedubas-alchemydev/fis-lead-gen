"use client";

import { X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  createVaultFolder,
  listVaultFolders,
  loadDoxieHistory,
  startNewDoxieChat,
  streamDoxieMessage,
  uploadVaultFile,
  type DoxieChatMessage,
  type DoxieStreamEvent
} from "@/lib/api";
import type { VaultFolder } from "@/lib/types";

import { ChatbotPanel, type ChatbotPanelHandle } from "./chatbot-panel";
import type { ChatMessage } from "./chatbot-message";

// Resolve which Vault folder the user named in free text. Prefers an exact
// (case-insensitive) name match, then the longest folder name contained in
// the text — so "Test" doesn't win inside "E2E Test Service".
function findFolderInText(folders: VaultFolder[], text: string): VaultFolder | null {
  const lower = text.trim().toLowerCase();
  if (!lower) return null;
  const exact = folders.find((f) => f.name?.toLowerCase() === lower);
  if (exact) return exact;
  const contained = folders
    .filter((f) => f.name && lower.includes(f.name.toLowerCase()))
    .sort((a, b) => b.name.length - a.name.length);
  return contained[0] ?? null;
}

const WELCOME_MESSAGE: ChatMessage = {
  id: 0,
  role: "assistant",
  content: "Hi! I'm Doxie 👋 Ask me anything about the app or the data you're looking at.",
  // Static greeting — not streamed, so render with markdown immediately.
  isFinalized: true
};

// Human-readable status labels shown while a tool is running. Falls back
// to a generic "Looking things up…" for any new tool the FE doesn't
// recognize so a backend addition doesn't break the UI before the FE
// catches up.
const TOOL_STATUS_LABELS: Record<string, string> = {
  search_broker_dealers: "Searching broker-dealers…",
  get_broker_dealer_profile: "Loading broker-dealer profile…",
  search_investment_advisors: "Searching investment advisors…",
  get_investment_advisor_profile: "Loading advisor profile…",
  search_institutional_investors: "Searching institutional investors…",
  get_institutional_investor_profile: "Loading investor profile…",
  list_broker_dealers_by_filter: "Filtering broker-dealers…",
  list_investment_advisors_by_filter: "Filtering investment advisors…",
  draft_outreach_email: "Drafting outreach email…"
};

function toolStatusFor(name: string): string {
  return TOOL_STATUS_LABELS[name] ?? "Looking things up…";
}

function errorMessageFor(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 503) {
      return "Doxie is offline right now — the chat service isn't configured on this environment.";
    }
    if (error.status === 502) {
      return "Doxie couldn't get a reply from the AI just now. Mind trying again?";
    }
    if (error.status === 413) {
      return "This conversation is too long — start a new chat and try again.";
    }
    if (error.status === 500) {
      return "Doxie answered but the message couldn't be saved. Try again in a moment.";
    }
    if (error.status === 401) {
      return "Your session expired. Refresh the page to sign back in.";
    }
  }
  return "Something went wrong reaching Doxie. Please try again.";
}

// Map a stream-event error code to a user-friendly fallback message.
// In-stream errors land here AFTER the SSE response was already 200, so
// we can't surface them as HTTP status codes — they replace the pending
// bubble's content with an inline message.
function streamErrorMessageFor(code: string, message: string): string {
  if (code === "config") {
    return "Doxie is offline right now — the chat service isn't configured on this environment.";
  }
  if (code === "persistence") {
    return "Doxie answered but the message couldn't be saved. Try again in a moment.";
  }
  return message || "Doxie hit a snag mid-reply. Please try again.";
}

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
  const [isSending, setIsSending] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const historyLoadedRef = useRef(false);

  // Conversational Vault upload: a file attached via the paperclip waits
  // here until the user names a folder (or Doxie asks for one).
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [awaitingFolder, setAwaitingFolder] = useState(false);
  const foldersRef = useRef<VaultFolder[] | null>(null);

  const fabRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<ChatbotPanelHandle>(null);
  const nextIdRef = useRef(1);

  // Page context for Doxie. ``usePathname`` is reactive across route
  // changes; document.title is read at send-time so a freshly-rendered
  // page reflects in the next prompt without a re-render of the widget.
  const pathname = usePathname();

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

  // Lazy-load persisted history on first panel open.
  useEffect(() => {
    if (!isOpen || historyLoadedRef.current) return;
    historyLoadedRef.current = true;

    let cancelled = false;
    setIsLoadingHistory(true);
    void (async () => {
      try {
        const history = await loadDoxieHistory();
        if (cancelled) return;
        if (history.messages.length === 0) {
          return;
        }
        const restored: ChatMessage[] = history.messages.map((m) => {
          const id = nextIdRef.current++;
          // Historical assistant messages already went through the full
          // stream → done flow on their original turn; mark them
          // finalized so markdown renders + deep-links work immediately.
          return { id, role: m.role, content: m.content, isFinalized: true };
        });
        setMessages(restored);
      } catch (error) {
        if (cancelled) return;
        // eslint-disable-next-line no-console
        console.warn("Doxie history load failed", error);
      } finally {
        if (!cancelled) setIsLoadingHistory(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  const handleAttachFile = useCallback((file: File) => {
    setPendingFile(file);
    setAwaitingFolder(false);
    setIsOpen(true);
    // Warm the folder cache so the "which folder?" step is instant.
    if (!foldersRef.current) {
      listVaultFolders()
        .then((f) => {
          foldersRef.current = f;
        })
        .catch(() => {
          /* will be fetched again on send if needed */
        });
    }
  }, []);

  const clearPendingFile = useCallback(() => {
    setPendingFile(null);
    setAwaitingFolder(false);
  }, []);

  const handleSend = useCallback(async () => {
    if (isSending) return;
    const trimmed = input.trim();

    // ── Conversational Vault upload ──────────────────────────────────
    // A file is attached (paperclip). Its bytes can't ride inside a chat
    // message, so we file it via the Vault API here — but present it as a
    // back-and-forth: name a folder and Doxie files it; omit one and it asks.
    if (pendingFile) {
      const file = pendingFile;
      const echo =
        trimmed || (awaitingFolder ? "" : `Upload "${file.name}" to my Vault.`);
      if (echo) {
        setMessages((prev) => [
          ...prev,
          { id: nextIdRef.current++, role: "user", content: echo, localOnly: true },
        ]);
      }
      setInput("");
      setIsSending(true);

      const appendAssistant = (content: string) =>
        setMessages((prev) => [
          ...prev,
          {
            id: nextIdRef.current++,
            role: "assistant",
            content,
            isFinalized: true,
            localOnly: true,
          },
        ]);

      const fileInto = async (folder: VaultFolder) => {
        try {
          await uploadVaultFile(folder.id, file);
          appendAssistant(
            `✅ Filed **${file.name}** into **${folder.name}** — it's processing now and I'll be able to read it shortly.`
          );
        } catch (err) {
          appendAssistant(
            err instanceof ApiError
              ? `I couldn't upload that: ${err.message}`
              : "I couldn't upload that file — please try again."
          );
        } finally {
          setPendingFile(null);
          setAwaitingFolder(false);
        }
      };

      try {
        let folders = foldersRef.current;
        if (!folders) {
          folders = await listVaultFolders();
          foldersRef.current = folders;
        }

        const newMatch = trimmed.match(/^new:\s*(.+)$/i);
        if (newMatch) {
          const created = await createVaultFolder({
            name: newMatch[1].trim(),
            description: "",
          });
          foldersRef.current = [...folders, created];
          await fileInto(created);
          return;
        }

        const named = trimmed ? findFolderInText(folders, trimmed) : null;
        if (named) {
          await fileInto(named);
          return;
        }

        if (folders.length === 0) {
          appendAssistant(
            `I'll file **${file.name}** for you, but you don't have any Vault folders yet. Reply with **new: My Folder** and I'll create it and upload there.`
          );
        } else {
          const names = folders.map((f) => `**${f.name}**`).join(", ");
          appendAssistant(
            `Which Vault folder should I file **${file.name}** into? Your folders: ${names}. Reply with a folder name, or **new: My Folder** to create one.`
          );
        }
        setAwaitingFolder(true);
      } catch (err) {
        appendAssistant(
          err instanceof ApiError
            ? `I couldn't reach your Vault: ${err.message}`
            : "Something went wrong reaching your Vault — please try again."
        );
      } finally {
        setIsSending(false);
      }
      return;
    }

    // ── Normal Doxie chat ────────────────────────────────────────────
    if (trimmed.length === 0) return;

    const userMessageId = nextIdRef.current++;
    const pendingId = nextIdRef.current++;
    const userMessage: ChatMessage = { id: userMessageId, role: "user", content: trimmed };
    const pendingMessage: ChatMessage = {
      id: pendingId,
      role: "assistant",
      content: "",
      pending: true
    };

    // Capture the history sent to the BE BEFORE we render the pending
    // placeholder — the placeholder must not go into the wire payload.
    const historyForApi: DoxieChatMessage[] = [
      ...messages
        .filter((m) => m.id !== WELCOME_MESSAGE.id && !m.error && !m.pending && !m.localOnly)
        .map((m) => ({ role: m.role, content: m.content })),
      { role: "user", content: trimmed }
    ];

    setMessages((prev) => [...prev, userMessage, pendingMessage]);
    setInput("");
    setIsSending(true);

    function patchPending(updater: (current: ChatMessage) => ChatMessage) {
      setMessages((prev) =>
        prev.map((m) => (m.id === pendingId ? updater(m) : m))
      );
    }

    function handleEvent(event: DoxieStreamEvent) {
      switch (event.type) {
        case "tool_call":
          patchPending((m) => ({ ...m, toolStatus: toolStatusFor(event.name) }));
          break;
        case "tool_result":
          // Clear the per-tool label; if another tool_call follows it'll
          // replace it. A "done" or first text_delta finalises pending.
          patchPending((m) => ({ ...m, toolStatus: undefined }));
          break;
        case "text_delta":
          patchPending((m) => ({
            ...m,
            // First delta clears pending so the bubble switches from
            // dots to streaming text.
            pending: false,
            toolStatus: undefined,
            content: m.content + event.text
          }));
          break;
        case "done":
          patchPending((m) => ({
            ...m,
            pending: false,
            toolStatus: undefined,
            // If no deltas streamed (e.g. iteration cap fallback), the
            // ``reply`` carries the final text — use it.
            content: m.content || event.reply,
            // Flip to finalised so the bubble re-renders with markdown
            // + clickable deep-links. Doing it only here (not on each
            // text_delta) is what prevents mid-stream half-link flicker.
            isFinalized: true
          }));
          break;
        case "error":
          patchPending(() => ({
            id: pendingId,
            role: "assistant",
            content: streamErrorMessageFor(event.code, event.message),
            error: true
          }));
          break;
      }
    }

    try {
      await streamDoxieMessage({
        messages: historyForApi,
        pageContext: {
          path: pathname ?? undefined,
          title: typeof document !== "undefined" ? document.title : undefined
        },
        onEvent: handleEvent
      });
    } catch (error) {
      // Network / HTTP-level failure before / during the stream — the BE
      // never sent a ``done`` event so the pending bubble still shows
      // dots. Replace it with an error message.
      patchPending(() => ({
        id: pendingId,
        role: "assistant",
        content: errorMessageFor(error),
        error: true
      }));
    } finally {
      setIsSending(false);
    }
  }, [input, isSending, messages, pathname, pendingFile, awaitingFolder]);

  const handleNewChat = useCallback(async () => {
    if (isSending) return;
    const previous = messages;
    setMessages([WELCOME_MESSAGE]);
    setInput("");
    setPendingFile(null);
    setAwaitingFolder(false);
    try {
      await startNewDoxieChat();
    } catch (error) {
      setMessages([
        ...previous,
        {
          id: nextIdRef.current++,
          role: "assistant",
          content: errorMessageFor(error),
          error: true
        }
      ]);
    }
  }, [isSending, messages]);

  return (
    <>
      <button
        ref={fabRef}
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-label={isOpen ? "Close chat with Doxie" : "Open chat with Doxie"}
        aria-expanded={isOpen}
        aria-controls="chatbot-panel"
        className="fixed bottom-6 right-6 z-40 grid h-14 w-14 place-items-center rounded-full bg-gradient-to-br from-[var(--doxie,#6366f1)] to-[var(--doxie-2,#8b5cf6)] text-white shadow-lg shadow-[var(--doxie,#6366f1)]/30 transition hover:scale-105 hover:brightness-110 hover:shadow-xl active:scale-95 focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40 focus:ring-offset-2"
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
          onNewChat={handleNewChat}
          isSending={isSending}
          isLoadingHistory={isLoadingHistory}
          pendingFileName={pendingFile?.name ?? null}
          onAttachFile={handleAttachFile}
          onClearPendingFile={clearPendingFile}
          // Clicking a Doxie-emitted deep-link should collapse the
          // panel so the user can see the destination page. Cmd/Ctrl/
          // shift-click and middle-click bypass this and open in a new
          // tab — see ChatLink in chatbot-message.tsx.
          onInternalNavigate={closePanel}
        />
      ) : null}
    </>
  );
}
