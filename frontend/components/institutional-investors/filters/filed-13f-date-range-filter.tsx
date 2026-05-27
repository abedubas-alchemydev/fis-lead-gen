"use client";

import { useEffect, useState } from "react";

// Sibling of RegistrationDateRangeFilter on the BD master list. Same
// control shape; the BE fields this filter targets are
// ``filed_13f_after`` / ``filed_13f_before`` against
// ``latest_13f_filing_date``. Preset durations skew longer than the
// registration filter's 30/60/90 because 13F-HR filings are quarterly —
// "last 30 days" would frequently match nothing.

interface Filed13fDateRangeFilterProps {
  // ISO `YYYY-MM-DD` strings, or null when the filter is unset.
  filed13fAfter: string | null;
  filed13fBefore: string | null;
  onChange: (next: {
    filed13fAfter?: string | null;
    filed13fBefore?: string | null;
  }) => void;
}

const PRESET_DAYS = [90, 180, 365] as const;

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

export function Filed13fDateRangeFilter({
  filed13fAfter,
  filed13fBefore,
  onChange,
}: Filed13fDateRangeFilterProps) {
  const [afterRaw, setAfterRaw] = useState<string>(filed13fAfter ?? "");
  const [beforeRaw, setBeforeRaw] = useState<string>(filed13fBefore ?? "");

  useEffect(() => {
    setAfterRaw(filed13fAfter ?? "");
  }, [filed13fAfter]);
  useEffect(() => {
    setBeforeRaw(filed13fBefore ?? "");
  }, [filed13fBefore]);

  const inputClass =
    "h-[38px] w-full min-w-0 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] tabular-nums text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]";

  const presetClass =
    "inline-flex items-center rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]";

  function commitAfter() {
    const next = afterRaw || null;
    if (next !== filed13fAfter) {
      onChange({ filed13fAfter: next });
    }
  }

  function commitBefore() {
    const next = beforeRaw || null;
    if (next !== filed13fBefore) {
      onChange({ filed13fBefore: next });
    }
  }

  function applyPreset(days: number) {
    const iso = daysAgoIso(days);
    setAfterRaw(iso);
    setBeforeRaw("");
    onChange({ filed13fAfter: iso, filed13fBefore: null });
  }

  return (
    <div>
      <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        Latest 13F Filing Date
      </label>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {PRESET_DAYS.map((days) => (
          <button
            key={days}
            type="button"
            onClick={() => applyPreset(days)}
            className={presetClass}
          >
            Last {days} days
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input
          type="date"
          value={afterRaw}
          onChange={(event) => setAfterRaw(event.target.value)}
          onBlur={commitAfter}
          max={beforeRaw || undefined}
          aria-label="Filed on or after"
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
          aria-label="Filed on or before"
          className={inputClass}
        />
      </div>
    </div>
  );
}
