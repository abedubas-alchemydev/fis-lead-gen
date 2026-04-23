"use client";

import type { KeyboardEvent } from "react";

// Mirrors `.segmented` + `.seg` + `.dotmark` in design-system.css. Controlled
// component with keyboard nav (← →) and aria-pressed on each button. Items
// are passed as an array — simpler + more type-safe than cloning children.
//
// Dotmark tones cover the six mockup variants: healthy / ok / risk share
// indicator colors with hot / warm / cold (red / amber / blue) so the same
// tone enum works for both the filter-card segmenteds (health, priority).
export type DotmarkTone = "healthy" | "ok" | "risk" | "hot" | "warm" | "cold";

export interface SegmentedItem {
  value: string;
  label: string;
  dot?: DotmarkTone;
}

export interface SegmentedProps {
  value: string;
  onChange: (value: string) => void;
  items: ReadonlyArray<SegmentedItem>;
  ariaLabel?: string;
  className?: string;
}

export function Segmented({ value, onChange, items, ariaLabel, className = "" }: SegmentedProps) {
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const currentIdx = items.findIndex((i) => i.value === value);
    if (currentIdx < 0) return;
    event.preventDefault();
    const delta = event.key === "ArrowLeft" ? -1 : 1;
    const next = items[(currentIdx + delta + items.length) % items.length];
    onChange(next.value);
  };

  return (
    <div
      role="group"
      aria-label={ariaLabel}
      onKeyDown={handleKeyDown}
      className={`inline-flex rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] p-[3px] ${className}`}
    >
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(item.value)}
            className={`inline-flex items-center gap-1.5 rounded-[7px] px-3 py-1.5 text-[12px] transition ${
              active
                ? "bg-[rgba(99,102,241,0.12)] font-semibold text-[#4338ca]"
                : "font-medium text-[var(--text-dim,#475569)] hover:text-[var(--text,#0f172a)]"
            }`}
          >
            {item.dot ? <Dotmark tone={item.dot} /> : null}
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

const dotmarkToneClass: Record<DotmarkTone, string> = {
  healthy: "bg-[var(--green,#10b981)]",
  ok: "bg-[var(--blue,#3b82f6)]",
  risk: "bg-[var(--red,#ef4444)]",
  hot: "bg-[var(--red,#ef4444)]",
  warm: "bg-[var(--amber,#f59e0b)]",
  cold: "bg-[var(--blue,#3b82f6)]",
};

// Opt-in box-shadow ring. Only the score-chip usage passes `halo`; filter
// Segmenteds don't, so this is purely additive — no existing visual shifts.
const dotmarkHaloClass: Record<DotmarkTone, string> = {
  healthy: "shadow-[0_0_0_3px_rgba(16,185,129,0.18)]",
  ok: "shadow-[0_0_0_3px_rgba(59,130,246,0.18)]",
  risk: "shadow-[0_0_0_3px_rgba(239,68,68,0.18)]",
  hot: "shadow-[0_0_0_3px_rgba(239,68,68,0.18)]",
  warm: "shadow-[0_0_0_3px_rgba(245,158,11,0.18)]",
  cold: "shadow-[0_0_0_3px_rgba(59,130,246,0.18)]",
};

export function Dotmark({
  tone,
  halo = false,
  className = "",
}: {
  tone: DotmarkTone;
  halo?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={`h-2 w-2 shrink-0 rounded-full ${dotmarkToneClass[tone]} ${halo ? dotmarkHaloClass[tone] : ""} ${className}`}
    />
  );
}
