"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { ChevronDown, ListFilter, Search, X } from "lucide-react";

// Checkbox-dropdown multi-select for the master-list filter bar. Trigger
// matches the height + border treatment of `Combo` and the native <select>
// next to it; the popover anchors below the trigger with a search input on
// top, scrollable option list, and a "Clear all" footer that only renders
// when something is selected. Each option exposes an optional `count`
// rendered as a muted badge — handy for "X firms have this attribute"
// breakdowns. Visual contract is identical to the rest of the bar so the
// new filter doesn't look bolted on.
export interface MultiSelectFilterOption {
  value: string;
  label: string;
  count?: number;
}

export interface MultiSelectFilterProps {
  value: string[];
  onChange: (value: string[]) => void;
  options: ReadonlyArray<MultiSelectFilterOption>;
  triggerLabel: string;
  placeholder?: string;
  emptyLabel?: string;
  loading?: boolean;
  ariaLabel?: string;
  className?: string;
}

export function MultiSelectFilter({
  value,
  onChange,
  options,
  triggerLabel,
  placeholder = "Search…",
  emptyLabel = "No matches",
  loading = false,
  ariaLabel,
  className = "",
}: MultiSelectFilterProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Outside-click closes. mousedown so option-toggle clicks below win.
  useEffect(() => {
    function onDocumentMouseDown(event: globalThis.MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentMouseDown);
    return () => document.removeEventListener("mousedown", onDocumentMouseDown);
  }, []);

  // Auto-focus the search input when the popover opens.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () => (q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options),
    [options, q],
  );

  const selectedSet = useMemo(() => new Set(value), [value]);

  function toggle(option: string) {
    if (selectedSet.has(option)) {
      onChange(value.filter((v) => v !== option));
    } else {
      onChange([...value, option]);
    }
  }

  function clearAll() {
    onChange([]);
    setQuery("");
  }

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((next) => !next)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        className={`flex h-[38px] w-full items-center gap-2 rounded-[10px] border bg-[var(--surface,#ffffff)] px-3 text-[13px] transition ${
          open
            ? "border-[var(--accent,#6366f1)] shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            : "border-[var(--border,rgba(30,64,175,0.1))]"
        }`}
      >
        <ListFilter
          className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
          strokeWidth={2}
        />
        <span
          className={`min-w-0 flex-1 truncate text-left ${
            value.length > 0
              ? "text-[var(--text,#0f172a)]"
              : "text-[var(--text-muted,#94a3b8)]"
          }`}
        >
          {triggerLabel}
        </span>
        {value.length > 0 ? (
          <span className="inline-flex h-5 min-w-[20px] shrink-0 items-center justify-center rounded-full bg-[rgba(99,102,241,0.12)] px-1.5 text-[11px] font-bold text-[#4338ca]">
            {value.length}
          </span>
        ) : null}
        <ChevronDown
          className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
          strokeWidth={2}
        />
      </button>

      {open ? (
        <div className="absolute left-0 top-full z-10 mt-1 w-full max-w-[360px] overflow-hidden rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
          <div className="flex items-center gap-2 border-b border-[var(--border,rgba(30,64,175,0.1))] px-3 py-2">
            <Search
              className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
              strokeWidth={2}
            />
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={placeholder}
              aria-label={ariaLabel ? `${ariaLabel} search` : "Search options"}
              className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] outline-none placeholder:text-[var(--text-muted,#94a3b8)]"
            />
          </div>

          <ul role="listbox" className="max-h-64 overflow-auto py-1">
            {loading ? (
              <li className="px-3 py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
                Loading…
              </li>
            ) : filtered.length === 0 ? (
              <li className="px-3 py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
                {emptyLabel}
              </li>
            ) : (
              filtered.map((option) => {
                const checked = selectedSet.has(option.value);
                return (
                  <li key={option.value}>
                    <label
                      className={`flex cursor-pointer items-center gap-2.5 px-3 py-1.5 text-[13px] transition hover:bg-[var(--surface-2,#f1f6fd)] ${
                        checked
                          ? "text-[var(--text,#0f172a)]"
                          : "text-[var(--text-dim,#475569)]"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(option.value)}
                        className="h-4 w-4 shrink-0 rounded border-[var(--border-2,rgba(30,64,175,0.16))] text-[var(--accent,#6366f1)] focus:ring-[var(--accent,#6366f1)]"
                      />
                      <span className="min-w-0 flex-1 truncate">{option.label}</span>
                      {option.count !== undefined ? (
                        <span className="ml-auto inline-flex h-5 min-w-[24px] shrink-0 items-center justify-center rounded-full bg-[var(--surface-2,#f1f6fd)] px-1.5 text-[11px] font-medium text-[var(--text-muted,#94a3b8)]">
                          {option.count.toLocaleString()}
                        </span>
                      ) : null}
                    </label>
                  </li>
                );
              })
            )}
          </ul>

          {value.length > 0 ? (
            <div className="border-t border-[var(--border,rgba(30,64,175,0.1))] px-3 py-2 text-right">
              <button
                type="button"
                onClick={clearAll}
                className="inline-flex items-center gap-1 rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
              >
                <X className="h-3 w-3" strokeWidth={2} />
                Clear all
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
