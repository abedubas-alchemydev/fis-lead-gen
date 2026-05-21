"use client";

import { useEffect, useRef, useState } from "react";

import { Maximize2, X } from "lucide-react";

// Inline textarea with an "Expand" affordance that opens a fullscreen
// modal editor. Edits stream through onChange in both views so the
// outer form's dirty state and Save button keep working unchanged —
// the modal has no separate save path.

interface ExpandableTextareaProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  maxLength: number;
  rows?: number;
  placeholder?: string;
  helperText?: string;
  disabled?: boolean;
}

export function ExpandableTextarea({
  label,
  value,
  onChange,
  maxLength,
  rows = 10,
  placeholder,
  helperText,
  disabled = false
}: ExpandableTextareaProps) {
  const [expanded, setExpanded] = useState(false);
  const inputId = `expandable-textarea-${label.replace(/\s+/g, "-").toLowerCase()}`;

  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <label
          htmlFor={inputId}
          className="text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]"
        >
          {label}
        </label>
        <button
          type="button"
          onClick={() => setExpanded(true)}
          disabled={disabled}
          aria-label={`Expand ${label.toLowerCase()} editor`}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--accent,#6366f1)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Maximize2 className="h-3.5 w-3.5" aria-hidden />
          Expand
        </button>
      </div>
      <textarea
        id={inputId}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        maxLength={maxLength}
        rows={rows}
        disabled={disabled}
        placeholder={placeholder}
        className="mt-2 block w-full min-h-[180px] resize-y rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm leading-6 text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20 disabled:cursor-not-allowed disabled:opacity-60"
      />
      <span className="mt-1 block text-[11px] text-[var(--text-muted,#94a3b8)]">
        {helperText ? <>{helperText}{" "}</> : null}
        {value.length.toLocaleString()} / {maxLength.toLocaleString()} characters{helperText ? "." : ""}
      </span>

      {expanded ? (
        <ExpandedEditor
          label={label}
          value={value}
          onChange={onChange}
          maxLength={maxLength}
          placeholder={placeholder}
          helperText={helperText}
          disabled={disabled}
          onClose={() => setExpanded(false)}
        />
      ) : null}
    </div>
  );
}

function ExpandedEditor({
  label,
  value,
  onChange,
  maxLength,
  placeholder,
  helperText,
  disabled,
  onClose
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  maxLength: number;
  placeholder?: string;
  helperText?: string;
  disabled: boolean;
  onClose: () => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const titleId = `expandable-textarea-modal-${label.replace(/\s+/g, "-").toLowerCase()}-title`;

  // Focus the textarea and put the cursor at the end so the user can
  // keep typing where they left off rather than fighting selection.
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.focus();
    const end = ta.value.length;
    ta.setSelectionRange(end, end);
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={onClose}
        className="absolute inset-0 bg-[rgba(15,23,42,0.55)] backdrop-blur-sm"
      />
      <div className="relative flex h-[85vh] w-full max-w-4xl flex-col rounded-2xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] shadow-[0_24px_48px_-16px_rgba(15,23,42,0.35)]">
        <header className="flex items-center justify-between border-b border-[var(--border,rgba(30,64,175,0.1))] px-5 py-4">
          <h2
            id={titleId}
            className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]"
          >
            {label}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close editor"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </header>
        <div className="flex-1 overflow-hidden px-5 py-4">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            maxLength={maxLength}
            disabled={disabled}
            placeholder={placeholder}
            aria-label={label}
            className="block h-full w-full resize-none rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3 text-sm leading-6 text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20 disabled:cursor-not-allowed disabled:opacity-60"
          />
        </div>
        <footer className="flex items-center justify-between gap-3 border-t border-[var(--border,rgba(30,64,175,0.1))] px-5 py-4">
          <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
            {helperText ? <>{helperText}{" "}</> : null}
            {value.length.toLocaleString()} / {maxLength.toLocaleString()} characters{helperText ? "." : ""}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 items-center rounded-lg bg-[var(--accent,#6366f1)] px-4 text-xs font-semibold text-white transition hover:bg-[var(--accent-2,#8b5cf6)]"
          >
            Done
          </button>
        </footer>
      </div>
    </div>
  );
}
