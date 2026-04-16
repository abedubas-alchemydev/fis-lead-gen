"use client";

import { useRouter } from "next/navigation";

import type { ClearingProviderShare } from "@/lib/types";
import { CompetitorBadge } from "@/components/master-list/competitor-badge";

const palette = ["#15305b", "#2d6aad", "#6d8097", "#d8a94a", "#ef4c3b", "#76a7e1"];

function polarToCartesian(centerX: number, centerY: number, radius: number, angleInDegrees: number) {
  const angleInRadians = ((angleInDegrees - 90) * Math.PI) / 180;
  return {
    x: centerX + radius * Math.cos(angleInRadians),
    y: centerY + radius * Math.sin(angleInRadians)
  };
}

function describeArc(x: number, y: number, radius: number, startAngle: number, endAngle: number) {
  const start = polarToCartesian(x, y, radius, endAngle);
  const end = polarToCartesian(x, y, radius, startAngle);
  const largeArcFlag = endAngle - startAngle <= 180 ? "0" : "1";
  return `M ${x} ${y} L ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArcFlag} 0 ${end.x} ${end.y} Z`;
}

export function ClearingDistributionChart({ items }: { items: ClearingProviderShare[] }) {
  const router = useRouter();
  let currentAngle = 0;

  if (items.length === 0) {
    return (
      <div className="rounded-[28px] border border-white/80 bg-white/92 p-6 shadow-shell">
        <p className="text-sm font-medium uppercase tracking-[0.22em] text-blue">Clearing Market</p>
        <p className="mt-3 text-sm text-slate-600">Clearing distribution will appear as extracted provider data becomes available.</p>
      </div>
    );
  }

  return (
    <div className="rounded-[28px] border border-white/80 bg-white/92 p-6 shadow-shell">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium uppercase tracking-[0.22em] text-blue">Clearing Market</p>
          <h3 className="mt-2 text-xl font-semibold text-navy">Provider distribution</h3>
        </div>
        <p className="max-w-44 text-right text-xs leading-5 text-slate-500">Click a segment or provider row to filter the Master List.</p>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[220px_1fr]">
        <div className="flex items-center justify-center">
          <svg viewBox="0 0 220 220" className="h-[220px] w-[220px]">
            <circle cx="110" cy="110" r="70" fill="#eff4fb" />
            <circle cx="110" cy="110" r="34" fill="white" />
            {items.map((item, index) => {
              const angle = (item.percentage / 100) * 360;
              const path = describeArc(110, 110, 70, currentAngle, currentAngle + angle);
              const segmentStart = currentAngle;
              currentAngle += angle;
              return (
                <path
                  key={`${item.provider}-${segmentStart}`}
                  d={path}
                  fill={palette[index % palette.length]}
                  className="cursor-pointer transition hover:opacity-85"
                  onClick={() => router.push(`/master-list?clearing_partner=${encodeURIComponent(item.provider)}`)}
                />
              );
            })}
            <text x="110" y="102" textAnchor="middle" className="fill-[#15305b] text-[14px] font-semibold">
              Clearing
            </text>
            <text x="110" y="124" textAnchor="middle" className="fill-[#6d8097] text-[11px]">
              Share
            </text>
          </svg>
        </div>

        <div className="space-y-3">
          {items.map((item, index) => (
            <button
              key={item.provider}
              type="button"
              onClick={() => router.push(`/master-list?clearing_partner=${encodeURIComponent(item.provider)}`)}
              className="flex w-full items-center justify-between rounded-2xl border border-slate-200 px-4 py-3 text-left transition hover:border-blue/35 hover:bg-slate-50"
            >
              <div className="flex items-center gap-3">
                <span className="h-3 w-3 rounded-full" style={{ backgroundColor: palette[index % palette.length] }} />
                <div>
                  <p className="text-sm font-medium text-navy">{item.provider}</p>
                  <div className="mt-1 flex items-center gap-2">
                    <p className="text-xs text-slate-500">{item.count.toLocaleString()} firms</p>
                    <CompetitorBadge isCompetitor={item.is_competitor} />
                  </div>
                </div>
              </div>
              <p className="text-sm font-semibold text-navy">{item.percentage.toFixed(1)}%</p>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
