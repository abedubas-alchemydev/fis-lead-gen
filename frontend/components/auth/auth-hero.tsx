import Image from "next/image";
import type { LucideIcon } from "lucide-react";
import { Lock, Network, ScrollText, Users } from "lucide-react";

type Kpi = {
  icon: LucideIcon;
  label: string;
  meta: string;
  delayClass: string;
};

const KPIS: Kpi[] = [
  {
    icon: ScrollText,
    label: "Regulatory Intelligence",
    meta: "Forensic monitoring of primary-source filings.",
    delayClass: "delay-300",
  },
  {
    icon: Network,
    label: "Clearing & Custody Mapping",
    meta: "Revealing exactly where and how participants clear and settle.",
    delayClass: "delay-400",
  },
  {
    icon: Users,
    label: "Participant Discovery",
    meta: "Identifying executive shifts into high-conviction discovery.",
    delayClass: "delay-500",
  },
];

export function AuthHero() {
  return (
    <aside className="hidden bg-[#162635] text-white lg:flex lg:flex-col lg:justify-between lg:px-14 lg:pb-6 lg:pt-2">
      {/* Top row: centered logo */}
      <div className="flex justify-center">
        <Image
          src="/dox-logo.png"
          alt="DOX"
          width={384}
          height={384}
          priority
          className="h-72 w-72 object-contain lg:h-80 lg:w-80 2xl:h-96 2xl:w-96"
        />
      </div>

      {/* Middle: headline + KPIs */}
      <div className="relative my-auto">
        <p className="animate-fade-in-left max-w-md text-sm leading-relaxed text-white/60">
          Enterprise broker-dealer intelligence for surfacing clearing opportunities, tracking risk
          signals, and managing qualified prospects in one system.
        </p>

        <div className="mt-10 space-y-2">
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
