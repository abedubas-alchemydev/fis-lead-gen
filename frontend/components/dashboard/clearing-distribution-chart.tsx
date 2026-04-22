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
  const totalFirms = items.reduce((acc, item) => acc + item.count, 0);

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
        <div className="relative flex items-center justify-center">
          {/* Soft radial glow behind the donut. */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 -z-10 m-auto h-[180px] w-[180px] rounded-full bg-blue/5 blur-2xl"
          />
          <svg
            viewBox="0 0 220 220"
            className="h-[220px] w-[220px] drop-shadow-[0_6px_16px_rgba(10,31,63,0.08)]"
          >
            <circle cx="110" cy="110" r="70" fill="#eff4fb" />
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
                  className="origin-center cursor-pointer animate-scale-in transition-all duration-200 hover:opacity-90 hover:brightness-110"
                  style={{ animationDelay: `${index * 80}ms` }}
                  onClick={() => router.push(`/master-list?clearing_partner=${encodeURIComponent(item.provider)}`)}
                >
                  <title>{`${item.provider} — ${item.count.toLocaleString()} firms (${item.percentage.toFixed(1)}%)`}</title>
                </path>
              );
            })}
            <circle cx="110" cy="110" r="36" fill="white" />
            <text x="110" y="104" textAnchor="middle" className="fill-[#15305b] text-[20px] font-semibold tabular-nums">
              {totalFirms.toLocaleString()}
            </text>
            <text
              x="110"
              y="122"
              textAnchor="middle"
              className="fill-[#6d8097] text-[9px] font-medium uppercase tracking-[0.18em]"
            >
              Total firms
            </text>
          </svg>
        </div>

        <div className="space-y-2.5">
          {items.map((item, index) => (
            <button
              key={item.provider}
              type="button"
              onClick={() => router.push(`/master-list?clearing_partner=${encodeURIComponent(item.provider)}`)}
              className="group flex w-full items-center justify-between rounded-2xl border border-slate-200 px-4 py-3 text-left transition hover:-translate-y-px hover:border-blue/40 hover:bg-slate-50 hover:shadow-sm"
            >
              <div className="flex min-w-0 items-center gap-3">
                <span
                  className="h-3 w-3 shrink-0 rounded-full ring-2 ring-white shadow-sm transition group-hover:scale-110"
                  style={{ backgroundColor: palette[index % palette.length] }}
                />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-navy">{item.provider}</p>
                  <div className="mt-1 flex items-center gap-2">
                    <p className="text-xs tabular-nums text-slate-500">{item.count.toLocaleString()} firms</p>
                    <CompetitorBadge isCompetitor={item.is_competitor} />
                  </div>
                </div>
              </div>
              <p className="shrink-0 text-sm font-semibold tabular-nums text-navy">{item.percentage.toFixed(1)}%</p>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
