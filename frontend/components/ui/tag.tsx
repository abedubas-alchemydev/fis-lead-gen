import type { ReactNode } from "react";

import { X } from "lucide-react";

// Matches `.tag` in design-system.css — 11.5px / weight 500 / surface-2 bg,
// pill-shape, optional dismiss button inside. Used by the master-list
// active-chips tray to summarize applied filters.
export interface TagProps {
  children: ReactNode;
  onDismiss?: () => void;
  className?: string;
  // Plain-text label surfaced as a native tooltip when the chip truncates.
  // Callers that pass mixed JSX in `children` should set this to the full
  // human-readable string so hover reveals the untruncated value.
  title?: string;
}

export function Tag({ children, onDismiss, className = "", title }: TagProps) {
  return (
    <span
      className={`inline-flex max-w-[280px] items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-1 text-[11.5px] font-medium text-[var(--text-dim,#475569)] ${className}`}
    >
      <span className="min-w-0 flex-1 truncate" title={title}>
        {children}
      </span>
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Remove filter"
          className="-mr-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full text-[var(--text-muted,#94a3b8)] opacity-60 transition hover:text-[var(--red,#ef4444)] hover:opacity-100"
        >
          <X className="h-3 w-3" strokeWidth={2} />
        </button>
      ) : null}
    </span>
  );
}
