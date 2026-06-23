"use client";

import { useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  BarChart3,
  Lock,
  Shield,
  Target,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { useTilt } from "@/lib/hooks/use-tilt";

// Landing redo §9 (5) — interactive product tour, the "what it does"
// centerpiece. Built on the feature-tour.tsx + feature-visuals.tsx idioms but
// restyled to the redo: an accent eyebrow + oversized H2, a vertical tab rail
// (WAI-ARIA tabs pattern, roving tabindex, arrow keys) feeding animated
// product-visual panels that echo the real dashboard — sparkline DRAW
// (.animate-draw with a per-path --draw-len), distribution BARS (.animate-bar-
// grow, staggered), and score RINGS. Each panel scale-ins / de-blurs on switch
// and gets a desktop tilt + a single "we're reading the filing" scan beam.
//
// Client "use client" leaf. ALL data is local mock fixtures — this file never
// fetches, never imports the apiRequest-backed dashboard components, so the
// landing bundle stays fetch-free (same discipline as feature-visuals.tsx).

// ── Mini product visuals (ported SVG idioms, redo-styled) ───────────────────

// Sparkline (idiom: components/dashboard/kpi-card.tsx). Area + line pair in a
// 0 0 200 40 viewBox with a vertical gradient fill; the line is drawn on switch
// via .animate-draw (stroke-dashoffset reveal). `drawLen` is the approximate
// path length — it just needs to be ≥ the real length for the dash to fully
// hide then reveal, so a generous constant is fine here.
type SparkData = {
  stroke: string;
  stop: string;
  area: string;
  line: string;
  drawLen: number;
};

function SparklinePanel({
  uid,
  title,
  value,
  trend,
  helper,
  data,
}: {
  uid: string;
  title: string;
  value: string;
  trend: { direction: "up" | "down"; label: string };
  helper: string;
  data: SparkData;
}) {
  const gradientId = `${uid}-fill`;
  // Up-trend chip text darkened from emerald-500 (#10b981, 2.26:1 on the pale
  // emerald-500/12 pill — fails AA) to emerald-700 (#047857, ~4.9:1, passes)
  // while keeping the positive-green read. Pill bg unchanged.
  const trendClass =
    trend.direction === "up"
      ? "bg-emerald-500/12 text-emerald-700"
      : "bg-red-500/12 text-red-500";

  return (
    <article className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#fff)] p-5 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
      <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-dim,#475569)]">
        {title}
      </div>
      <div className="mb-1.5 mt-2 flex items-baseline gap-2.5 text-[34px] font-bold tracking-[-0.02em] tabular-nums text-[var(--text,#0f172a)]">
        {value}
        <span
          className={`inline-flex items-center gap-1 rounded-md px-2 py-[3px] text-[12px] font-semibold ${trendClass}`}
        >
          <svg
            width="10"
            height="10"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            viewBox="0 0 24 24"
            aria-hidden
          >
            {trend.direction === "up" ? (
              <path d="M7 17l10-10M7 7h10v10" />
            ) : (
              <path d="M7 7l10 10M7 17h10V7" />
            )}
          </svg>
          {trend.label}
        </span>
      </div>
      <p className="text-[12px] text-[var(--text-dim,#475569)]">{helper}</p>
      <svg
        className="mt-3 inline h-9 w-full align-baseline"
        viewBox="0 0 200 40"
        preserveAspectRatio="none"
        aria-hidden
      >
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={data.stop} stopOpacity="0.35" />
            <stop offset="100%" stopColor={data.stop} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={data.area} fill={`url(#${gradientId})`} />
        <path
          className="animate-draw"
          d={data.line}
          fill="none"
          stroke={data.stroke}
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ ["--draw-len" as string]: data.drawLen }}
        />
      </svg>
    </article>
  );
}

// Distribution bars (idiom: clearing-distribution-chart.tsx). Rows of
// [swatch] [label · N firms] [track] [percent]; the track width is normalised
// against the leading row and built with .animate-bar-grow (--bar-w per row,
// staggered animationDelay) so the bars sweep out on panel switch.
type BarRow = {
  label: string;
  meta: string;
  percentage: number;
  swatch: string;
  fillA: string;
  fillB: string;
};

