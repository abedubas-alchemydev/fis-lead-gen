"use client";

import { useState } from "react";

// Per-service "Outreach instructions" editor. Collapsed by default when
// no instructions are set so first-time users see a "+ Add instructions"
// CTA instead of a blank textarea — making the feature discoverable
// without forcing it on. Once any text exists (or the user clicks Add),
// the textarea + char counter render normally.

interface VaultInstructionsEditorProps {
  value: string;
  onChange: (value: string) => void;
  maxLength?: number;
  disabled?: boolean;
}

const DEFAULT_MAX = 10_000;

export function VaultInstructionsEditor({
  value,
  onChange,
  maxLength = DEFAULT_MAX,
  disabled = false
}: VaultInstructionsEditorProps) {
  const initiallyExpanded = value.length > 0;
  const [expanded, setExpanded] = useState(initiallyExpanded);

  if (!expanded) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-2,#f1f6fd)]/60 px-4 py-3">
        <button
          type="button"
          onClick={() => setExpanded(true)}
          disabled={disabled}
          className="text-xs font-medium text-[var(--accent,#6366f1)] underline-offset-4 transition hover:underline disabled:cursor-not-allowed disabled:opacity-60"
        >
          + Add outreach instructions
        </button>
        <p className="mt-1 text-[11px] leading-4 text-[var(--text-muted,#94a3b8)]">
          Optional permanent guidance the AI follows on every draft for this
          service — e.g. tone, length caps, must-mention items.
        </p>
      </div>
    );
  }

  return (
    <label className="block text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
      Outreach instructions
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        maxLength={maxLength}
        rows={4}
        disabled={disabled}
        placeholder='e.g. "Keep emails under 100 words. Always mention 24-hour turnaround. Tone: formal, never casual."'
        className="mt-2 block w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm leading-6 text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20 disabled:cursor-not-allowed disabled:opacity-60"
      />
      <span className="mt-1 block text-[11px] text-[var(--text-muted,#94a3b8)]">
        Permanent prompt guidance — the AI follows this on every draft for
        this service. {value.length.toLocaleString()} /{" "}
        {maxLength.toLocaleString()} characters.
      </span>
    </label>
  );
}
