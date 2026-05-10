import Link from "next/link";
import type { Route } from "next";
import type { ComponentType } from "react";

type Tone = "blue" | "purple" | "red" | "amber";

// Verbatim tokens from dashboard-redesign.html .kpi tone variants.
// Using arbitrary [value] Tailwind classes (not bg-blue-500/15 etc.)
// because this project's tailwind.config.ts replaces the default `blue`
// palette with a single string `blue: "#1B5E9E"`, so `bg-blue-500` and
// `text-blue-400` don't exist. Arbitrary values bypass the palette entirely.
const iconChipMap: Record<Tone, string> = {
  blue: "bg-[rgba(59,130,246,0.15)] text-[#60a5fa]",
  purple: "bg-[rgba(139,92,246,0.15)] text-[#a78bfa]",
  red: "bg-[rgba(239,68,68,0.15)] text-[#fca5a5]",
  amber: "bg-[rgba(245,158,11,0.15)] text-[#fbbf24]"
};

const sparkGradientMap: Record<Tone, { id: string; stroke: string; stop: string }> = {
  blue: { id: "kpi-spark-blue", stroke: "#60a5fa", stop: "#3b82f6" },
  purple: { id: "kpi-spark-purple", stroke: "#a78bfa", stop: "#a78bfa" },
  red: { id: "kpi-spark-red", stroke: "#fca5a5", stop: "#ef4444" },
  amber: { id: "kpi-spark-amber", stroke: "#fbbf24", stop: "#f59e0b" }
};

// Synthetic sparkline shapes per tone — placeholder until a time-series
// endpoint exists. Paths copied verbatim from the mockup per tone.
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

// Icon components for KpiCard accept className + strokeWidth so the card
// can control sizing/stroke without being coupled to any specific icon
// library. Callers pass in inline SVG components (see dashboard-home-client).
export type KpiIconProps = { className?: string; strokeWidth?: number };
export type KpiIconComponent = ComponentType<KpiIconProps>;

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
  icon: KpiIconComponent;
  href?: Route;
  trend?: { direction: TrendDirection; label: string };
}) {
  const spark = sparkGradientMap[tone];
  const paths = sparkPaths[tone];

  // .trend-up:   color var(--green)=#10b981 (emerald-500); bg rgba(16,185,129,0.12)
  // .trend-down: color var(--red)=#ef4444   (red-500);     bg rgba(239,68,68,0.12)
  // .trend-flat: color var(--text-muted)=slate-400;        bg rgba(255,255,255,0.05)
  const trendClass =
    trend?.direction === "up"
      ? "bg-emerald-500/12 text-emerald-500"
      : trend?.direction === "down"
        ? "bg-red-500/12 text-red-500"
        : "bg-white/5 text-slate-400";

  const content = (
    // .kpi: bg var(--surface), 1px border var(--border), 16px radius, 20px
    //       pad, shadow var(--shadow-card), transition. Hover: translateY(-2px)
    //       + border-color var(--border-2). Hover does NOT change shadow.
    //       All tokens have fallbacks so non-dashboard contexts still work.
    <article
      className="relative overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5 transition-all duration-200 hover:-translate-y-0.5 hover:border-[var(--border-2,rgba(30,64,175,0.16))]"
      style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
    >
      {/* .kpi-head: flex, gap 10px, margin-bottom 14px */}
      <div className="mb-3.5 flex items-center gap-2.5">
        {/* .kpi-icon: 36x36, 10px radius, tinted tone bg + icon color */}
        <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-[10px] ${iconChipMap[tone]}`}>
          <Icon className="h-[18px] w-[18px]" strokeWidth={2} />
        </div>
        {/* .kpi-label: 11px, 0.1em tracking, uppercase, weight 600, color var(--text-muted). */}
        <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
          {title}
        </div>
      </div>

      {/* .kpi-value: 34px, weight 700, -0.02em tracking, flex baseline, gap 10px, mb 6px.
          NO line-height override → inherits body 1.5. Color var(--text). */}
      <div className="mb-1.5 flex items-baseline gap-2.5 text-[34px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
        {value}
        {trend ? (
          // .kpi-trend: inline-flex, gap 4px, 12px, weight 600, 3px 8px padding, 6px radius
          <span
            className={`inline-flex items-center gap-1 rounded-md px-2 py-[3px] text-[12px] font-semibold ${trendClass}`}
          >
            <svg width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
              {trend.direction === "up" ? (
                <path d="M7 17l10-10M7 7h10v10" />
              ) : trend.direction === "down" ? (
                <path d="M7 7l10 10M7 17h10V7" />
              ) : (
                <path d="M5 12h14" />
              )}
            </svg>
            {trend.label}
          </span>
        ) : null}
      </div>

      {/* .kpi-sub: 12px, color var(--text-muted), inherits body 1.5 line-height */}
      <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">{helper}</p>

      {/* .kpi-spark: margin-top 12px, height 36px, width 100%.
          `inline align-baseline` overrides Tailwind Preflight's
          `svg { display: block }` so the SVG sits on the text baseline like
          the mockup — this adds ~5px of inline descender space below it,
          which is the entire reason localhost was 214.6px while the mockup
          measured 220px. */}
      <svg className="mt-3 inline h-9 w-full align-baseline" viewBox="0 0 200 40" preserveAspectRatio="none">
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
