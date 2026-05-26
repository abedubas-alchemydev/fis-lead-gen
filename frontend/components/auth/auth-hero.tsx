import Image from "next/image";
import type { LucideIcon } from "lucide-react";
import { Activity, Layers, Lock, Network } from "lucide-react";

const GRAIN_DATA_URI =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.6 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")";

type Kpi = {
  icon: LucideIcon;
  label: string;
  meta: string;
  delayClass: string;
};

const KPIS: Kpi[] = [
  {
    icon: Network,
    label: "Broker-dealers tracked",
    meta: "Updated this morning · 06:14 ET",
    delayClass: "delay-300",
  },
  {
    icon: Layers,
    label: "Cleared assets mapped",
    meta: "Across 48 clearing relationships",
    delayClass: "delay-400",
  },
  {
    icon: Activity,
    label: "Filings ingested",
    meta: "FOCUS · X-17A-5 · BrokerCheck",
    delayClass: "delay-500",
  },
];

export function AuthHero() {
  return (
    <aside className="relative hidden overflow-hidden bg-[#162635] text-white lg:flex lg:flex-col lg:justify-between lg:px-14 lg:py-12">
      {/* Texture layers */}
      <div className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-blue/15 blur-3xl" aria-hidden />
      <div className="pointer-events-none absolute -bottom-24 -left-16 h-56 w-56 rounded-full bg-gold/12 blur-3xl" aria-hidden />
      <div
        className="pointer-events-none absolute inset-0 mix-blend-overlay opacity-[0.18]"
        style={{ backgroundImage: GRAIN_DATA_URI }}
        aria-hidden
      />

      {/* Top row: logo + tag */}
      <div className="relative flex items-center">
        <Image
          src="/logo.png"
          alt="DOX"
          width={120}
          height={38}
          priority
          className="h-9 w-auto"
        />
        <span className="ml-4 border-l border-white/15 pl-4 text-[10px] font-semibold uppercase tracking-[0.32em] text-white/55">
          Institutional Intelligence
        </span>
      </div>

      {/* Middle: headline + KPIs */}
      <div className="relative my-auto">
        <h1 className="animate-fade-in-left max-w-md text-[clamp(1.875rem,1.4vw+1.4rem,2.5rem)] font-semibold leading-[1.1] tracking-tight text-white">
          The clearing layer, mapped.
        </h1>
        <p className="animate-fade-in-left delay-150 mt-4 max-w-md text-sm leading-relaxed text-white/60">
          Primary-source filings, custody chains, and counterparty graphs — refreshed continuously,
          surfaced as one unified research layer.
        </p>

        <div className="mt-10 space-y-3">
          {KPIS.map((kpi) => {
            const Icon = kpi.icon;
            return (
              <div
                key={kpi.label}
                className={`animate-fade-in-left ${kpi.delayClass} flex max-w-md items-center gap-4 rounded-2xl border border-white/10 bg-white/5 px-5 py-4 backdrop-blur`}
              >
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10">
                  <Icon className="h-5 w-5 text-white/80" aria-hidden />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-white/90">{kpi.label}</p>
                  <p className="mt-0.5 text-xs text-white/45">{kpi.meta}</p>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Bottom: trust strip */}
      <div className="relative flex items-center gap-3 text-xs text-white/45">
        <Lock className="h-3.5 w-3.5" aria-hidden />
        <span>Trusted by clearing operators · Compliance-grade access controls</span>
      </div>
    </aside>
  );
}
