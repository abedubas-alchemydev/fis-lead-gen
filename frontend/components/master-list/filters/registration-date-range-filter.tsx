"use client";

interface RegistrationDateRangeFilterProps {
  // ISO `YYYY-MM-DD` strings, or null when the filter is unset.
  registeredAfter: string | null;
  registeredBefore: string | null;
  onChange: (next: {
    registeredAfter?: string | null;
    registeredBefore?: string | null;
  }) => void;
}

// Two native <input type="date"> pickers. The browser emits `YYYY-MM-DD`
// directly, which is what FastAPI's date validator on the BE accepts —
// no parsing or formatting needed. Empty string from the picker means
// the user cleared the field, so we forward null to the parent.
export function RegistrationDateRangeFilter({
  registeredAfter,
  registeredBefore,
  onChange,
}: RegistrationDateRangeFilterProps) {
  const inputClass =
    "h-[38px] w-full min-w-0 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] tabular-nums text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]";

  return (
    <div>
      <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        Registration Date Range
      </label>
      <div className="flex items-center gap-2">
        <input
          type="date"
          value={registeredAfter ?? ""}
          onChange={(event) =>
            onChange({ registeredAfter: event.target.value || null })
          }
          // Block picking an after-date later than the before-date. Native
          // pickers honor max= when present.
          max={registeredBefore ?? undefined}
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
          value={registeredBefore ?? ""}
          onChange={(event) =>
            onChange({ registeredBefore: event.target.value || null })
          }
          min={registeredAfter ?? undefined}
          aria-label="Registered on or before"
          className={inputClass}
        />
      </div>
    </div>
  );
}
