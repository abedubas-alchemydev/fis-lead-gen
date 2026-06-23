"use client";

import { useInView } from "@/lib/hooks/use-in-view";
import { useCountUp } from "@/lib/hooks/use-count-up";

type Pillar = {
  // When `value` is set the headline counts up to it on scroll-in (with an
  // optional prefix/suffix); otherwise `label` is rendered as static text.
  value?: number;
  prefix?: string;
  suffix?: string;
  display?: string;
  caption: string;
};

const PILLARS: Pillar[] = [
  { value: 3847, suffix: "+", caption: "broker-dealers tracked across SEC + FINRA" },
  { display: "Daily", caption: "Form BD + 17a-11 deficiency scans" },
  { display: "X-17A-5", caption: "clearing relationships extracted via LLM" },
  { value: 24, prefix: "< ", suffix: "h", caption: "from filing to scored prospect" },
];

const COUNT_DURATION_MS = 1200;

function StatHeadline({ pillar, start }: { pillar: Pillar; start: boolean }) {
  // Hook is called unconditionally (rules of hooks); for static pillars the
  // result is simply ignored in favour of `pillar.display`.
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

// Social-proof strip — four headline pillars. The numeric headlines count up
// the first time the strip scrolls into view (gated by `useInView`'s `start`),
// and show their final value immediately under reduced motion.
export function StatStrip() {
  const { ref, inView } = useInView<HTMLDivElement>();

  return (
    <section className="border-y border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/50 py-10 backdrop-blur-sm">
      <div ref={ref} className="mx-auto grid max-w-7xl grid-cols-2 gap-y-8 px-6 md:grid-cols-4">
        {PILLARS.map((pillar) => (
          <div
            key={pillar.caption}
            className="flex flex-col gap-1 md:border-r md:border-[var(--border,rgba(30,64,175,0.1))] md:pr-6 md:last:border-r-0"
          >
            <p className="text-2xl font-bold tracking-tight text-[var(--text,#0f172a)]">
              <StatHeadline pillar={pillar} start={inView} />
            </p>
            <p className="text-xs leading-snug text-[var(--text-muted,#94a3b8)]">{pillar.caption}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
