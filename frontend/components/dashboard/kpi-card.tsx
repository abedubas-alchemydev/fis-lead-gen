import Link from "next/link";
import type { Route } from "next";
import type { LucideIcon } from "lucide-react";

type Tone = "navy" | "blue" | "danger" | "gold";

// Neutral white base with color-as-accent — the modern enterprise SaaS
// pattern (Stripe, Linear, Vercel). Each tone surfaces only via a thin
// top strip, the icon chip color, and an understated focus ring on hover.
const accentStripMap: Record<Tone, string> = {
  navy: "bg-navy",
  blue: "bg-blue",
  danger: "bg-danger",
  gold: "bg-gold"
};

const iconChipMap: Record<Tone, string> = {
  navy: "bg-navy/10 text-navy",
  blue: "bg-blue/10 text-blue",
  danger: "bg-danger/10 text-danger",
  gold: "bg-gold/15 text-[#a06d0f]"
};

const hoverRingMap: Record<Tone, string> = {
  navy: "group-hover/kpi:ring-navy/25",
  blue: "group-hover/kpi:ring-blue/25",
  danger: "group-hover/kpi:ring-danger/25",
  gold: "group-hover/kpi:ring-gold/35"
};

export function KpiCard({
  title,
  value,
  helper,
  tone,
  icon: Icon,
  href
}: {
  title: string;
  value: string;
  helper: string;
  tone: Tone;
  icon: LucideIcon;
  href?: Route;
}) {
  const content = (
    <article
      className={`group/kpi relative isolate overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-6 ring-1 ring-transparent transition-all duration-200 hover:border-slate-300 hover:shadow-[0_14px_38px_rgba(10,31,63,0.08)] ${hoverRingMap[tone]}`}
    >
      {/* Thin tone-colored strip at the top — the only saturated element on the card. */}
      <div
        aria-hidden
        className={`absolute inset-x-0 top-0 h-0.5 ${accentStripMap[tone]}`}
      />
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">
            {title}
          </p>
          <p className="mt-4 text-5xl font-semibold tabular-nums leading-none text-navy">
            {value}
          </p>
        </div>
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-transform duration-200 group-hover/kpi:scale-105 ${iconChipMap[tone]}`}
        >
          <Icon className="h-5 w-5" strokeWidth={2} />
        </div>
      </div>
      <p className="mt-5 text-sm leading-5 text-slate-500">{helper}</p>
    </article>
  );

  if (!href) {
    return content;
  }

  return (
    <Link
      href={href}
      className="block rounded-2xl transition duration-200 ease-out hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue focus-visible:ring-offset-2 focus-visible:ring-offset-white"
    >
      {content}
    </Link>
  );
}
