"use client";

import { useEffect, useState } from "react";

interface EstablishedDateRangeFilterProps {
  // ISO `YYYY-MM-DD` strings, or null when the filter is unset.
  establishedAfter: string | null;
  establishedBefore: string | null;
  onChange: (next: {
    establishedAfter?: string | null;
    establishedBefore?: string | null;
  }) => void;
}

// Structural clone of the master list's RegistrationDateRangeFilter, but
// with much wider presets: new-bank charter volume runs ~7 per half-year
// nationally, so the BD card's 30/60/90-day windows would almost always
// render an empty list here.
const PRESETS = [
  { label: "Last 6 months", days: 183 },
  { label: "Last 12 months", days: 365 },
  { label: "Last 24 months", days: 730 },
] as const;

// Local-zone YYYY-MM-DD. Native <input type="date"> reads/writes in the
// user's local zone, so going through .toISOString() can shift the result
// by up to a day around midnight UTC.
function toLocalIsoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function daysAgoIso(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return toLocalIsoDate(date);
}

// Two native <input type="date"> pickers. Inputs hold local state and
// commit on blur instead of forwarding every keystroke — the parent commits
// to the URL, which re-renders this component with a new value prop, and a
// mid-type intermediate empty value would clear the visible MM/DD segments
// (see registration-date-range-filter.tsx for the original writeup).
export function EstablishedDateRangeFilter({
  establishedAfter,
  establishedBefore,
  onChange,
}: EstablishedDateRangeFilterProps) {
  const [afterRaw, setAfterRaw] = useState<string>(establishedAfter ?? "");
  const [beforeRaw, setBeforeRaw] = useState<string>(establishedBefore ?? "");

  // Re-seed local state when the URL changes from outside (back-nav,
  // share-link landing, Clear filters).
  useEffect(() => {
    setAfterRaw(establishedAfter ?? "");
  }, [establishedAfter]);
  useEffect(() => {
    setBeforeRaw(establishedBefore ?? "");
  }, [establishedBefore]);

  const inputClass =
    "h-[38px] w-full min-w-0 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] tabular-nums text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]";

  const presetClass =
    "inline-flex items-center rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]";

  function commitAfter() {
    const next = afterRaw || null;
    if (next !== establishedAfter) {
      onChange({ establishedAfter: next });
    }
  }

  function commitBefore() {
    const next = beforeRaw || null;
    if (next !== establishedBefore) {
      onChange({ establishedBefore: next });
    }
  }

  // Presets short-circuit the type-and-blur flow: set local state and
  // commit to the parent in one click. Clearing the upper bound matches
  // the "established in the last N months" intent.
  function applyPreset(days: number) {
    const iso = daysAgoIso(days);
    setAfterRaw(iso);
    setBeforeRaw("");
    onChange({ establishedAfter: iso, establishedBefore: null });
  }

  return (
    <div>
      <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        Established Date Range
      </label>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {PRESETS.map((preset) => (
          <button
            key={preset.days}
            type="button"
            onClick={() => applyPreset(preset.days)}
            className={presetClass}
          >
            {preset.label}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input
          type="date"
          value={afterRaw}
          onChange={(event) => setAfterRaw(event.target.value)}
          onBlur={commitAfter}
          // Block picking an after-date later than the before-date. Native
          // pickers honor max= when present; the local raw value tracks
          // unsaved edits in the sibling input.
          max={beforeRaw || undefined}
          aria-label="Established on or after"
          className={inputClass}
        />
        <span
          aria-hidden
          className="shrink-0 text-[12px] text-[var(--text-muted,#94a3b8)]"
        >
          —
        </span>
        <input
          type="date"
          value={beforeRaw}
          onChange={(event) => setBeforeRaw(event.target.value)}
          onBlur={commitBefore}
          min={afterRaw || undefined}
          aria-label="Established on or before"
          className={inputClass}
        />
      </div>
    </div>
  );
}
