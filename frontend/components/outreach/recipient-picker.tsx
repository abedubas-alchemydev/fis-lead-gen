"use client";

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { ChevronDown, Mail, Search, Send, X } from "lucide-react";

import { ApiError, searchOutreachContacts } from "@/lib/api";
import type { RecipientSearchResult } from "@/lib/types";

// Compose-tab recipient picker. Either:
//   * Picks an existing contact via the autocomplete (firm contact /
//     advisor contact / investor contact -- the backend's
//     /outreach/contacts/search UNIONs all three).
//   * Or accepts a free-form email address; the dropdown surfaces a
//     synthetic "send to <typed>" row when the query parses as an
//     email and no contact matches.
//
// Built to look like the Combo primitive in components/ui/combo.tsx
// (same border / focus ring / chevron) but renders richer rows (with
// firm + entity-kind tag pill) so it doesn't fit Combo's
// string-only contract.

export type RecipientValue =
  | { kind: "contact"; result: RecipientSearchResult }
  | { kind: "adhoc"; email: string; name?: string | null };

interface RecipientPickerProps {
  value: RecipientValue | null;
  onChange: (value: RecipientValue | null) => void;
  disabled?: boolean;
  ariaLabel?: string;
}

const EMAIL_REGEX = /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i;
const SEARCH_DEBOUNCE_MS = 250;
const ENTITY_KIND_LABEL: Record<
  RecipientSearchResult["entity_kind"],
  string
> = {
  broker_dealer: "Broker-dealer",
  advisor: "Advisor",
  institutional_investor: "Investor",
};

