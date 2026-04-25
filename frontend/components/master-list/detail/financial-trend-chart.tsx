"use client";

import { useMemo } from "react";

import { formatCurrency } from "@/lib/format";

// Simple sparkline-style trend chart for net-capital history.
//   - 0 points  → empty-state copy on var(--surface-2) wash
//   - 1 point   → centered value card (chart can't draw a line)
//   - 2+ points → inline SVG line chart + per-period mini cards
// Uses var(--accent) for stroke + dot fill and var(--surface-2) for the
// canvas so the chart sits comfortably inside the parent SectionPanel.
export function FinancialTrendChart({ points }: { points: Array<{ label: string; value: number }> }) {
  const viewBoxWidth = 360;
  const viewBoxHeight = 160;

  const path = useMemo(() => {
    if (points.length <= 1) return "";
    const values = points.map((p) => p.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = Math.max(max - min, 1);
    return points
      .map((p, i) => {
        const x = (i / Math.max(points.length - 1, 1)) * (viewBoxWidth - 30) + 15;
        const y = viewBoxHeight - (((p.value - min) / range) * (viewBoxHeight - 30) + 15);
        return `${i === 0 ? "M" : "L"} ${x} ${y}`;
      })
      .join(" ");
  }, [points]);

  if (points.length === 0) {
    return (
      <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-10 text-center text-sm text-[var(--text-muted,#94a3b8)]">
        No financial history available yet.
      </div>
    );
  }

  if (points.length === 1) {
    return (
      <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-center">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
          {points[0].label}
        </p>
        <p className="mt-2 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
          {formatCurrency(points[0].value)}
        </p>
        <p className="mt-2 text-xs text-[var(--text-muted,#94a3b8)]">
          Only one reporting period available. The trend chart will appear when a second year of data is filed.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <svg
        viewBox={`0 0 ${viewBoxWidth} ${viewBoxHeight}`}
        className="w-full rounded-2xl bg-[var(--surface-2,#f1f6fd)] p-3"
      >
        <path d={path} fill="none" stroke="var(--accent, #6366f1)" strokeWidth="3" strokeLinecap="round" />
        {points.map((p, i) => {
          const values = points.map((pt) => pt.value);
          const min = Math.min(...values);
          const max = Math.max(...values);
          const range = Math.max(max - min, 1);
          const x = (i / Math.max(points.length - 1, 1)) * (viewBoxWidth - 30) + 15;
          const y = viewBoxHeight - (((p.value - min) / range) * (viewBoxHeight - 30) + 15);
          return <circle key={p.label} cx={x} cy={y} r="4" fill="var(--accent, #6366f1)" />;
        })}
      </svg>
      <div className="grid gap-2 sm:grid-cols-2">
        {points.map((p) => (
          <div
            key={p.label}
            className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-sm"
          >
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              {p.label}
            </p>
            <p className="mt-1 font-semibold tabular-nums text-[var(--text,#0f172a)]">
              {formatCurrency(p.value)}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
