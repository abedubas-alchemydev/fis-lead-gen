"use client";

import { useRouter } from "next/navigation";

import type { ClearingProviderShare } from "@/lib/types";

// Mockup palette — each row gets a stable color pair by position.
const ROW_PALETTES = [
  { swatch: "#1e3a8a", fillA: "#1e3a8a", fillB: "#3b82f6" },
  { swatch: "#ef4444", fillA: "#b91c1c", fillB: "#ef4444" },
  { swatch: "#9ca3af", fillA: "#6b7280", fillB: "#9ca3af" },
  { swatch: "#fbbf24", fillA: "#d97706", fillB: "#fbbf24" },
  { swatch: "#ec4899", fillA: "#be185d", fillB: "#ec4899" },
  { swatch: "#10b981", fillA: "#047857", fillB: "#10b981" },
  { swatch: "#06b6d4", fillA: "#0e7490", fillB: "#06b6d4" },
  { swatch: "#8b5cf6", fillA: "#6d28d9", fillB: "#8b5cf6" },
] as const;

// Real extraction data produces compound provider names like
// "Goldman, Sachs & Co., Pershing LLC, Mirae Asset Securities (USA), Inc."
// that wrap and destroy row rhythm. Collapse to single-line + ellipsis and
// expose the full text via the title attr so it's still inspectable.
function formatFirmsLabel(count: number): string {
  return `${count.toLocaleString()} ${count === 1 ? "firm" : "firms"}`;
}

// "0%" stays "0%". Real values below 0.1% render as "<0.1%" so we don't
// lie with "0.0%" when 1-of-3000 is technically 0.033%.
function formatPercentLabel(percentage: number): string {
  if (percentage <= 0) return "0%";
  if (percentage < 0.1) return "<0.1%";
  return `${percentage.toFixed(1)}%`;
}

export function ClearingDistributionChart({ items }: { items: ClearingProviderShare[] }) {
  const router = useRouter();

  if (items.length === 0) {
    return (
      <div
        className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
        style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
          Clearing Market
        </p>
        <p className="mt-3 text-sm text-[var(--text-muted,#94a3b8)]">
          Clearing distribution will appear as extracted provider data becomes available.
        </p>
      </div>
    );
  }

  // Normalize bar widths so the top bar reads at ~92% and everything else
  // scales against the leader — matches the mockup's visual rhythm and
  // keeps the track visible when percentages are near-zero in aggregate.
  const maxPercent = Math.max(...items.map((i) => i.percentage));
  const scale = (p: number) => (maxPercent > 0 ? (p / maxPercent) * 92 : 0);

  return (
    <div
      className="flex h-full flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
      style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
    >
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            Clearing market — provider distribution
          </h3>
          <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
            Click a row to filter the Master List
          </p>
        </div>
      </div>

      <div>
        {items.map((item, index) => {
          const palette = ROW_PALETTES[index % ROW_PALETTES.length];
          const firmsLabel = formatFirmsLabel(item.count);
          const percentLabel = formatPercentLabel(item.percentage);
          return (
            <button
              key={item.provider}
              type="button"
              onClick={() =>
                router.push(`/master-list?clearing_partner=${encodeURIComponent(item.provider)}`)
              }
              title={item.provider}
              // Grid columns keep the bar track and percentage aligned
              // across rows regardless of provider-name length:
              //   [swatch] [label cluster, capped at 40%] [bar, flexes]
              //   [percent, fixed 56px right-aligned]
              className="grid w-full grid-cols-[10px_minmax(0,40%)_minmax(80px,1fr)_56px] items-center gap-3.5 border-t border-[var(--border,rgba(30,64,175,0.1))] py-2.5 text-left transition first:border-t-0 hover:bg-[var(--surface-2,#f1f6fd)]"
            >
              <span
                className="h-2.5 w-2.5 rounded-[3px]"
                style={{ backgroundColor: palette.swatch }}
              />
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate text-[13px] font-medium text-[var(--text,#0f172a)]">
                  {item.provider}
                </span>
                <span className="shrink-0 whitespace-nowrap text-[11px] text-[var(--text-muted,#94a3b8)]">
                  · {firmsLabel}
                </span>
                {item.is_competitor ? (
                  <span className="shrink-0 rounded bg-red-500/12 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-red-600">
                    COMPETITOR
                  </span>
                ) : null}
              </div>
              <div className="relative h-1.5 overflow-hidden rounded-full bg-[var(--surface-2,#f1f6fd)]">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${scale(item.percentage)}%`,
                    background: `linear-gradient(90deg, ${palette.fillA}, ${palette.fillB})`,
                  }}
                />
              </div>
              <span className="text-right text-[13px] font-semibold tabular-nums text-[var(--text,#0f172a)]">
                {percentLabel}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
