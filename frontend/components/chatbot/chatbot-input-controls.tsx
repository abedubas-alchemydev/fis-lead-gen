"use client";

import { Mic, Paperclip } from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent } from "react";

const ICON_BUTTON_CLASS =
  "grid h-10 w-9 shrink-0 place-items-center rounded-xl text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-[var(--doxie,#6366f1)]/40";

// ── Voice input (Web Speech API) ────────────────────────────────────────
// Minimal shape for the non-standard SpeechRecognition API — only the bits
// we use are typed. The browser ctor lives on window under a vendor prefix
// in Chrome/Edge/Safari and is absent in Firefox, where the button hides.
interface SpeechResultEvent {
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
}
interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((event: SpeechResultEvent) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  start: () => void;
  stop: () => void;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function ChatbotVoiceButton({
  onTranscript,
  disabled,
}: {
  onTranscript: (text: string) => void;
  disabled?: boolean;
}) {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  // Keep the latest callback so the recognition handler (bound at start)
  // never fires into a stale closure.
  const cbRef = useRef(onTranscript);
  cbRef.current = onTranscript;

  useEffect(() => {
    setSupported(getSpeechRecognitionCtor() !== null);
    return () => recognitionRef.current?.stop();
  }, []);

  function toggle() {
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) return;
    const rec = new Ctor();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.continuous = false;
    rec.onresult = (event) => {
      const transcript = Array.from(event.results)
        .map((result) => result[0]?.transcript ?? "")
        .join(" ")
        .trim();
      if (transcript) cbRef.current(transcript);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recognitionRef.current = rec;
    setListening(true);
    rec.start();
  }

  if (!supported) return null;

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={disabled}
      aria-label={listening ? "Stop voice input" : "Start voice input"}
      title={listening ? "Stop voice input" : "Speak your message"}
      aria-pressed={listening}
      className={`${ICON_BUTTON_CLASS} ${listening ? "animate-pulse text-[var(--doxie,#6366f1)]" : ""}`}
    >
      <Mic size={16} strokeWidth={2} />
    </button>
  );
}

// ── Attach a document to the Vault ──────────────────────────────────────
// The paperclip now just *selects* a file and hands it to the chat widget,
// which runs the conversational "which folder?" flow and the actual upload.
// (Previously it uploaded inline; the widget owns that now so Doxie can ask
// where to file the document when no folder is named.)
export const VAULT_UPLOAD_ACCEPT = ".pdf,.docx,.pptx,.xlsx,.txt,.md,.rtf,.csv,.html,.json";

export function ChatbotAttachButton({
  onFile,
  disabled,
}: {
  onFile: (file: File) => void;
  disabled?: boolean;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // allow re-picking the same file later
    if (file) onFile(file);
  }

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept={VAULT_UPLOAD_ACCEPT}
        onChange={onFileChange}
        hidden
      />
      <button
        type="button"
        onClick={() => fileInputRef.current?.click()}
        disabled={disabled}
        aria-label="Attach a document to your Vault"
        title="Attach a document to your Vault"
        className={ICON_BUTTON_CLASS}
      >
        <Paperclip size={16} strokeWidth={2} />
      </button>
    </>
  );
}
