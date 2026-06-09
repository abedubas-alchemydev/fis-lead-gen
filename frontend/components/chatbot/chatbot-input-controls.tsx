"use client";

import { Loader2, Mic, Paperclip } from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent } from "react";

import { useToast } from "@/components/ui/use-toast";
import { ApiError, listVaultFolders, uploadVaultFile } from "@/lib/api";
import type { VaultFolder } from "@/lib/types";

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
const VAULT_UPLOAD_ACCEPT = ".pdf,.docx,.pptx,.xlsx,.txt,.md,.rtf,.csv,.html,.json";

export function ChatbotAttachButton({ disabled }: { disabled?: boolean }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [folders, setFolders] = useState<VaultFolder[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const targetFolderRef = useRef<number | null>(null);

  async function toggleMenu() {
    const next = !open;
    setOpen(next);
    if (next && folders === null && !loading) {
      setLoading(true);
      try {
        setFolders(await listVaultFolders());
      } catch {
        toast.error("Couldn't load your Vault folders.");
        setOpen(false);
      } finally {
        setLoading(false);
      }
    }
  }

  function pickFolder(folderId: number) {
    targetFolderRef.current = folderId;
    setOpen(false);
    fileInputRef.current?.click();
  }

  async function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // let the same file be re-picked later
    const folderId = targetFolderRef.current;
    if (!file || folderId === null) return;
    setUploading(true);
    try {
      await uploadVaultFile(folderId, file);
      toast.success(`Uploaded “${file.name}” to your Vault — it’s processing now.`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Upload failed. Try again.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="relative">
      <input
        ref={fileInputRef}
        type="file"
        accept={VAULT_UPLOAD_ACCEPT}
        onChange={onFileChange}
        hidden
      />
      <button
        type="button"
        onClick={toggleMenu}
        disabled={disabled || uploading}
        aria-label="Attach a document to your Vault"
        title="Attach a document to your Vault"
        aria-expanded={open}
        className={ICON_BUTTON_CLASS}
      >
        {uploading ? (
          <Loader2 size={16} strokeWidth={2} className="animate-spin" />
        ) : (
          <Paperclip size={16} strokeWidth={2} />
        )}
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute bottom-12 left-0 z-50 max-h-56 w-60 overflow-y-auto rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-1 shadow-xl"
        >
          <p className="px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted,#94a3b8)]">
            Upload to Vault folder
          </p>
          {loading ? (
            <p className="px-2 py-2 text-xs text-[var(--text-muted,#94a3b8)]">Loading folders…</p>
          ) : folders && folders.length > 0 ? (
            folders.map((folder) => (
              <button
                key={folder.id}
                type="button"
                role="menuitem"
                onClick={() => pickFolder(folder.id)}
                className="block w-full truncate rounded-lg px-2 py-1.5 text-left text-sm text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
              >
                {folder.name}
              </button>
            ))
          ) : (
            <p className="px-2 py-2 text-xs text-[var(--text-muted,#94a3b8)]">
              No Vault folders yet. Create one on the Vault page first.
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
