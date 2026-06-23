"use client";

import type { LucideIcon } from "lucide-react";
import { Building2, Radar, FileSearch, Timer } from "lucide-react";

import { useInView } from "@/lib/hooks/use-in-view";
import { useCountUp } from "@/lib/hooks/use-count-up";

// First fully-bright surface after the dark hero/wire — a thin credibility
// band that "steps into daylight". Closely follows stat-strip.tsx but
// elevated: an eyebrow, per-pillar icons, a shimmering top keyline, and one
// gold "money metric". Mock fixtures only — no fetch.

type Pillar = {
  icon: LucideIcon;
  // When `value` is set the headline counts up to it on scroll-in (with an
  // optional prefix/suffix); otherwise `display` is rendered as static text.
  value?: number;
  prefix?: string;
  suffix?: string;
  display?: string;
  caption: string;
  // The "money metric" — tinted with the institutional gold accent. At most
  // one per band (spec §1: gold is the sparing "money" highlight).
  gold?: boolean;
};

const PILLARS: Pillar[] = [
  {
    icon: Building2,
    value: 3847,
    suffix: "+",
    caption: "broker-dealers tracked across SEC + FINRA",
  },
  {
    icon: Radar,
    display: "Daily",
    caption: "Form BD + 17a-11 deficiency scans",
  },
  {
    icon: FileSearch,
    display: "X-17A-5",
    caption: "clearing relationships extracted via LLM",
  },
  {
    icon: Timer,
    value: 24,
    prefix: "< ",
    suffix: "h",
    caption: "from filing to scored prospect",
    gold: true,
  },
];

const COUNT_DURATION_MS = 1200;

function StatHeadline({ pillar, start }: { pillar: Pillar; start: boolean }) {
  // Hook is called unconditionally (rules of hooks); for static pillars the
  // result is simply ignored in favour of `pillar.display`. Mirrors the
  // StatHeadline approach in stat-strip.tsx.
  const counted = useCountUp(pillar.value ?? 0, COUNT_DURATION_MS, 0, start);

  if (pillar.value === undefined) {
    return <>{pillar.display}</>;
  }
  return (
    <>
      {pillar.prefix}
      <span className="tabular-nums">{counted}</span>
      {pillar.suffix}
    </>
  );
}

// Thin credibility band — four headline pillars. The numeric headlines count
// up the first time the band scrolls into view (gated by `useInView`'s
// `start`), and show their final value immediately under reduced motion.
export function RedoMetricsBand() {
  const { ref, inView } = useInView<HTMLDivElement>();

  return (
    <section
      aria-label="Coverage at a glance"
      className="relative border-y border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/50 px-6 py-16 backdrop-blur-sm"
    >
      {/* Faint gradient keyline that catches the light along the top seam — a
          slow shimmer sweep ties the daylight band back to the signal-gradient
          motif. Decorative; neutralised under reduced motion by .animate-shimmer's
          guard in globals.css. */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-px overflow-hidden"
      >
        <span className="animate-shimmer absolute inset-0 bg-gradient-to-r from-transparent via-[var(--accent,#6366f1)]/45 to-transparent" />
      </span>

      <div className="mx-auto max-w-7xl">
        {/* Eyebrow chrome — the recurring kicker that ties the redo sections
            together (spec §2). */}
        <p className="flex items-center justify-center gap-2 text-center text-xs font-semibold uppercase tracking-[0.22em] text-[var(--text-muted,#94a3b8)]">
          <span className="relative inline-flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--green,#10b981)]/70" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          Watching the wire, live
        </p>

        <div
          ref={ref}
          className="mt-12 grid grid-cols-2 gap-y-10 md:grid-cols-4"
        >
          {PILLARS.map((pillar) => {
            const Icon = pillar.icon;
            const accentVar = pillar.gold ? "--gold,#e8a838" : "--accent,#6366f1";
            return (
              <div
                key={pillar.caption}
                className="group flex flex-col gap-2.5 md:border-r md:border-[var(--border,rgba(30,64,175,0.1))] md:pr-6 md:last:border-r-0"
              >
                <span
                  className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] text-[var(--accent,#6366f1)] shadow-sm transition-transform duration-300 group-hover:-translate-y-0.5"
                  style={{ color: `var(${accentVar})` }}
                  aria-hidden
                >
                  <Icon className="h-[18px] w-[18px]" strokeWidth={2} />
                </span>
                <p
                  className="text-2xl font-bold tracking-tight tabular-nums text-[var(--text,#0f172a)] sm:text-3xl"
                  style={pillar.gold ? { color: `var(${accentVar})` } : undefined}
                >
                  <StatHeadline pillar={pillar} start={inView} />
                </p>
                <p className="text-xs leading-snug text-[var(--text-muted,#94a3b8)]">
                  {pillar.caption}
                </p>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
