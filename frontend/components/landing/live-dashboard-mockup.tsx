"use client";

import { useEffect, useState } from "react";

import { useCountUp } from "@/lib/hooks/use-count-up";
import { useTilt } from "@/lib/hooks/use-tilt";

type Kpi = { label: string; value: number; color: string };

const KPIS: Kpi[] = [
  { label: "Active BDs", value: 3847, color: "bg-white/10" },
  { label: "New (30d)", value: 127, color: "bg-blue/20" },
  { label: "Hot Prospects", value: 312, color: "bg-gold/20" },
  { label: "Deficiencies", value: 18, color: "bg-danger/20" },
];

const BAR_WIDTHS = [40, 25, 15, 12, 8];
const BAR_COLORS = ["#1B5E9E", "#2d7fd3", "#E8A838", "#6d8097", "#27AE60"];

const COUNT_DURATION_MS = 1100;
const COUNT_STAGGER_MS = 120;

// How often the "live" highlight ring hops to the next KPI tile. Purely
// cosmetic — gives the mock a pulse without faking real data updates.
const HIGHLIGHT_INTERVAL_MS = 2600;

function KpiTile({ kpi, index, active }: { kpi: Kpi; index: number; active: boolean }) {
  const value = useCountUp(kpi.value, COUNT_DURATION_MS, index * COUNT_STAGGER_MS);
  return (
    <div
      className={`rounded-2xl ${kpi.color} p-4 backdrop-blur transition-all duration-500 ${
        active ? "ring-1 ring-white/40 ring-offset-0" : "ring-1 ring-transparent"
      }`}
    >
      <p className="text-[10px] uppercase tracking-[0.2em] text-white/50">{kpi.label}</p>
      <p className="mt-2 text-xl font-bold text-white tabular-nums">{value}</p>
    </div>
  );
}

export function LiveDashboardMockup() {
  const { ref, style } = useTilt<HTMLDivElement>(6);

  // Rotating highlight — cycles a subtle ring across the KPI tiles so the
  // panel feels live. Paused under reduced motion (no interval started), and
  // the interval is always cleared on unmount.
  const [activeKpi, setActiveKpi] = useState(0);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const id = window.setInterval(() => {
      setActiveKpi((i) => (i + 1) % KPIS.length);
    }, HIGHLIGHT_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="animate-fade-in-right delay-300 relative mt-4 lg:mt-0">
      <div
        ref={ref}
        style={style}
        className="animate-float relative rounded-[28px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/80 p-2 shadow-2xl shadow-[var(--accent,#6366f1)]/10 backdrop-blur"
      >
        <div className="rounded-[22px] bg-gradient-to-br from-navy via-[#0f2d52] to-[#163768] p-5 sm:p-8">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <p className="text-[10px] uppercase tracking-[0.3em] text-white/50">Live Platform</p>
              <p className="mt-1 text-sm font-semibold text-white">DOX Intelligence Workspace</p>
            </div>
            <div className="h-2 w-2 rounded-full bg-success animate-pulse" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            {KPIS.map((kpi, i) => (
              <KpiTile key={kpi.label} kpi={kpi} index={i} active={i === activeKpi} />
            ))}
          </div>
          <div className="mt-4 rounded-2xl bg-white/5 p-4">
            <p className="text-[10px] uppercase tracking-[0.2em] text-white/40">Clearing Distribution</p>
            <div className="mt-3 flex gap-1">
              {BAR_WIDTHS.map((w, i) => (
                <div
                  key={i}
                  className="h-2 rounded-full animate-bar-grow"
                  style={{
                    width: `${w}%`,
                    backgroundColor: BAR_COLORS[i],
                    ["--bar-w" as string]: `${w}%`,
                    animationDelay: `${500 + i * 120}ms`,
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="pointer-events-none absolute -inset-4 -z-10 rounded-[36px] bg-gradient-to-br from-blue/15 via-transparent to-gold/10 blur-2xl" />
    </div>
  );
}
