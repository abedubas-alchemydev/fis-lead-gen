"use client";

import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { Activity, BarChart3, Lock, Shield, Target, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import {
  DistributionPanel,
  ProspectPanel,
  SparklinePanel,
} from "@/components/landing/feature-visuals";

type Feature = {
  id: string;
  icon: LucideIcon;
  title: string;
  desc: string;
  iconBg: string;
  visual: ReactNode;
};

// All numbers below are local mock fixtures — see feature-visuals.tsx.
const FEATURES: Feature[] = [
  {
    id: "financial-health",
    icon: BarChart3,
    title: "Financial Health Scoring",
    desc: "Net capital analysis, YoY growth tracking, and automated health classification from FOCUS reports.",
    iconBg: "bg-[var(--accent,#6366f1)]/10 text-[var(--accent,#6366f1)]",
    visual: (
      <SparklinePanel
        title="Net Capital"
        value="$48.2M"
        trend={{ direction: "up", label: "+38%" }}
        helper="Meridian Trade Group · 4-quarter trend"
        data={{
          gradientId: "tour-spark-health",
          stroke: "#60a5fa",
          stop: "#3b82f6",
          area: "M0 28 L20 24 L40 26 L60 18 L80 20 L100 14 L120 18 L140 10 L160 14 L180 8 L200 12 L200 40 L0 40 Z",
          line: "M0 28 L20 24 L40 26 L60 18 L80 20 L100 14 L120 18 L140 10 L160 14 L180 8 L200 12",
        }}
      />
    ),
  },
  {
    id: "prospect-scoring",
    icon: Target,
    title: "Prospect Scoring Engine",
    desc: "Weighted scoring model with configurable factors. Hot, Warm, and Cold prospect classification.",
    iconBg: "bg-gold/10 text-[#b88520]",
    visual: (
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
    desc: "Automated scan for Form BD and 17a-11 deficiency filings with real-time alert routing.",
    iconBg: "bg-success/10 text-success",
    visual: (
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
    desc: "LLM-powered extraction of clearing partners from X-17A-5 annual audit PDFs.",
    iconBg: "bg-[var(--accent,#6366f1)]/10 text-[var(--accent,#6366f1)]",
    visual: (
      <DistributionPanel
        title="Clearing market — provider distribution"
        helper="Extracted from X-17A-5 audit PDFs"
        rows={[
          { provider: "Pershing LLC", firms: 612, percentage: 31.4, swatch: "#1e3a8a", fillA: "#1e3a8a", fillB: "#3b82f6" },
          { provider: "Apex Clearing", firms: 388, percentage: 19.9, swatch: "#ef4444", fillA: "#b91c1c", fillB: "#ef4444" },
          { provider: "RBC Clearing", firms: 247, percentage: 12.7, swatch: "#fbbf24", fillA: "#d97706", fillB: "#fbbf24" },
          { provider: "Self-clearing", firms: 184, percentage: 9.4, swatch: "#10b981", fillA: "#047857", fillB: "#10b981" },
          { provider: "Hilltop Securities", firms: 96, percentage: 4.9, swatch: "#8b5cf6", fillA: "#6d28d9", fillB: "#8b5cf6" },
        ]}
      />
    ),
  },
  {
    id: "data-export",
    icon: Shield,
    title: "Controlled Data Export",
    desc: "Restricted CSV with permitted fields only. 100-record cap and 3-export daily limit.",
    iconBg: "bg-danger/10 text-danger",
    visual: (
      <DistributionPanel
        title="Export usage — today"
        helper="100-record cap · 3 exports per user per day"
        rows={[
          { provider: "Permitted fields", firms: 9, percentage: 100, swatch: "#10b981", fillA: "#047857", fillB: "#10b981" },
          { provider: "Records per export", firms: 100, percentage: 80, swatch: "#3b82f6", fillA: "#1e3a8a", fillB: "#3b82f6" },
          { provider: "Exports used", firms: 2, percentage: 66, swatch: "#f59e0b", fillA: "#d97706", fillB: "#fbbf24" },
          { provider: "Watermarked", firms: 1, percentage: 100, swatch: "#8b5cf6", fillA: "#6d28d9", fillB: "#8b5cf6" },
        ]}
      />
    ),
  },
  {
    id: "contact-enrichment",
    icon: Zap,
    title: "Contact Enrichment",
    desc: "On-demand enrichment for executive email, phone, and LinkedIn data.",
    iconBg: "bg-[var(--accent,#6366f1)]/10 text-[var(--accent,#6366f1)]",
    visual: (
      <SparklinePanel
        title="Contacts enriched"
        value="1,284"
        trend={{ direction: "up", label: "+12%" }}
        helper="Email · phone · LinkedIn · this month"
        data={{
          gradientId: "tour-spark-enrich",
          stroke: "#a78bfa",
          stop: "#8b5cf6",
          area: "M0 30 L20 26 L40 28 L60 22 L80 18 L100 20 L120 14 L140 16 L160 10 L180 12 L200 6 L200 40 L0 40 Z",
          line: "M0 30 L20 26 L40 28 L60 22 L80 18 L100 20 L120 14 L140 16 L160 10 L180 12 L200 6",
        }}
      />
    ),
  },
];

export function FeatureTour() {
  const [active, setActive] = useState(0);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Roving tabindex / arrow-key navigation per the WAI-ARIA tabs pattern.
  // Left/Right cycle (wrapping), Home/End jump to the ends; the newly active
  // tab is focused so keyboard users land on it.
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

  return (
    <section className="relative py-24">
      <div className="mx-auto max-w-7xl px-6">
        <div className="text-center">
          <p className="text-sm font-semibold uppercase tracking-[0.25em] text-[var(--accent,#6366f1)]">
            Platform Capabilities
          </p>
          <h2 className="mx-auto mt-4 max-w-2xl text-3xl font-bold tracking-tight text-[var(--text,#0f172a)] sm:text-4xl">
            Every signal, one workspace
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-base text-[var(--text-muted,#94a3b8)]">
            From new BD registrations to clearing partner changes, the platform monitors
            SEC and FINRA in real time and delivers qualified prospects inside one system.
          </p>
        </div>

        <div className="mt-16 grid gap-8 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
          {/* Tab rail — vertical on desktop, horizontal segmented scroll on mobile. */}
          <div
            role="tablist"
            aria-label="Platform capabilities"
            aria-orientation="vertical"
            onKeyDown={onKeyDown}
            className="-mx-6 flex gap-2 overflow-x-auto px-6 pb-2 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0"
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
                  role="tab"
                  id={`feature-tab-${feature.id}`}
                  aria-selected={selected}
                  aria-controls={`feature-panel-${feature.id}`}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => setActive(index)}
                  className={`group flex shrink-0 items-start gap-4 rounded-2xl border p-4 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]/40 lg:w-full ${
                    selected
                      ? "border-[var(--accent,#6366f1)]/30 bg-[var(--surface,#ffffff)] shadow-[0_8px_24px_rgba(99,102,241,0.12)]"
                      : "border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/50 hover:-translate-y-0.5 hover:border-[var(--accent,#6366f1)]/20 hover:shadow-md"
                  }`}
                >
                  <div className={`inline-flex shrink-0 rounded-xl p-2.5 ${feature.iconBg}`}>
                    <Icon className="h-5 w-5" />
                  </div>
                  <div className="min-w-0">
                    <h3 className="text-[15px] font-semibold text-[var(--text,#0f172a)]">{feature.title}</h3>
                    <p className="mt-1 hidden text-[13px] leading-relaxed text-[var(--text-muted,#94a3b8)] lg:block">
                      {feature.desc}
                    </p>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Panels — only the active one is mounted/visible; others are hidden. */}
          <div className="relative">
            {FEATURES.map((feature, index) => (
              <div
                key={feature.id}
                role="tabpanel"
                id={`feature-panel-${feature.id}`}
                aria-labelledby={`feature-tab-${feature.id}`}
                hidden={index !== active}
              >
                {index === active ? (
                  <div className="animate-scale-in">
                    <p className="mb-4 text-sm leading-relaxed text-[var(--text-dim,#475569)] lg:hidden">
                      {feature.desc}
                    </p>
                    {feature.visual}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
