"use client";

// Mini product visuals for the feature tour. Each one RE-IMPLEMENTS the SVG /
// markup idiom of a real dashboard component (kpi-card sparkline, clearing-
// distribution bars, top-prospects score ring) using **local mock data only**
// — these intentionally do NOT import the apiRequest-backed dashboard
// components, so the landing bundle stays fetch-free.

// ── Sparkline (idiom: components/dashboard/kpi-card.tsx) ─────────────────────
// Area + line path pair in a 0 0 200 40 viewBox with a vertical gradient fill.

type SparkData = {
  gradientId: string;
  stroke: string;
  stop: string;
  area: string;
  line: string;
};

export function SparklinePanel({
  title,
  value,
  trend,
  helper,
  data,
}: {
  title: string;
  value: string;
  trend: { direction: "up" | "down"; label: string };
  helper: string;
  data: SparkData;
}) {
  const trendClass =
    trend.direction === "up"
      ? "bg-emerald-500/12 text-emerald-500"
      : "bg-red-500/12 text-red-500";

  return (
    <article
      className="relative overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
      style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
    >
      <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
        {title}
      </div>
      <div className="mb-1.5 mt-2 flex items-baseline gap-2.5 text-[34px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
        {value}
        <span className={`inline-flex items-center gap-1 rounded-md px-2 py-[3px] text-[12px] font-semibold ${trendClass}`}>
          <svg width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
            {trend.direction === "up" ? <path d="M7 17l10-10M7 7h10v10" /> : <path d="M7 7l10 10M7 17h10V7" />}
          </svg>
          {trend.label}
        </span>
      </div>
      <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">{helper}</p>
      <svg className="mt-3 inline h-9 w-full align-baseline" viewBox="0 0 200 40" preserveAspectRatio="none">
        <defs>
          <linearGradient id={data.gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={data.stop} stopOpacity="0.35" />
            <stop offset="100%" stopColor={data.stop} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={data.area} fill={`url(#${data.gradientId})`} />
        <path d={data.line} fill="none" stroke={data.stroke} strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    </article>
  );
}

// ── Distribution bars (idiom: clearing-distribution-chart.tsx) ───────────────
// Rows of [swatch] [label · N firms] [gradient track scaled to ~92% of max]
// [percent]. Track width is normalised against the leading row.

type BarRow = {
  provider: string;
  firms: number;
  percentage: number;
  swatch: string;
  fillA: string;
  fillB: string;
};

export function DistributionPanel({ title, helper, rows }: { title: string; helper: string; rows: BarRow[] }) {
  const maxPercent = Math.max(...rows.map((r) => r.percentage));
  const scale = (p: number) => (maxPercent > 0 ? (p / maxPercent) * 92 : 0);

  return (
    <div
      className="flex h-full flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
      style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
    >
      <div className="mb-3">
        <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">{title}</h3>
        <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">{helper}</p>
      </div>
      <div className="flex-1">
        {rows.map((row) => (
          <div
            key={row.provider}
            className="grid grid-cols-[10px_minmax(0,40%)_minmax(60px,1fr)_44px] items-center gap-3.5 border-t border-[var(--border,rgba(30,64,175,0.1))] py-2.5 first:border-t-0"
          >
            <span className="h-2.5 w-2.5 rounded-[3px]" style={{ backgroundColor: row.swatch }} />
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-[13px] font-medium text-[var(--text,#0f172a)]">{row.provider}</span>
              <span className="shrink-0 whitespace-nowrap text-[11px] text-[var(--text-muted,#94a3b8)]">
                · {row.firms.toLocaleString()} firms
              </span>
            </div>
            <div className="relative h-1.5 overflow-hidden rounded-full bg-[var(--surface-2,#f1f6fd)]">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${scale(row.percentage)}%`,
                  background: `linear-gradient(90deg, ${row.fillA}, ${row.fillB})`,
                }}
              />
            </div>
            <span className="text-right text-[13px] font-semibold tabular-nums text-[var(--text,#0f172a)]">
              {row.percentage.toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Prospect rows + score ring (idiom: top-prospects-card.tsx) ───────────────
// Avatar initials, firm + meta, value, and a two-circle ring in a 0 0 36 36
// viewBox where strokeDasharray = `${(pct/100)*88} 88` on r=14, rotate(-90).

type ProspectRow = {
  name: string;
  meta: string;
  value: string;
  pct: number;
  gradient: string;
  ring: string;
};

export function ProspectPanel({ title, helper, rows }: { title: string; helper: string; rows: ProspectRow[] }) {
  return (
    <div
      className="flex h-full flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
      style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
    >
      <div className="mb-3">
        <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">{title}</h3>
        <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">{helper}</p>
      </div>
      <div>
        {rows.map((row) => {
          const dashArray = `${(row.pct / 100) * 88} 88`;
          const initials = row.name
            .replace(/[,&.]/g, "")
            .split(/\s+/)
            .filter((w) => w.length > 1)
            .slice(0, 2)
            .map((w) => w[0])
            .join("")
            .toUpperCase();
          return (
            <div
              key={row.name}
              className="grid grid-cols-[36px_1fr_auto] items-center gap-3 border-t border-[var(--border,rgba(30,64,175,0.1))] py-3 first:border-t-0"
            >
              <div
                className="grid h-9 w-9 place-items-center rounded-[10px] text-[13px] font-bold text-white"
                style={{ background: row.gradient }}
              >
                {initials || "BD"}
              </div>
              <div className="min-w-0">
                <div className="truncate text-[13.5px] font-semibold text-[var(--text,#0f172a)]">{row.name}</div>
                <div className="mt-0.5 truncate text-[11px] text-[var(--text-muted,#94a3b8)]">{row.meta}</div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[14px] font-bold" style={{ color: row.ring }}>
                  {row.value}
                </span>
                <svg width="30" height="30" viewBox="0 0 36 36" className="shrink-0">
                  <circle cx="18" cy="18" r="14" fill="none" stroke="rgba(15,23,42,0.06)" strokeWidth="3" />
                  <circle
                    cx="18"
                    cy="18"
                    r="14"
                    fill="none"
                    stroke={row.ring}
                    strokeWidth="3"
                    strokeLinecap="round"
                    strokeDasharray={dashArray}
                    transform="rotate(-90 18 18)"
                  />
                </svg>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
