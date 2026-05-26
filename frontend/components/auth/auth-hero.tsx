import Image from "next/image";
import type { LucideIcon } from "lucide-react";
import { Activity, Layers, Lock, Network } from "lucide-react";

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
    <aside className="hidden bg-[#162635] text-white lg:flex lg:flex-col lg:justify-between lg:px-14 lg:pb-12 lg:pt-4">
      {/* Top row: centered logo */}
      <div className="flex justify-center">
        <Image
          src="/dox-logo.png"
          alt="DOX"
          width={384}
          height={384}
          priority
          className="h-80 w-80 object-contain lg:h-96 lg:w-96"
        />
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
