"use client";

import { useRouter } from "next/navigation";

import type { ClearingProviderShare } from "@/lib/types";

// Mockup palette — each row gets a stable color pair by position.
const rowPalettes = [
  { swatch: "#1e3a8a", fillA: "#1e3a8a", fillB: "#3b82f6" },
  { swatch: "#ef4444", fillA: "#b91c1c", fillB: "#ef4444" },
  { swatch: "#9ca3af", fillA: "#6b7280", fillB: "#9ca3af" },
  { swatch: "#fbbf24", fillA: "#d97706", fillB: "#fbbf24" },
  { swatch: "#ec4899", fillA: "#be185d", fillB: "#ec4899" },
  { swatch: "#10b981", fillA: "#047857", fillB: "#10b981" },
  { swatch: "#06b6d4", fillA: "#0e7490", fillB: "#06b6d4" },
  { swatch: "#8b5cf6", fillA: "#6d28d9", fillB: "#8b5cf6" }
];

export function ClearingDistributionChart({ items }: { items: ClearingProviderShare[] }) {
  const router = useRouter();

  if (items.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-200/70 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05)]">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">
          Clearing Market
        </p>
        <p className="mt-3 text-sm text-slate-500">
          Clearing distribution will appear as extracted provider data becomes available.
        </p>
      </div>
    );
  }

  // Normalize bar widths so the top bar is ~92% and others scale
  // proportionally — matches the mockup's visual rhythm.
  const maxPercent = Math.max(...items.map((i) => i.percentage));
  const scale = (p: number) => (maxPercent > 0 ? (p / maxPercent) * 92 : 0);

  return (
    <div className="rounded-2xl border border-slate-200/70 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05)]">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-slate-900">
            Clearing market — provider distribution
          </h3>
          <p className="mt-0.5 text-xs text-slate-500">Click a row to filter the Master List</p>
        </div>
      </div>

      <div>
        {items.map((item, index) => {
          const p = rowPalettes[index % rowPalettes.length];
          return (
            <button
              key={item.provider}
              type="button"
              onClick={() =>
                router.push(`/master-list?clearing_partner=${encodeURIComponent(item.provider)}`)
              }
              className="flex w-full items-center gap-3.5 border-t border-slate-200/70 py-2.5 text-left transition first:border-t-0 hover:bg-slate-50/60"
            >
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                style={{ backgroundColor: p.swatch }}
              />
              <div className="flex min-w-[220px] items-center gap-2 text-[13px]">
                <span className="font-medium text-slate-800">{item.provider}</span>
                <span className="text-[11px] text-slate-500">· {item.count.toLocaleString()} firms</span>
                {item.is_competitor ? (
                  <span className="rounded bg-red-500/12 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-red-600">
                    COMPETITOR
                  </span>
                ) : null}
              </div>
              <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${scale(item.percentage)}%`,
                    background: `linear-gradient(90deg, ${p.fillA}, ${p.fillB})`
                  }}
                />
              </div>
              <span className="min-w-[48px] text-right text-[13px] font-semibold tabular-nums text-slate-800">
                {item.percentage.toFixed(1)}%
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
