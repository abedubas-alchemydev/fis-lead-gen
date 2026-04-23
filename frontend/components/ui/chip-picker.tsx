"use client";

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { X } from "lucide-react";

// Typeahead multi-select matching the mockup's `.chip-picker` + `.chip` +
// `.chip-input` primitives. Input grows inline with chips; suggestions
// render in an absolute dropdown filtered against `options`, excluding any
// already-selected values. Arrow up/down walks suggestions, Enter commits,
// Backspace on empty input removes the last chip.
export interface ChipPickerProps {
  value: string[];
  onChange: (value: string[]) => void;
  options: ReadonlyArray<string>;
  placeholder?: string;
  ariaLabel?: string;
  className?: string;
}

export function ChipPicker({
  value,
  onChange,
  options,
  placeholder = "",
  ariaLabel,
  className = "",
}: ChipPickerProps) {
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Outside-click closes the suggestion list. mousedown (not click) so the
  // chip-add path below (onMouseDown → preventDefault) still wins on
  // suggestion selection.
  useEffect(() => {
    function onDocumentMouseDown(event: globalThis.MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setFocused(false);
    }
    document.addEventListener("mousedown", onDocumentMouseDown);
    return () => document.removeEventListener("mousedown", onDocumentMouseDown);
  }, []);

  const q = query.trim().toLowerCase();
  const suggestions = options
    .filter((opt) => !value.includes(opt))
    .filter((opt) => !q || opt.toLowerCase().includes(q));

  function addChip(chip: string) {
    if (value.includes(chip)) return;
    onChange([...value, chip]);
    setQuery("");
    setActiveIdx(0);
  }

  function removeChip(chip: string) {
    onChange(value.filter((v) => v !== chip));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      if (suggestions.length === 0) return;
      event.preventDefault();
      setActiveIdx((i) => (i + 1) % suggestions.length);
    } else if (event.key === "ArrowUp") {
      if (suggestions.length === 0) return;
      event.preventDefault();
      setActiveIdx((i) => (i - 1 + suggestions.length) % suggestions.length);
    } else if (event.key === "Enter") {
      if (suggestions.length === 0) return;
      event.preventDefault();
      addChip(suggestions[activeIdx]);
    } else if (event.key === "Backspace" && query === "" && value.length > 0) {
      event.preventDefault();
      removeChip(value[value.length - 1]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      setFocused(false);
      inputRef.current?.blur();
    }
  }

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <div
        onClick={() => inputRef.current?.focus()}
        className={`flex min-h-[38px] flex-wrap items-center gap-1.5 rounded-[10px] border bg-[var(--surface,#ffffff)] px-2 py-1.5 transition ${
          focused
            ? "border-[var(--accent,#6366f1)] shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            : "border-[var(--border,rgba(30,64,175,0.1))]"
        }`}
      >
        {value.map((chip) => (
          <span
            key={chip}
            className="inline-flex items-center gap-1 rounded-full border border-[rgba(99,102,241,0.25)] bg-[rgba(99,102,241,0.12)] px-2 py-[3px] text-[11.5px] font-medium text-[#4338ca]"
          >
            {chip}
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                removeChip(chip);
              }}
              aria-label={`Remove ${chip}`}
              className="grid h-4 w-4 place-items-center rounded-full opacity-60 transition hover:text-[var(--red,#ef4444)] hover:opacity-100"
            >
              <X className="h-3 w-3" strokeWidth={2} />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActiveIdx(0);
          }}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          placeholder={value.length === 0 ? placeholder : ""}
          aria-label={ariaLabel}
          className="min-w-[80px] flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] placeholder:text-[var(--text-muted,#94a3b8)] outline-none"
        />
      </div>

      {focused && suggestions.length > 0 ? (
        <ul
          role="listbox"
          className="absolute left-0 right-0 top-full z-10 mt-1 max-h-56 overflow-auto rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] py-1 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]"
        >
          {suggestions.map((opt, idx) => (
            <li
              key={opt}
              role="option"
              aria-selected={idx === activeIdx}
              onMouseDown={(event) => {
                event.preventDefault();
                addChip(opt);
              }}
              onMouseEnter={() => setActiveIdx(idx)}
              className={`cursor-pointer px-3 py-1.5 text-[13px] transition ${
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
  );
}
