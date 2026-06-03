"use client";

import { useState } from "react";

import clsx from "clsx";
import { Sparkles } from "lucide-react";

import { buttonBase, buttonSizes } from "@/components/ui/button";
import { ApiError, optimizeOutreachInstructions } from "@/lib/api";

import { ExpandableTextarea } from "./expandable-textarea";

// Per-service "Outreach instructions" editor. Collapsed by default when
// no instructions are set so first-time users see a "+ Add instructions"
// CTA instead of a blank textarea — making the feature discoverable
// without forcing it on. Once any text exists (or the user clicks Add),
// the ExpandableTextarea (inline + fullscreen modal) renders.
//
// "Optimize Prompt" sends the typed text to Gemini Flash (via
// /outreach/optimize-instructions) and replaces it with a cleaned-up
// version — spelling/grammar fixed, wording sharpened. A one-step Undo
// restores the pre-optimize text. Shared by the create form and the
// detail editor, so the button shows up in both.

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
  const [optimizing, setOptimizing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Pre-optimize snapshot for a one-step Undo. null = nothing to undo.
  const [undoValue, setUndoValue] = useState<string | null>(null);

  async function handleOptimize() {
    const text = value.trim();
    if (!text || optimizing || disabled) return;
    setOptimizing(true);
    setError(null);
    try {
      const result = await optimizeOutreachInstructions({ text });
      setUndoValue(value);
      onChange(result.optimized_text);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Could not optimize the instructions.");
      }
    } finally {
      setOptimizing(false);
    }
  }

  function handleUndo() {
    if (undoValue === null) return;
    onChange(undoValue);
    setUndoValue(null);
  }

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
    <div>
      <ExpandableTextarea
        label="Outreach instructions"
        value={value}
        onChange={onChange}
        maxLength={maxLength}
        disabled={disabled}
        helperText="Permanent prompt guidance — the AI follows this on every draft for this service."
      />
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void handleOptimize()}
          disabled={disabled || optimizing || value.trim().length === 0}
          className={clsx(
            buttonBase,
            buttonSizes.sm,
            "border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--accent,#6366f1)] hover:bg-[var(--surface-2,#f1f6fd)]"
          )}
        >
          <Sparkles className="h-3.5 w-3.5" aria-hidden />
          {optimizing ? "Optimizing..." : "Optimize Prompt"}
        </button>
        {undoValue !== null && !optimizing ? (
          <button
            type="button"
            onClick={handleUndo}
            disabled={disabled}
            className="text-xs font-medium text-[var(--text-muted,#94a3b8)] underline-offset-4 transition hover:text-[var(--text,#0f172a)] hover:underline disabled:cursor-not-allowed disabled:opacity-60"
          >
            Undo
          </button>
        ) : null}
        <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
          Fixes spelling &amp; grammar and sharpens your instructions with AI.
        </span>
      </div>
      {error ? (
        <div className="mt-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      ) : null}
    </div>
  );
}
