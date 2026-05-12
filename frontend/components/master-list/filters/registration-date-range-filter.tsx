"use client";

import { useEffect, useState } from "react";

interface RegistrationDateRangeFilterProps {
  // ISO `YYYY-MM-DD` strings, or null when the filter is unset.
  registeredAfter: string | null;
  registeredBefore: string | null;
  onChange: (next: {
    registeredAfter?: string | null;
    registeredBefore?: string | null;
  }) => void;
}

const PRESET_DAYS = [30, 60, 90] as const;

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

// Two native <input type="date"> pickers. The browser emits `YYYY-MM-DD`
// directly, which is what FastAPI's date validator on the BE accepts —
// no parsing or formatting needed.
//
// Inputs use local state and commit on blur instead of forwarding every
// keystroke to the parent. Reason: the parent commits to the URL via
// router.push, which re-renders this component with a new `value` prop.
// While the user is typing the YYYY segment, the date is intermediately
// invalid and `event.target.value` is the empty string — that propagates
// to null, the URL re-renders the input with `value=""`, and the
// browser's visible MM/DD segments get cleared along with it. Holding
// the typed value locally and committing on blur breaks that loop.
export function RegistrationDateRangeFilter({
  registeredAfter,
  registeredBefore,
  onChange,
}: RegistrationDateRangeFilterProps) {
  const [afterRaw, setAfterRaw] = useState<string>(registeredAfter ?? "");
  const [beforeRaw, setBeforeRaw] = useState<string>(registeredBefore ?? "");

  // Re-seed local state when the URL changes from outside (back-nav,
  // share-link landing, Clear filters). Compare against the local raw
  // value rather than blindly setting so we don't fight the user mid-type.
  useEffect(() => {
    setAfterRaw(registeredAfter ?? "");
  }, [registeredAfter]);
  useEffect(() => {
    setBeforeRaw(registeredBefore ?? "");
  }, [registeredBefore]);

  const inputClass =
    "h-[38px] w-full min-w-0 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] tabular-nums text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]";

  const presetClass =
    "inline-flex items-center rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]";

  function commitAfter() {
    const next = afterRaw || null;
    if (next !== registeredAfter) {
      onChange({ registeredAfter: next });
    }
  }

  function commitBefore() {
    const next = beforeRaw || null;
    if (next !== registeredBefore) {
      onChange({ registeredBefore: next });
    }
  }

  // Presets short-circuit the type-and-blur flow: set local state and
  // commit to the parent in one click. Clearing the upper bound matches
  // the "registered in the last N days" intent.
  function applyPreset(days: number) {
    const iso = daysAgoIso(days);
    setAfterRaw(iso);
    setBeforeRaw("");
    onChange({ registeredAfter: iso, registeredBefore: null });
  }

  return (
    <div>
      <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        Registration Date Range
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
          // Block picking an after-date later than the before-date. Native
          // pickers honor max= when present. Use the local raw value so
          // the constraint tracks unsaved edits in the sibling input.
          max={beforeRaw || undefined}
          aria-label="Registered on or after"
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
          aria-label="Registered on or before"
          className={inputClass}
        />
      </div>
    </div>
  );
}
