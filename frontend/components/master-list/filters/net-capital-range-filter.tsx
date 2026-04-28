"use client";

import { useEffect, useRef, useState } from "react";

// Parses Deshorn-style net-capital input into a number of dollars.
// Accepts:
//   "10M" / "10m"           → 10_000_000
//   "100K" / "100k"         → 100_000
//   "1B" / "1b"             → 1_000_000_000
//   "10000000"              → 10_000_000
//   "10,000,000" / "10 000" → 10_000_000 (commas/whitespace stripped)
//   "$10M"                  → 10_000_000 (leading $ tolerated)
// Returns:
//   null      — input is empty (clear the filter)
//   number    — finite, non-negative parse
//   undefined — unparseable; caller should keep the previous value
export function parseNetCapitalInput(
  raw: string,
): number | null | undefined {
  const trimmed = raw.trim();
  if (trimmed === "") return null;

  const cleaned = trimmed.replace(/[,\s$]/g, "");
  const match = /^(\d*\.?\d+)([kKmMbB])?$/.exec(cleaned);
  if (!match) return undefined;

  const base = Number.parseFloat(match[1]);
  if (!Number.isFinite(base) || base < 0) return undefined;

  const suffix = match[2]?.toLowerCase();
  const multiplier =
    suffix === "k" ? 1_000 : suffix === "m" ? 1_000_000 : suffix === "b" ? 1_000_000_000 : 1;

  return base * multiplier;
}

interface NetCapitalRangeFilterProps {
  min: number | null;
  max: number | null;
  // Either a single field or both can change at once. The workspace
  // commits both via updateState in one go to avoid a double URL replace.
  onChange: (next: { min?: number | null; max?: number | null }) => void;
  // Debounce in ms. Defaults to 250ms — same value the spec mandated.
  debounceMs?: number;
}

// Two-input range filter for `latest_net_capital`. Local state holds the
// raw typed string so each keystroke doesn't churn the URL; the parsed
// value flows to onChange after `debounceMs` of inactivity. When the
// parent state changes externally (back-nav, share-link, clear-filters)
// we re-seed the inputs from the URL value.
export function NetCapitalRangeFilter({
  min,
  max,
  onChange,
  debounceMs = 250,
}: NetCapitalRangeFilterProps) {
  const [minRaw, setMinRaw] = useState<string>(min !== null ? String(min) : "");
  const [maxRaw, setMaxRaw] = useState<string>(max !== null ? String(max) : "");

  // Re-seed local input state when the URL state changes from outside
  // (back-nav restoring a different filter, Clear filters, share-link
  // landing). The String() conversion mirrors how the URL serializes the
  // value, so the user sees the exact filter they're filtering on.
  useEffect(() => {
    setMinRaw(min !== null ? String(min) : "");
  }, [min]);
  useEffect(() => {
    setMaxRaw(max !== null ? String(max) : "");
  }, [max]);

  // Debounced commit. Refs hold the latest snapshot so the timer
  // callback always reads fresh values without re-arming on every render.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const lastCommitted = useRef<{ min: number | null; max: number | null }>({
    min,
    max,
  });
  lastCommitted.current = { min, max };

  useEffect(() => {
    const handle = window.setTimeout(() => {
      const parsedMin = parseNetCapitalInput(minRaw);
      const parsedMax = parseNetCapitalInput(maxRaw);
      const patch: { min?: number | null; max?: number | null } = {};
      // `undefined` from the parser means "unparseable, keep last value"
      // — leave it out of the patch.
      if (parsedMin !== undefined && parsedMin !== lastCommitted.current.min) {
        patch.min = parsedMin;
      }
      if (parsedMax !== undefined && parsedMax !== lastCommitted.current.max) {
        patch.max = parsedMax;
      }
      if (patch.min !== undefined || patch.max !== undefined) {
        onChangeRef.current(patch);
      }
    }, debounceMs);
    return () => window.clearTimeout(handle);
  }, [minRaw, maxRaw, debounceMs]);

  const inputClass =
    "h-[38px] w-full min-w-0 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] tabular-nums text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)] placeholder:text-[var(--text-muted,#94a3b8)]";

  return (
    <div>
      <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        Net Capital Range
      </label>
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={minRaw}
          onChange={(event) => setMinRaw(event.target.value)}
          placeholder="Min, e.g. 10M"
          aria-label="Minimum net capital"
          className={inputClass}
        />
        <span
          aria-hidden
          className="shrink-0 text-[12px] text-[var(--text-muted,#94a3b8)]"
        >
          —
        </span>
        <input
          type="text"
          inputMode="numeric"
          value={maxRaw}
          onChange={(event) => setMaxRaw(event.target.value)}
          placeholder="Max, e.g. 100M"
          aria-label="Maximum net capital"
          className={inputClass}
        />
      </div>
    </div>
  );
}
