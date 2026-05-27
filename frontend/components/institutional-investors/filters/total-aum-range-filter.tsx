"use client";

import { useEffect, useRef, useState } from "react";

import { parseRegulatoryAumInput } from "@/components/advisor-list/filters/regulatory-aum-range-filter";

// Sibling of RegulatoryAumRangeFilter on the advisor list. Same control
// shape; the BE field this filter targets is ``total_aum`` (parsed from
// the latest 13F-HR ``<tableValueTotal>``), not the IAPD ``regulatory_aum``,
// so the label and ARIA strings reference "Total AUM" instead. The
// suffix-tolerant input parser is reused as-is.

interface TotalAumRangeFilterProps {
  min: number | null;
  max: number | null;
  onChange: (next: { min?: number | null; max?: number | null }) => void;
  debounceMs?: number;
}

export function TotalAumRangeFilter({
  min,
  max,
  onChange,
  debounceMs = 250,
}: TotalAumRangeFilterProps) {
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
        Total AUM
      </label>
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={minRaw}
          onChange={(event) => setMinRaw(event.target.value)}
          placeholder="Min, e.g. 1B"
          aria-label="Minimum total AUM"
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
          aria-label="Maximum total AUM"
          className={inputClass}
        />
      </div>
    </div>
  );
}
