"use client";

import { useEffect, useState } from "react";
import { Sparkles, X } from "lucide-react";

// Dismissible "enrichment in progress" notice shown at the top of the Banks
// list (/banks). Purely informational: bank profiles are continuously
// enriched (contacts, details) in the background, so this reassures
// first-time visitors that data is still filling in — and lets them close
// it for good. Dismissal persists in localStorage; bump the key suffix
// (":v1" → ":v2") to re-surface the notice after a future enrichment wave.
const DISMISS_KEY = "banks-enrichment-notice-dismissed:v1";

export function BankEnrichmentNotice() {
  // Start hidden so the server render and the first client paint agree (no
  // hydration mismatch); reveal after mount only when not previously dismissed.
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      if (window.localStorage.getItem(DISMISS_KEY) !== "1") {
        setVisible(true);
      }
    } catch {
      // localStorage can throw (private mode / blocked storage) — degrade to
      // showing the notice rather than crashing the page.
      setVisible(true);
    }
  }, []);

  if (!visible) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      // Ignore — worst case the notice reappears on the next load.
    }
    setVisible(false);
  };

  return (
    <div
      role="status"
      aria-live="polite"
      className="animate-fade-in mb-4 flex items-start gap-3 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-4 py-3.5"
    >
      <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[rgba(99,102,241,0.12)] text-[var(--accent,#6366f1)]">
        <Sparkles className="h-4 w-4" strokeWidth={2} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[13.5px] font-semibold text-[var(--text,#0f172a)]">
          Banks data enrichment is ongoing
        </p>
        <p className="mt-0.5 text-[12px] text-[var(--text-dim,#475569)]">
          We&apos;re continuously enriching bank profiles with contacts and
          details in the background. Keep browsing — new data appears
          automatically as it&apos;s found.
        </p>
        {/* Indeterminate loader bar — a moving accent segment signalling the
            ongoing background work. Falls back to a static partial bar under
            prefers-reduced-motion (the animation binding lives inside the
            reduced-motion guard in globals.css). */}
        <div className="relative mt-2.5 h-1.5 w-full max-w-[420px] overflow-hidden rounded-full bg-[rgba(99,102,241,0.16)]">
          <div
            aria-hidden
            className="animate-indeterminate absolute inset-y-0 left-0 w-1/4 rounded-full bg-[var(--accent,#6366f1)]"
          />
        </div>
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss enrichment notice"
        className="shrink-0 rounded-lg p-1 text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface,#ffffff)] hover:text-[var(--text,#0f172a)]"
      >
        <X className="h-4 w-4" strokeWidth={2} aria-hidden />
      </button>
    </div>
  );
}
