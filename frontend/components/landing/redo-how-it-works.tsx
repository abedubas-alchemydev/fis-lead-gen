import { FileSearch, Gauge, PhoneCall } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Reveal } from "@/components/landing/reveal";

// redo-how-it-works — the narrative beat. Stays a SERVER component; the per-step
// entrance is delegated to the client <Reveal> wrapper (staggered 120ms each),
// and the signature touch — a drawn connector line linking the three cards on
// md+ — uses the shared `.animate-draw` utility from globals.css (resting state
// is fully drawn; under reduced motion it simply stays drawn). Mock copy only.
// Spec: reports/landing-redo-spec.md §9 (6).

type Step = {
  icon: LucideIcon;
  step: string;
  title: string;
  desc: string;
};

const STEPS: Step[] = [
  {
    icon: FileSearch,
    step: "01",
    title: "A filing lands",
    desc: "Form BD, 17a-11, and X-17A-5 filings are pulled from SEC and FINRA the moment they post — no manual checking.",
  },
  {
    icon: Gauge,
    step: "02",
    title: "It gets scored",
    desc: "Net capital, YoY growth, clearing arrangement, and your weighted factors classify each firm Hot, Warm, or Cold.",
  },
  {
    icon: PhoneCall,
    step: "03",
    title: "You make the call",
    desc: "Qualified prospects surface with enriched executive contacts, ranked by who's worth your outreach today.",
  },
];

// Horizontal connector path drawn behind the row on md+. It threads left→right
// at the icon-tile's vertical centre with two gentle dips between the cards, so
// the three steps read as one continuous wire. `--draw-len` is the measured
// path length (slightly padded) so `.animate-draw` strokes it on in cleanly.
const CONNECTOR_PATH = "M 2 30 C 130 30 170 58 300 58 S 470 30 598 30";
const CONNECTOR_LEN = 660;

export function RedoHowItWorks() {
  return (
    <section className="relative border-t border-[var(--border,rgba(30,64,175,0.1))] px-6 py-24">
      <div className="mx-auto max-w-7xl">
        {/* Header */}
        <div className="text-center">
          <Reveal animation="fade-in">
            <p className="text-xs font-semibold uppercase tracking-[0.25em] text-[var(--accent,#6366f1)]">
              How it works
            </p>
            <h2 className="mx-auto mt-4 max-w-2xl text-3xl font-bold tracking-tight text-[var(--text,#0f172a)] sm:text-4xl lg:text-5xl">
              From filing to first call in{" "}
              <span className="text-gradient">under a day</span>
            </h2>
          </Reveal>
        </div>

        {/* Steps + connector */}
        <div className="relative mt-16">
          {/* Drawn connector — sits behind the cards, md+ only. aria-hidden +
              pointer-events-none so it's purely decorative. The wrapping
              <Reveal> fades the whole overlay in as the row scrolls into view
              while `.animate-draw` strokes the path. Inset matches the grid
              gutter (gap-6 → ~3rem) so endpoints land under the outer cards. */}
          <Reveal
            animation="fade-in"
            delay={220}
            className="pointer-events-none absolute inset-x-[12%] top-[58px] hidden md:block"
          >
            <svg
              className="h-24 w-full overflow-visible"
              viewBox="0 0 600 88"
              fill="none"
              preserveAspectRatio="none"
              aria-hidden
            >
              <defs>
                <linearGradient id="redo-hiw-connector" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="var(--accent,#6366f1)" stopOpacity="0.15" />
                  <stop offset="50%" stopColor="var(--accent-2,#8b5cf6)" stopOpacity="0.55" />
                  <stop offset="100%" stopColor="var(--accent,#6366f1)" stopOpacity="0.15" />
                </linearGradient>
              </defs>
              {/* Faint full-length rail so the wire reads even before/after the
                  stroke draw, then the gradient stroke draws over it. */}
              <path
                d={CONNECTOR_PATH}
                stroke="var(--border,rgba(30,64,175,0.1))"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
              <path
                className="animate-draw"
                style={{ ["--draw-len" as string]: CONNECTOR_LEN }}
                d={CONNECTOR_PATH}
                stroke="url(#redo-hiw-connector)"
                strokeWidth="2"
                strokeLinecap="round"
              />
              {/* Node dots at each card's icon column — the "stops" on the wire. */}
              {[2, 300, 598].map((cx, i) => (
                <circle
                  key={cx}
                  cx={cx}
                  cy={i === 1 ? 58 : 30}
                  r="3.5"
                  fill="var(--accent,#6366f1)"
                  fillOpacity="0.5"
                />
              ))}
            </svg>
          </Reveal>

          <div className="relative grid gap-6 md:grid-cols-3">
            {STEPS.map((step, index) => {
              const Icon = step.icon;
              return (
                <Reveal key={step.step} animation="fade-in" delay={index * 120}>
                  <div className="group relative h-full overflow-hidden rounded-[24px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/70 p-7 shadow-sm backdrop-blur-sm transition duration-300 hover:-translate-y-0.5 hover:border-[var(--accent,#6366f1)]/30 hover:shadow-[0_8px_24px_rgba(99,102,241,0.12)]">
                    {/* Soft accent wash that warms on hover — adds depth without
                        competing with the copy. */}
                    <span
                      className="pointer-events-none absolute -right-10 -top-12 h-32 w-32 rounded-full bg-[var(--accent,#6366f1)]/10 opacity-0 blur-2xl transition-opacity duration-500 group-hover:opacity-100"
                      aria-hidden
                    />
                    <div className="relative flex items-center justify-between">
                      <div className="inline-flex rounded-2xl bg-[var(--accent,#6366f1)]/10 p-3 text-[var(--accent,#6366f1)] transition-transform duration-300 group-hover:scale-105">
                        <Icon className="h-5 w-5" strokeWidth={2} />
                      </div>
                      <span className="text-4xl font-bold tracking-tight text-[var(--accent,#6366f1)]/15 tabular-nums">
                        {step.step}
                      </span>
                    </div>
                    <h3 className="relative mt-5 text-lg font-semibold text-[var(--text,#0f172a)]">
                      {step.title}
                    </h3>
                    <p className="relative mt-2 text-sm leading-relaxed text-[var(--text-dim,#475569)]">
                      {step.desc}
                    </p>
                  </div>
                </Reveal>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
