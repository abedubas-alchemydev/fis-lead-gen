import { FileSearch, Gauge, PhoneCall } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Reveal } from "@/components/landing/reveal";

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

// How-it-works — stays a server component; the entrance animation is delegated
// to the client <Reveal> wrapper so each step staggers in on scroll.
export function HowItWorks() {
  return (
    <section className="relative border-t border-[var(--border,rgba(30,64,175,0.1))] py-24">
      <div className="mx-auto max-w-7xl px-6">
        <div className="text-center">
          <p className="text-sm font-semibold uppercase tracking-[0.25em] text-[var(--accent,#6366f1)]">
            How it works
          </p>
          <h2 className="mx-auto mt-4 max-w-2xl text-3xl font-bold tracking-tight text-[var(--text,#0f172a)] sm:text-4xl">
            From filing to first call in under a day
          </h2>
        </div>

        <div className="mt-16 grid gap-6 md:grid-cols-3">
          {STEPS.map((step, index) => {
            const Icon = step.icon;
            return (
              <Reveal key={step.step} animation="fade-in" delay={index * 120}>
                <div className="relative h-full rounded-[24px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/70 p-7 shadow-sm">
                  <div className="flex items-center justify-between">
                    <div className="inline-flex rounded-2xl bg-[var(--accent,#6366f1)]/10 p-3 text-[var(--accent,#6366f1)]">
                      <Icon className="h-5 w-5" />
                    </div>
                    <span className="text-4xl font-bold tracking-tight text-[var(--accent,#6366f1)]/15">
                      {step.step}
                    </span>
                  </div>
                  <h3 className="mt-5 text-lg font-semibold text-[var(--text,#0f172a)]">{step.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted,#94a3b8)]">{step.desc}</p>
                </div>
              </Reveal>
            );
          })}
        </div>
      </div>
    </section>
  );
}
