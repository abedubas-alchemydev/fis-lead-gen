import type { ReactNode } from "react";

// Inline mini-stat card for the public share profiles — used inside the
// Financials / Assessment / Profile / Overview panels for the small
// key-value tiles. Compact mode tightens the vertical rhythm so 4-up grids
// keep their footprint.
//
// Deliberately a per-surface copy of the MiniStat helper inside
// broker-dealer-detail-client.tsx (~line 1590): the codebase keeps one copy
// per detail surface rather than a shared primitive, and the markup /
// classes mirror that original verbatim so share tiles render identically
// to the authed pages.
export function MiniStat({
  label,
  value,
  helper,
  valueClassName,
  compact,
}: {
  label: string;
  // Widened from `string` so callers can render a custom node (e.g. a
  // Copyable-wrapped value) when the underlying value needs wrapping.
  value: ReactNode;
  helper?: string;
  valueClassName?: string;
  compact?: boolean;
}) {
  return (
    <div className={`rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 ${compact ? "py-3" : "py-4"} text-sm`}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </p>
      <p
        className={`mt-1 ${
          compact
            ? "text-[13px] text-[var(--text,#0f172a)]"
            : "text-[18px] font-semibold tabular-nums text-[var(--text,#0f172a)]"
        } ${valueClassName ?? ""}`}
      >
        {value}
      </p>
      {helper ? <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">{helper}</p> : null}
    </div>
  );
}
