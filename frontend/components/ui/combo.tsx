"use client";

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { ChevronDown, Search, X } from "lucide-react";

// Searchable single-select matching the mockup's `.combo` + `.quick-chips`
// primitives. Input displays the current value when closed; typing opens
// the dropdown and filters options; Enter commits the active option.
// Optional `quickChips` row underneath offers one-click selection.
// Empty string (`""`) represents "no selection" — the `emptyLabel` row
// inside the dropdown clears the value.
export interface ComboProps {
  value: string;
  onChange: (value: string) => void;
  options: ReadonlyArray<string>;
  quickChips?: ReadonlyArray<string>;
  placeholder?: string;
  ariaLabel?: string;
  emptyLabel?: string;
  className?: string;
}

export function Combo({
  value,
  onChange,
  options,
  quickChips,
  placeholder = "Search…",
  ariaLabel,
  emptyLabel = "All",
  className = "",
}: ComboProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    function onDocumentMouseDown(event: globalThis.MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentMouseDown);
    return () => document.removeEventListener("mousedown", onDocumentMouseDown);
  }, []);

  const q = query.trim().toLowerCase();
  const filtered = q ? options.filter((o) => o.toLowerCase().includes(q)) : options;

  function pick(next: string) {
    onChange(next);
    setQuery("");
    setOpen(false);
    setActiveIdx(0);
  }

  function clear() {
    onChange("");
    setQuery("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      if (filtered.length > 0) setActiveIdx((i) => (i + 1) % filtered.length);
    } else if (event.key === "ArrowUp") {
      if (filtered.length === 0) return;
      event.preventDefault();
      setActiveIdx((i) => (i - 1 + filtered.length) % filtered.length);
    } else if (event.key === "Enter") {
      if (filtered.length === 0) return;
      event.preventDefault();
      pick(filtered[activeIdx]);
    } else if (event.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  }

  // When open, let the user type. When closed, show the committed value.
  const inputValue = open ? query : value;

  return (
    <div ref={rootRef} className={className}>
      <div
        onClick={() => {
          setOpen(true);
          inputRef.current?.focus();
        }}
        className={`relative flex items-center gap-2 rounded-[10px] border bg-[var(--surface,#ffffff)] px-3.5 py-2 transition ${
          open
            ? "border-[var(--accent,#6366f1)] shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            : "border-[var(--border,rgba(30,64,175,0.1))]"
        }`}
      >
        <Search className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]" strokeWidth={2} />
        <input
          ref={inputRef}
          value={inputValue}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
            setActiveIdx(0);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder={value ? "" : placeholder}
          aria-label={ariaLabel}
          className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] placeholder:text-[var(--text-muted,#94a3b8)] outline-none"
        />
        {value && !open ? (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              clear();
            }}
            aria-label="Clear selection"
            className="grid h-4 w-4 shrink-0 place-items-center rounded-full text-[var(--text-muted,#94a3b8)] opacity-60 hover:text-[var(--red,#ef4444)] hover:opacity-100"
          >
            <X className="h-3 w-3" strokeWidth={2} />
          </button>
        ) : null}
        <ChevronDown className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]" strokeWidth={2} />

        {open ? (
          <ul
            role="listbox"
            className="absolute left-0 right-0 top-full z-10 mt-1 max-h-56 overflow-auto rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] py-1 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]"
          >
            <li
              role="option"
              aria-selected={value === ""}
              onMouseDown={(event) => {
                event.preventDefault();
                pick("");
              }}
              className={`cursor-pointer px-3 py-1.5 text-[13px] ${
                value === ""
                  ? "bg-[var(--surface-2,#f1f6fd)] text-[var(--text,#0f172a)]"
                  : "text-[var(--text-dim,#475569)]"
              }`}
            >
              {emptyLabel}
            </li>
            {filtered.map((opt, idx) => (
              <li
                key={opt}
                role="option"
                aria-selected={idx === activeIdx}
                onMouseDown={(event) => {
                  event.preventDefault();
                  pick(opt);
                }}
                onMouseEnter={() => setActiveIdx(idx)}
                className={`cursor-pointer px-3 py-1.5 text-[13px] ${
                  idx === activeIdx
                    ? "bg-[var(--surface-2,#f1f6fd)] text-[var(--text,#0f172a)]"
                    : "text-[var(--text-dim,#475569)]"
                }`}
              >
                {opt}
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {quickChips && quickChips.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {quickChips.map((chip) => {
            const selected = value === chip;
            return (
              <button
                key={chip}
                type="button"
                onClick={() => pick(chip)}
                className={`rounded-full border px-2.5 py-[3px] text-[11.5px] font-medium transition ${
                  selected
                    ? "border-[rgba(99,102,241,0.3)] bg-[rgba(99,102,241,0.12)] text-[#4338ca]"
                    : "border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] text-[var(--text-dim,#475569)] hover:bg-[var(--surface-3,#dbeafe)] hover:text-[var(--text,#0f172a)]"
                }`}
              >
                {chip}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