export function RecipientPicker({
  value,
  onChange,
  disabled = false,
  ariaLabel,
}: RecipientPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RecipientSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeIdx, setActiveIdx] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Close on outside click. Matches Combo's pattern -- mousedown not
  // click so we close before any nested control fires.
  useEffect(() => {
    function onDocumentMouseDown(event: globalThis.MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentMouseDown);
    return () => document.removeEventListener("mousedown", onDocumentMouseDown);
  }, []);

  // Debounced fetch on query change.
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    const handle = window.setTimeout(async () => {
      try {
        const response = await searchOutreachContacts(trimmed);
        if (!isMountedRef.current) return;
        setResults(response.items);
      } catch (err) {
        if (!isMountedRef.current) return;
        setResults([]);
        setError(
          err instanceof ApiError
            ? err.detail || err.message
            : err instanceof Error
              ? err.message
              : "Search failed.",
        );
      } finally {
        if (isMountedRef.current) setLoading(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query]);

  const trimmed = query.trim();
  const isEmailQuery = EMAIL_REGEX.test(trimmed);
  // Offer the adhoc row only when the query is an email AND we don't
  // already have an exact contact match -- otherwise we'd surface two
  // ways to pick the same address.
  const adhocAvailable =
    isEmailQuery &&
    !results.some(
      (r) => r.contact_email.toLowerCase() === trimmed.toLowerCase(),
    );
  // Total option count drives ArrowDown wraparound; +1 when adhoc is on.
  const optionCount = results.length + (adhocAvailable ? 1 : 0);

  function pickContact(result: RecipientSearchResult) {
    onChange({ kind: "contact", result });
    setQuery("");
    setOpen(false);
    setActiveIdx(0);
  }

  function pickAdhoc(email: string) {
    onChange({ kind: "adhoc", email });
    setQuery("");
    setOpen(false);
    setActiveIdx(0);
  }

  function clear() {
    onChange(null);
    setQuery("");
    inputRef.current?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      if (optionCount > 0) setActiveIdx((i) => (i + 1) % optionCount);
    } else if (event.key === "ArrowUp") {
      if (optionCount === 0) return;
      event.preventDefault();
      setActiveIdx((i) => (i - 1 + optionCount) % optionCount);
    } else if (event.key === "Enter") {
      if (optionCount === 0) {
        // No matches + valid email = adhoc commit even without arrow.
        if (isEmailQuery) {
          event.preventDefault();
          pickAdhoc(trimmed);
        }
        return;
      }
      event.preventDefault();
      if (activeIdx < results.length) {
        pickContact(results[activeIdx]);
      } else if (adhocAvailable) {
        pickAdhoc(trimmed);
      }
    } else if (event.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  }

  if (value) {
    // Selected state: chip in place of input. Keeps the same border
    // metrics as the open input so the form doesn't jump on selection.
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3.5 py-2">
        <RecipientChip value={value} />
        <button
          type="button"
          onClick={clear}
          disabled={disabled}
          aria-label="Clear recipient"
          className="ml-auto grid h-6 w-6 shrink-0 place-items-center rounded-full text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--red,#ef4444)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          <X className="h-3.5 w-3.5" strokeWidth={2} />
        </button>
      </div>
    );
  }

  return (
    <div ref={rootRef} className="relative">
      <div
        onClick={() => {
          if (disabled) return;
          setOpen(true);
          inputRef.current?.focus();
        }}
        className={`relative flex items-center gap-2 rounded-[10px] border bg-[var(--surface,#ffffff)] px-3.5 py-2 transition ${
          disabled ? "opacity-60" : ""
        } ${
          open
            ? "border-[var(--accent,#6366f1)] shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            : "border-[var(--border,rgba(30,64,175,0.1))]"
        }`}
      >
        <Search
          className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
          strokeWidth={2}
        />
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
            setActiveIdx(0);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder="Search contacts or type an email…"
          aria-label={ariaLabel ?? "Recipient"}
          autoComplete="off"
          className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] outline-none placeholder:text-[var(--text-muted,#94a3b8)]"
        />
        <ChevronDown
          className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
          strokeWidth={2}
        />
      </div>

      {open && trimmed ? (
        <div
          role="listbox"
          className="absolute left-0 right-0 top-full z-10 mt-1 max-h-72 overflow-auto rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] py-1 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]"
        >
          {loading && results.length === 0 ? (
            <div className="px-3 py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
              Searching…
            </div>
          ) : null}

          {!loading && error ? (
            <div className="px-3 py-2 text-[12px] text-[var(--pill-red-text,#b91c1c)]">
              {error}
            </div>
          ) : null}

          {!loading && !error && results.length === 0 && !adhocAvailable ? (
            <div className="px-3 py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
              No contacts match. Type a full email address to send a one-off.
            </div>
          ) : null}

          {results.map((result, idx) => {
            const active = idx === activeIdx;
            return (
              <button
                key={`${result.entity_kind}-${result.contact_id}`}
                type="button"
                role="option"
                aria-selected={active}
                onMouseDown={(event) => {
                  event.preventDefault();
                  pickContact(result);
                }}
                onMouseEnter={() => setActiveIdx(idx)}
                className={`block w-full cursor-pointer px-3 py-2 text-left text-[13px] transition ${
                  active
                    ? "bg-[var(--surface-2,#f1f6fd)]"
                    : "bg-transparent"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-semibold text-[var(--text,#0f172a)]">
                    {result.contact_name || result.contact_email}
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
                    {ENTITY_KIND_LABEL[result.entity_kind]}
                  </span>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-[var(--text-dim,#475569)]">
                  <Mail className="h-3 w-3 text-[var(--text-muted,#94a3b8)]" />
                  <span className="truncate">{result.contact_email}</span>
                  <span className="text-[var(--text-muted,#94a3b8)]">·</span>
                  <span className="truncate">{result.entity_name}</span>
                  {result.contact_title ? (
                    <>
                      <span className="text-[var(--text-muted,#94a3b8)]">·</span>
                      <span className="truncate">{result.contact_title}</span>
                    </>
                  ) : null}
                </div>
              </button>
            );
          })}

          {adhocAvailable ? (
            <button
              type="button"
              role="option"
              aria-selected={activeIdx === results.length}
              onMouseDown={(event) => {
                event.preventDefault();
                pickAdhoc(trimmed);
              }}
              onMouseEnter={() => setActiveIdx(results.length)}
              className={`block w-full cursor-pointer border-t border-[var(--border,rgba(30,64,175,0.1))] px-3 py-2 text-left text-[13px] transition ${
                activeIdx === results.length
                  ? "bg-[var(--surface-2,#f1f6fd)]"
                  : "bg-transparent"
              }`}
            >
              <div className="flex items-center gap-2">
                <Send className="h-3.5 w-3.5 text-[var(--accent,#6366f1)]" />
                <span className="font-semibold text-[var(--text,#0f172a)]">
                  Send to{" "}
                  <span className="text-[var(--accent,#6366f1)]">{trimmed}</span>
                </span>
                <span className="ml-auto inline-flex rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
                  Ad-hoc
                </span>
              </div>
              <div className="mt-0.5 text-[11px] text-[var(--text-dim,#475569)]">
                One-off send. No firm / contact will be linked.
              </div>
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function RecipientChip({ value }: { value: RecipientValue }) {
  if (value.kind === "adhoc") {
    return (
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <Send className="h-4 w-4 shrink-0 text-[var(--accent,#6366f1)]" />
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-[13px] font-semibold text-[var(--text,#0f172a)]">
            {value.email}
          </span>
          <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
            Ad-hoc · no firm linked
          </span>
        </div>
      </div>
    );
  }
  const { result } = value;
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <Mail className="h-4 w-4 shrink-0 text-[var(--accent,#6366f1)]" />
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-[13px] font-semibold text-[var(--text,#0f172a)]">
          {result.contact_name || result.contact_email}
        </span>
        <span className="truncate text-[11px] text-[var(--text-dim,#475569)]">
          {result.contact_email} · {result.entity_name}
          {result.contact_title ? ` · ${result.contact_title}` : ""}
        </span>
      </div>
      <span className="inline-flex shrink-0 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
        {ENTITY_KIND_LABEL[result.entity_kind]}
      </span>
    </div>
  );
}