function DistributionPanel({
  title,
  helper,
  rows,
}: {
  title: string;
  helper: string;
  rows: BarRow[];
}) {
  const maxPercent = Math.max(...rows.map((r) => r.percentage));
  const scale = (p: number) => (maxPercent > 0 ? (p / maxPercent) * 92 : 0);

  return (
    <div className="flex h-full flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#fff)] p-5 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
      <div className="mb-3">
        <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
          {title}
        </h3>
        <p className="mt-0.5 text-[12px] text-[var(--text-dim,#475569)]">{helper}</p>
      </div>
      <div className="flex-1">
        {rows.map((row, index) => {
          const width = `${scale(row.percentage)}%`;
          return (
            <div
              key={row.label}
              className="grid grid-cols-[10px_minmax(0,42%)_minmax(60px,1fr)_44px] items-center gap-3.5 border-t border-[var(--border,rgba(30,64,175,0.1))] py-2.5 first:border-t-0"
            >
              <span
                className="h-2.5 w-2.5 rounded-[3px]"
                style={{ backgroundColor: row.swatch }}
              />
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate text-[13px] font-medium text-[var(--text,#0f172a)]">
                  {row.label}
                </span>
                <span className="shrink-0 whitespace-nowrap text-[11px] text-[var(--text-dim,#475569)]">
                  {row.meta}
                </span>
              </div>
              <div className="relative h-1.5 overflow-hidden rounded-full bg-[var(--surface-2,#f1f6fd)]">
                <div
                  className="animate-bar-grow h-full rounded-full"
                  style={{
                    width,
                    background: `linear-gradient(90deg, ${row.fillA}, ${row.fillB})`,
                    ["--bar-w" as string]: width,
                    animationDelay: `${index * 110}ms`,
                  }}
                />
              </div>
              <span className="text-right text-[13px] font-semibold tabular-nums text-[var(--text,#0f172a)]">
                {row.percentage.toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Prospect rows + score ring (idiom: top-prospects-card.tsx). Avatar initials,
// firm + meta, value, and a two-circle ring in a 0 0 36 36 viewBox where
// strokeDasharray = `${(pct/100)*88} 88` on r=14, rotate(-90). The progress arc
// draws on switch via .animate-draw (--draw-len = the 88-unit circumference).
type ProspectRow = {
  name: string;
  meta: string;
  value: string;
  pct: number;
  gradient: string;
  ring: string;
};

function ProspectPanel({
  title,
  helper,
  rows,
}: {
  title: string;
  helper: string;
  rows: ProspectRow[];
}) {
  return (
    <div className="flex h-full flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#fff)] p-5 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
      <div className="mb-3">
        <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
          {title}
        </h3>
        <p className="mt-0.5 text-[12px] text-[var(--text-dim,#475569)]">{helper}</p>
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
                <div className="truncate text-[13.5px] font-semibold text-[var(--text,#0f172a)]">
                  {row.name}
                </div>
                <div className="mt-0.5 truncate text-[11px] text-[var(--text-dim,#475569)]">
                  {row.meta}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[14px] font-bold tabular-nums" style={{ color: row.ring }}>
                  {row.value}
                </span>
                <svg width="30" height="30" viewBox="0 0 36 36" className="shrink-0" aria-hidden>
                  <circle cx="18" cy="18" r="14" fill="none" stroke="rgba(15,23,42,0.06)" strokeWidth="3" />
                  <circle
                    className="animate-draw"
                    cx="18"
                    cy="18"
                    r="14"
                    fill="none"
                    stroke={row.ring}
                    strokeWidth="3"
                    strokeLinecap="round"
                    strokeDasharray={dashArray}
                    transform="rotate(-90 18 18)"
                    style={{ ["--draw-len" as string]: 88 }}
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

// ── Feature fixtures — all numbers hardcoded per the section spec ───────────

type Feature = {
  id: string;
  icon: LucideIcon;
  title: string;
  desc: string;
  iconBg: string;
  visual: (uid: string) => ReactNode;
};

const FEATURES: Feature[] = [
  {
    id: "financial-health",
    icon: BarChart3,
    title: "Financial Health Scoring",
    desc: "Net-capital analysis, YoY growth tracking, and automated health classification straight from FOCUS reports.",
    iconBg: "bg-[var(--accent,#6366f1)]/10 text-[var(--accent,#6366f1)]",
    visual: (uid) => (
      <SparklinePanel
        uid={uid}
        title="Net Capital"
        value="$48.2M"
        trend={{ direction: "up", label: "+38%" }}
        helper="Meridian Trade Group · 4-quarter trend"
        data={{
          stroke: "#60a5fa",
          stop: "#3b82f6",
          area: "M0 28 L20 24 L40 26 L60 18 L80 20 L100 14 L120 18 L140 10 L160 14 L180 8 L200 12 L200 40 L0 40 Z",
          line: "M0 28 L20 24 L40 26 L60 18 L80 20 L100 14 L120 18 L140 10 L160 14 L180 8 L200 12",
          drawLen: 230,
        }}
      />
    ),
  },
  {
    id: "prospect-scoring",
    icon: Target,
    title: "Prospect Scoring Engine",
    desc: "A weighted scoring model with configurable factors, ranking every firm Hot, Warm, or Cold by who's worth your next call.",
    iconBg: "bg-gold/10 text-[#b88520]",
    visual: () => (
      <ProspectPanel
        title="Top scored prospects"
        helper="Weighted model · largest opportunity first"
        rows={[
          { name: "Brightwater Partners", meta: "CRD #182204 · NY · Hot", value: "92", pct: 92, gradient: "linear-gradient(135deg,#6366f1,#8b5cf6)", ring: "#6366f1" },
          { name: "Atlas Capital Markets", meta: "CRD #154820 · IL · Hot", value: "87", pct: 87, gradient: "linear-gradient(135deg,#10b981,#06b6d4)", ring: "#10b981" },
          { name: "Sterling Cove Securities", meta: "CRD #201145 · TX · Warm", value: "74", pct: 74, gradient: "linear-gradient(135deg,#ec4899,#f59e0b)", ring: "#ec4899" },
          { name: "Northpoint Execution", meta: "CRD #199801 · CA · Warm", value: "68", pct: 68, gradient: "linear-gradient(135deg,#3b82f6,#8b5cf6)", ring: "#3b82f6" },
        ]}
      />
    ),
  },
  {
    id: "filing-monitor",
    icon: Activity,
    title: "Daily Filing Monitor",
    desc: "An automated daily scan for Form BD and 17a-11 deficiency filings, with alerts routed the moment they hit the wire.",
    iconBg: "bg-success/10 text-success",
    visual: () => (
      <ProspectPanel
        title="Today's filing alerts"
        helper="Form BD + 17a-11 · routed by severity"
        rows={[
          { name: "Cedar Ridge Securities", meta: "Form BD · new registration", value: "New", pct: 100, gradient: "linear-gradient(135deg,#3b82f6,#6366f1)", ring: "#3b82f6" },
          { name: "Atlas Capital Markets", meta: "17a-11 · net-capital deficiency", value: "High", pct: 90, gradient: "linear-gradient(135deg,#ef4444,#f59e0b)", ring: "#ef4444" },
          { name: "Hollis & Crane LLC", meta: "17a-11 · early-warning threshold", value: "Warn", pct: 60, gradient: "linear-gradient(135deg,#f59e0b,#fbbf24)", ring: "#f59e0b" },
          { name: "Vanguard Reach Capital", meta: "FOCUS · health upgraded", value: "Info", pct: 40, gradient: "linear-gradient(135deg,#10b981,#06b6d4)", ring: "#10b981" },
        ]}
      />
    ),
  },
  {
    id: "clearing-map",
    icon: Lock,
    title: "Clearing Relationship Map",
    desc: "LLM-powered extraction of clearing partners from X-17A-5 annual audit PDFs — see who clears for whom across the market.",
    iconBg: "bg-[var(--accent,#6366f1)]/10 text-[var(--accent,#6366f1)]",
    visual: () => (
      <DistributionPanel
        title="Clearing market — provider distribution"
        helper="Extracted from X-17A-5 audit PDFs"
        rows={[
          { label: "Pershing LLC", meta: "· 612 firms", percentage: 31.4, swatch: "#1e3a8a", fillA: "#1e3a8a", fillB: "#3b82f6" },
          { label: "Apex Clearing", meta: "· 388 firms", percentage: 19.9, swatch: "#ef4444", fillA: "#b91c1c", fillB: "#ef4444" },
          { label: "RBC Clearing", meta: "· 247 firms", percentage: 12.7, swatch: "#fbbf24", fillA: "#d97706", fillB: "#fbbf24" },
          { label: "Self-clearing", meta: "· 184 firms", percentage: 9.4, swatch: "#10b981", fillA: "#047857", fillB: "#10b981" },
          { label: "Hilltop Securities", meta: "· 96 firms", percentage: 4.9, swatch: "#8b5cf6", fillA: "#6d28d9", fillB: "#8b5cf6" },
        ]}
      />
    ),
  },
  {
    id: "data-export",
    icon: Shield,
    title: "Controlled Data Export",
    desc: "Restricted CSV with permitted fields only — a 100-record cap, a 3-export daily limit, and a watermark on every file.",
    iconBg: "bg-danger/10 text-danger",
    visual: () => (
      <DistributionPanel
        title="Export guardrails"
        helper="PRD-locked · enforced server-side"
        rows={[
          { label: "Permitted fields", meta: "· 9 columns", percentage: 100, swatch: "#10b981", fillA: "#047857", fillB: "#10b981" },
          { label: "Records per export", meta: "· 100 cap", percentage: 80, swatch: "#3b82f6", fillA: "#1e3a8a", fillB: "#3b82f6" },
          { label: "Exports per day", meta: "· 3 limit", percentage: 60, swatch: "#f59e0b", fillA: "#d97706", fillB: "#fbbf24" },
          { label: "Watermarked", meta: "· every file", percentage: 100, swatch: "#8b5cf6", fillA: "#6d28d9", fillB: "#8b5cf6" },
        ]}
      />
    ),
  },
  {
    id: "contact-enrichment",
    icon: Zap,
    title: "Contact Enrichment",
    desc: "On-demand enrichment for executive email, phone, and LinkedIn data — so the right person is one click from your inbox.",
    iconBg: "bg-[var(--accent,#6366f1)]/10 text-[var(--accent,#6366f1)]",
    visual: (uid) => (
      <SparklinePanel
        uid={uid}
        title="Contacts enriched"
        value="1,284"
        trend={{ direction: "up", label: "+12%" }}
        helper="Email · phone · LinkedIn · this month"
        data={{
          stroke: "#a78bfa",
          stop: "#8b5cf6",
          area: "M0 30 L20 26 L40 28 L60 22 L80 18 L100 20 L120 14 L140 16 L160 10 L180 12 L200 6 L200 40 L0 40 Z",
          line: "M0 30 L20 26 L40 28 L60 22 L80 18 L100 20 L120 14 L140 16 L160 10 L180 12 L200 6",
          drawLen: 230,
        }}
      />
    ),
  },
];

// One tilted, scanned shell wrapping the active panel. Keyed on the active id by
// the parent so React remounts it on switch — that re-triggers .animate-scale-
// in / .animate-draw / .animate-bar-grow (which are `both`-filled one-shots and
// otherwise wouldn't replay). useTilt no-ops on touch / reduced-motion.
function PanelStage({ children }: { children: ReactNode }) {
  const { ref, style } = useTilt<HTMLDivElement>(4);
  return (
    <div
      ref={ref}
      style={style}
      className="animate-reveal-blur group relative overflow-hidden rounded-[24px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)]/40 p-3 shadow-[0_8px_30px_rgba(15,23,42,0.06)] backdrop-blur-sm sm:p-4"
    >
      {/* "We're reading the filing" scan beam — one horizontal line sweeping
          down the panel. Decorative; .animate-scan is neutralised under
          reduced motion by its guard in globals.css. */}
      <span
        aria-hidden
        className="animate-scan pointer-events-none absolute inset-x-0 top-0 z-10 h-px bg-gradient-to-r from-transparent via-[var(--accent,#6366f1)]/60 to-transparent"
      />
      {/* Soft accent glow bleed behind the card for depth. */}
      <span
        aria-hidden
        className="pointer-events-none absolute -inset-px -z-10 rounded-[24px] bg-gradient-to-br from-[var(--accent,#6366f1)]/5 via-transparent to-[var(--accent-2,#8b5cf6)]/5"
      />
      <div className="animate-scale-in">{children}</div>
    </div>
  );
}

export function RedoFeatureShowcase() {
  const [active, setActive] = useState(0);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  // Stable per-instance prefix so the sparkline gradient ids are unique and
  // SSR-deterministic (avoids hydration id mismatches).
  const uid = useId();

  // Roving tabindex / arrow-key navigation per the WAI-ARIA tabs pattern.
  // Left/Up + Right/Down cycle (wrapping), Home/End jump to the ends; the newly
  // active tab is focused so keyboard users land on it.
  const onKeyDown = (event: React.KeyboardEvent) => {
    const last = FEATURES.length - 1;
    let next = active;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = active === last ? 0 : active + 1;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = active === 0 ? last : active - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    else return;

    event.preventDefault();
    setActive(next);
    tabRefs.current[next]?.focus();
  };

  const activeFeature = FEATURES[active];

  return (
    <section id="features" aria-labelledby="features-heading" className="relative px-6 py-24">
      <div className="mx-auto max-w-7xl">
        {/* Header — centered eyebrow chrome + oversized gradient-capable H2. */}
        <div className="text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-[var(--accent,#6366f1)]">
            Platform capabilities
          </p>
          <h2
            id="features-heading"
            className="mx-auto mt-4 max-w-3xl text-3xl font-bold tracking-tight text-[var(--text,#0f172a)] sm:text-4xl lg:text-5xl"
          >
            Every signal, one workspace
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-[var(--text-dim,#475569)]">
            From new BD registrations to clearing-partner changes, DOX watches SEC and
            FINRA in real time and delivers ranked, qualified prospects inside one system.
          </p>
        </div>

        <div className="mt-16 grid gap-8 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
          {/* Tab rail — vertical on desktop, horizontal scroll-snap on mobile. */}
          <div
            role="tablist"
            aria-label="Platform capabilities"
            aria-orientation="vertical"
            onKeyDown={onKeyDown}
            className="-mx-6 flex snap-x snap-mandatory gap-2 overflow-x-auto px-6 pb-2 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0"
          >
            {FEATURES.map((feature, index) => {
              const Icon = feature.icon;
              const selected = index === active;
              return (
                <button
                  key={feature.id}
                  ref={(el) => {
                    tabRefs.current[index] = el;
                  }}
                  type="button"
                  role="tab"
                  id={`feature-tab-${feature.id}`}
                  aria-selected={selected}
                  aria-controls={`feature-panel-${feature.id}`}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => setActive(index)}
                  className={`group flex w-[78vw] shrink-0 snap-start items-start gap-4 rounded-2xl border p-4 text-left transition-all duration-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]/40 sm:w-[60vw] lg:w-full ${
                    selected
                      ? "border-[var(--accent,#6366f1)]/30 bg-[var(--surface,#fff)] shadow-[0_8px_24px_rgba(99,102,241,0.12)]"
                      : "border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#fff)]/50 hover:-translate-y-0.5 hover:border-[var(--accent,#6366f1)]/20 hover:shadow-md"
                  }`}
                >
                  <span
                    className={`inline-flex shrink-0 rounded-xl p-2.5 transition-transform duration-300 group-hover:scale-105 ${feature.iconBg}`}
                    aria-hidden
                  >
                    <Icon className="h-5 w-5" strokeWidth={2} />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[15px] font-semibold text-[var(--text,#0f172a)]">
                      {feature.title}
                    </span>
                    <span className="mt-1 hidden text-[13px] leading-relaxed text-[var(--text-dim,#475569)] lg:block">
                      {feature.desc}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>

          {/* Panels — only the active one is mounted/visible; the rest carry
              `hidden`. The active panel is keyed on its id so the entrance +
              draw animations replay on every switch. */}
          <div className="relative">
            {FEATURES.map((feature, index) => (
              <div
                key={feature.id}
                role="tabpanel"
                id={`feature-panel-${feature.id}`}
                aria-labelledby={`feature-tab-${feature.id}`}
                tabIndex={0}
                hidden={index !== active}
                className="focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]/40 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg,#eaf3ff)]"
              >
                {index === active ? (
                  <>
                    {/* Mobile-only description (the rail hides it < lg). */}
                    <p className="mb-4 text-sm leading-relaxed text-[var(--text-dim,#475569)] lg:hidden">
                      {feature.desc}
                    </p>
                    <PanelStage key={`stage-${activeFeature.id}`}>
                      {feature.visual(`${uid}-${feature.id}`)}
                    </PanelStage>
                  </>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
