"use client";

import { useState } from "react";

import { ExpandableTextarea } from "./expandable-textarea";

// Per-service "Outreach instructions" editor. Collapsed by default when
// no instructions are set so first-time users see a "+ Add instructions"
// CTA instead of a blank textarea — making the feature discoverable
// without forcing it on. Once any text exists (or the user clicks Add),
// the ExpandableTextarea (inline + fullscreen modal) renders.

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
    <ExpandableTextarea
      label="Outreach instructions"
      value={value}
      onChange={onChange}
      maxLength={maxLength}
      disabled={disabled}
      placeholder='e.g. "Keep emails under 100 words. Always mention 24-hour turnaround. Tone: formal, never casual."'
      helperText="Permanent prompt guidance — the AI follows this on every draft for this service."
    />
  );
}
