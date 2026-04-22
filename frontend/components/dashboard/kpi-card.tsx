import Link from "next/link";
import type { Route } from "next";
import type { LucideIcon } from "lucide-react";

type Tone = "blue" | "purple" | "red" | "amber";

// Mockup tone palette — white card + tinted icon chip + colored sparkline.
// No saturated card backgrounds, no gradients on the card body.
const iconChipMap: Record<Tone, string> = {
  blue: "bg-blue-500/15 text-blue-500",
  purple: "bg-violet-500/15 text-violet-500",
  red: "bg-red-500/15 text-red-400",
  amber: "bg-amber-500/15 text-amber-500"
};

const sparkGradientMap: Record<Tone, { id: string; stroke: string; stop: string }> = {
  blue: { id: "kpi-spark-blue", stroke: "#60a5fa", stop: "#3b82f6" },
  purple: { id: "kpi-spark-purple", stroke: "#a78bfa", stop: "#a78bfa" },
  red: { id: "kpi-spark-red", stroke: "#fca5a5", stop: "#ef4444" },
  amber: { id: "kpi-spark-amber", stroke: "#fbbf24", stop: "#f59e0b" }
};

// Synthetic sparkline shapes per tone — placeholder until a time-series
// endpoint exists for each KPI. Paths are plausible monotone-ish trends.
const sparkPaths: Record<Tone, { area: string; line: string }> = {
  blue: {
    area: "M0 28 L20 24 L40 26 L60 18 L80 20 L100 14 L120 18 L140 10 L160 14 L180 8 L200 12 L200 40 L0 40 Z",
    line: "M0 28 L20 24 L40 26 L60 18 L80 20 L100 14 L120 18 L140 10 L160 14 L180 8 L200 12"
  },
  purple: {
    area: "M0 12 L20 14 L40 10 L60 16 L80 14 L100 20 L120 18 L140 24 L160 22 L180 28 L200 30 L200 40 L0 40 Z",
    line: "M0 12 L20 14 L40 10 L60 16 L80 14 L100 20 L120 18 L140 24 L160 22 L180 28 L200 30"
  },
  red: {
    area: "M0 30 L20 28 L40 24 L60 26 L80 20 L100 22 L120 16 L140 14 L160 10 L180 8 L200 6 L200 40 L0 40 Z",
    line: "M0 30 L20 28 L40 24 L60 26 L80 20 L100 22 L120 16 L140 14 L160 10 L180 8 L200 6"
  },
  amber: {
    area: "M0 30 L20 26 L40 28 L60 22 L80 18 L100 20 L120 14 L140 16 L160 10 L180 12 L200 6 L200 40 L0 40 Z",
    line: "M0 30 L20 26 L40 28 L60 22 L80 18 L100 20 L120 14 L140 16 L160 10 L180 12 L200 6"
  }
};

type TrendDirection = "up" | "down" | "flat";

export function KpiCard({
  title,
  value,
  helper,
  tone,
  icon: Icon,
  href,
  trend
}: {
  title: string;
  value: string;
  helper: string;
  tone: Tone;
  icon: LucideIcon;
  href?: Route;
  trend?: { direction: TrendDirection; label: string };
}) {
  const spark = sparkGradientMap[tone];
  const paths = sparkPaths[tone];

  const trendClass =
    trend?.direction === "up"
      ? "bg-emerald-500/12 text-emerald-600"
      : trend?.direction === "down"
        ? "bg-red-500/12 text-red-500"
        : "bg-slate-500/10 text-slate-500";

  const content = (
    <article className="relative overflow-hidden rounded-2xl border border-slate-200/70 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05)] transition-all duration-200 hover:-translate-y-0.5 hover:border-slate-300">
      <div className="mb-3.5 flex items-center gap-2.5">
        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] ${iconChipMap[tone]}`}>
          <Icon className="h-[18px] w-[18px]" strokeWidth={2} />
        </div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">{title}</p>
      </div>

      <div className="mb-1.5 flex items-baseline gap-2.5">
        <p className="text-[34px] font-bold leading-none tracking-[-0.02em] tabular-nums text-slate-900">
          {value}
        </p>
        {trend ? (
          <span
            className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold ${trendClass}`}
          >
            <svg width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
              {trend.direction === "up" ? (
                <path d="M7 17l10-10M7 7h10v10" strokeLinecap="round" strokeLinejoin="round" />
              ) : trend.direction === "down" ? (
                <path d="M7 7l10 10M7 17h10V7" strokeLinecap="round" strokeLinejoin="round" />
              ) : (
                <path d="M5 12h14" strokeLinecap="round" />
              )}
            </svg>
            {trend.label}
          </span>
        ) : null}
      </div>

      <p className="text-xs text-slate-500">{helper}</p>

      <svg className="mt-3 h-9 w-full" viewBox="0 0 200 40" preserveAspectRatio="none">
        <defs>
          <linearGradient id={spark.id} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={spark.stop} stopOpacity="0.35" />
            <stop offset="100%" stopColor={spark.stop} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={paths.area} fill={`url(#${spark.id})`} />
        <path d={paths.line} fill="none" stroke={spark.stroke} strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    </article>
  );

  if (!href) {
    return content;
  }

  return (
    <Link
      href={href}
      className="block rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white"
    >
      {content}
    </Link>
  );
}
