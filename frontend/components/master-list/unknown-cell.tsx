"use client";

import type { ReactNode } from "react";
import { Info } from "lucide-react";

import { unknownReasonShort } from "@/lib/format";
import type { UnknownReason } from "@/lib/types";

// Notes can run long when the BE captures the full extraction narrative.
// 240 chars keeps the tooltip readable inside a table cell without
// pushing the layout. Anything longer is clipped with an ellipsis.
const NOTE_TRUNCATE = 240;

interface UnknownCellProps {
  reason?: UnknownReason | null;
  // Text rendered in place of the missing value. Defaults to "Unknown" so
  // most callers can drop UnknownCell in without thinking; pass `—` (or a
  // styled span) to match the surrounding cell's existing placeholder.
  fallback?: ReactNode;
  // Shrinks the icon + tooltip a touch for inline use inside pill rows
  // or stat cards where the standard size feels heavy.
  compact?: boolean;
}

// Inline cell that explains why a master-list / firm-detail field is
// "Unknown". When the BE supplies an `unknown_reason`, the cell renders
// the fallback text plus a small ⓘ icon; on hover or keyboard focus the
// categorized reason + (optionally) the BE's free-text note pops above
// the icon. When `reason` is null/undefined the cell falls back to plain
// text so the FE keeps working before cli01's BE contract ships.
export function UnknownCell({
  reason,
  fallback = "Unknown",
  compact = false,
}: UnknownCellProps) {
  if (!reason) {
    return (
      <span className="text-[var(--text-muted,#94a3b8)]">{fallback}</span>
    );
  }

  const shortLabel = unknownReasonShort(reason);
  const note =
    reason.note && reason.note.length > NOTE_TRUNCATE
      ? reason.note.slice(0, NOTE_TRUNCATE) + "…"
      : reason.note;

  const iconSize = compact ? "h-3 w-3" : "h-3.5 w-3.5";

  return (
    <span className="group relative inline-flex items-center gap-1 text-[var(--text-muted,#94a3b8)]">
      {fallback}
      <button
        type="button"
        aria-label={`Why is this Unknown? ${shortLabel}`}
        className="inline-flex cursor-help items-center rounded-full p-0.5 text-[var(--text-muted,#94a3b8)] outline-none transition hover:text-[var(--text-dim,#475569)] focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]"
      >
        <Info className={`${iconSize} opacity-70`} strokeWidth={2} aria-hidden />
      </button>
      {/*
        Self-contained CSS-driven tooltip — no shadcn / radix in this
        repo, and the spec forbids adding a new tooltip library or
        touching components/ui. group-hover and group-focus-within
        cover mouse + keyboard without React state.
      */}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 hidden w-max max-w-xs -translate-x-1/2 rounded-lg border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-left text-[12px] leading-5 text-[var(--text,#0f172a)] shadow-[0_10px_28px_rgba(15,23,42,0.18)] group-hover:block group-focus-within:block"
      >
        <span className="block font-semibold">{shortLabel}</span>
        {note ? (
          <span className="mt-1 block text-[11px] text-[var(--text-dim,#475569)]">
            {note}
          </span>
        ) : null}
      </span>
    </span>
  );
}
