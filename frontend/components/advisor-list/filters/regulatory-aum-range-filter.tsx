"use client";

import { useEffect, useRef, useState } from "react";

// Parses Deshorn-style regulatory-AUM input into a number of dollars.
// Mirrors parseNetCapitalInput on the BD side; "T" added because RIA
// AUM is reported in trillions for the very largest filers (BlackRock,
// Vanguard, etc.).
//   "10M" / "10m"           → 10_000_000
//   "100K" / "100k"         → 100_000
//   "1B" / "1b"             → 1_000_000_000
//   "1T" / "1t"             → 1_000_000_000_000
//   "10000000"              → 10_000_000
//   "10,000,000" / "10 000" → 10_000_000 (commas/whitespace stripped)
//   "$10M"                  → 10_000_000 (leading $ tolerated)
// Returns:
//   null      — input is empty (clear the filter)
//   number    — finite, non-negative parse
//   undefined — unparseable; caller should keep the previous value
export function parseRegulatoryAumInput(
  raw: string,
): number | null | undefined {
  const trimmed = raw.trim();
  if (trimmed === "") return null;

  const cleaned = trimmed.replace(/[,\s$]/g, "");
  const match = /^(\d*\.?\d+)([kKmMbBtT])?$/.exec(cleaned);
  if (!match) return undefined;

  const base = Number.parseFloat(match[1]);
  if (!Number.isFinite(base) || base < 0) return undefined;

  const suffix = match[2]?.toLowerCase();
  const multiplier =
    suffix === "k"
      ? 1_000
      : suffix === "m"
      ? 1_000_000
      : suffix === "b"
      ? 1_000_000_000
      : suffix === "t"
      ? 1_000_000_000_000
      : 1;

  return base * multiplier;
}

interface RegulatoryAumRangeFilterProps {
  min: number | null;
  max: number | null;
  onChange: (next: { min?: number | null; max?: number | null }) => void;
  debounceMs?: number;
}

export function RegulatoryAumRangeFilter({
  min,
  max,
  onChange,
  debounceMs = 250,
}: RegulatoryAumRangeFilterProps) {
  const [minRaw, setMinRaw] = useState<string>(min !== null ? String(min) : "");
  const [maxRaw, setMaxRaw] = useState<string>(max !== null ? String(max) : "");

  useEffect(() => {
    setMinRaw(min !== null ? String(min) : "");
  }, [min]);
  useEffect(() => {
    setMaxRaw(max !== null ? String(max) : "");
  }, [max]);

  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const lastCommitted = useRef<{ min: number | null; max: number | null }>({
    min,
    max,
  });
  lastCommitted.current = { min, max };

  useEffect(() => {
    const handle = window.setTimeout(() => {
      const parsedMin = parseRegulatoryAumInput(minRaw);
      const parsedMax = parseRegulatoryAumInput(maxRaw);
      const patch: { min?: number | null; max?: number | null } = {};
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
        Regulatory AUM
      </label>
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={minRaw}
          onChange={(event) => setMinRaw(event.target.value)}
          placeholder="Min, e.g. 1B"
          aria-label="Minimum regulatory AUM"
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
          placeholder="Max, e.g. 100B"
          aria-label="Maximum regulatory AUM"
          className={inputClass}
        />
      </div>
    </div>
  );
}
