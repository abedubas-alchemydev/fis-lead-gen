"use client";

// Reusable "Rows per page" selector. Extracted so the email-extractor
// results table (scan-detail-view) and the Saved Contacts page share one
// control with identical styling + option set. Styling mirrors the search
// <input> in scan-detail-view (var(--border,…) CSS-var classes) so the two
// controls read as a matched pair in the same toolbar.

// A page size is either a concrete row count or the "all" sentinel, which
// callers resolve to an effective size (usually the total row count) so the
// slice returns everything and the pager hides.
export type RowsPerPageValue = number | "all";

// Default option set — 25 / 50 / 100 / All. Exported so callers can pass a
// custom list while still defaulting to the standard scale.
export const DEFAULT_ROWS_PER_PAGE_OPTIONS: readonly RowsPerPageValue[] = [
  25, 50, 100, "all",
];

function optionLabel(value: RowsPerPageValue): string {
  return value === "all" ? "All" : String(value);
}

export function RowsPerPageSelect({
  value,
  onChange,
  options = DEFAULT_ROWS_PER_PAGE_OPTIONS,
  id = "rows-per-page",
  className,
}: {
  value: RowsPerPageValue;
  onChange: (next: RowsPerPageValue) => void;
  options?: readonly RowsPerPageValue[];
  // Unique id so the visible label's htmlFor points at THIS select when the
  // control appears more than once on a page.
  id?: string;
  className?: string;
}): React.ReactElement {
  return (
    <div
      className={`inline-flex items-center gap-2 text-[12px] text-[var(--text-muted,#94a3b8)]${
        className ? ` ${className}` : ""
      }`}
    >
      <label htmlFor={id} className="whitespace-nowrap">
        Rows per page
      </label>
      <select
        id={id}
        value={String(value)}
        onChange={(e) =>
          onChange(e.target.value === "all" ? "all" : Number(e.target.value))
        }
        className="rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] py-1.5 pl-2.5 pr-7 text-[13px] text-[var(--text,#0f172a)] focus:border-[var(--blue,#3b82f6)] focus:outline-none focus:ring-2 focus:ring-[rgba(59,130,246,0.2)]"
      >
        {options.map((opt) => (
          <option key={String(opt)} value={String(opt)}>
            {optionLabel(opt)}
          </option>
        ))}
      </select>
    </div>
  );
}
