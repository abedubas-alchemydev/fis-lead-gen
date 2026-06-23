import { Activity, Database, Lock, Shield } from "lucide-react";

import { Reveal } from "@/components/landing/reveal";

// ─────────────────────────────────────────────────────────────────────────
// redo-credibility — institutional trust band (spec §9.7).
//
// Pure SERVER component: static copy + hardcoded fixtures, no fetch, no
// state. The only motion is (a) scroll-reveal via the <Reveal> client leaf
// and (b) the CSS-only `.animate-marquee-left` rail — so this whole file can
// stay server-rendered. Everything resolves to its visible resting state
// under prefers-reduced-motion (reveals snap in, the rail freezes) via the
// resets already in globals.css.
//
// Two parts:
//   (A) PROOF POINTS — eyebrow + a grid of glass chips reusing the
//       Shield/Lock/Activity icon vocabulary from the hero trust bar, plus a
//       coverage stat. Success-tinted icons; gold kept to a single keyline.
//   (B) LOGO/KEYWORD RAIL — a marquee of regulator & clearing-firm word-marks
//       (no real logos available), rendered twice with the duplicate
//       aria-hidden and edge-masked so it fades at both ends.
// ─────────────────────────────────────────────────────────────────────────

type ProofPoint = {
  icon: typeof Shield;
  title: string;
  detail: string;
  // The lone gold accent lives on the coverage stat; everything else reads
  // success-green so the band stays calm and the "money" highlight is scarce.
  accent: "success" | "gold";
};

const PROOF_POINTS: ProofPoint[] = [
  {
    icon: Shield,
    title: "SOC 2-ready architecture",
    detail: "Least-privilege access, audit-logged actions, and isolated tenant data.",
    accent: "success",
  },
  {
    icon: Lock,
    title: "AES-256 encryption at rest",
    detail: "Every record and document encrypted in storage and in transit.",
    accent: "success",
  },
  {
    icon: Activity,
    title: "99.5% uptime SLA",
    detail: "The wire keeps watching — filings land the moment they post.",
    accent: "success",
  },
  {
    icon: Database,
    title: "3,847+ firms tracked",
    detail: "Continuous coverage across SEC EDGAR + FINRA BrokerCheck.",
    accent: "gold",
  },
];

// Regulators & clearing firms surfaced as refined word-marks. Mirrors the
// competitor/clearing vocabulary the platform actually classifies against.
const RAIL_WORDS = [
  "SEC",
  "FINRA",
  "SEC EDGAR",
  "Pershing",
  "Apex Clearing",
  "RBC Clearing",
  "Hilltop Securities",
  "Axos",
  "Vision",
  "DTCC",
  "OCC",
];

// Edge fade so the rail dissolves into the section instead of hard-cutting at
// the viewport — applied as both `maskImage` and the WebKit-prefixed twin for
// Safari coverage.
const RAIL_MASK =
  "linear-gradient(to right, transparent, #000 12%, #000 88%, transparent)";

function ProofChip({ point, index }: { point: ProofPoint; index: number }) {
  const Icon = point.icon;
  const isGold = point.accent === "gold";

  // Success-tinted by default; the coverage stat gets the single gold tint.
  const iconWrap = isGold
    ? "bg-gold/10 text-gold ring-1 ring-gold/20"
    : "bg-success/10 text-success ring-1 ring-success/15";

  return (
    <Reveal animation="fade-in" delay={index * 90}>
      <div className="group relative h-full overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/70 p-5 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04))] backdrop-blur transition duration-300 hover:-translate-y-0.5 hover:border-[var(--accent,#6366f1)]/25 hover:shadow-[0_8px_24px_rgba(99,102,241,0.12)]">
        {/* Faint corner wash that warms on hover — depth without weight. */}
        <div
          aria-hidden
          className={`pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full blur-2xl transition-opacity duration-300 ${
            isGold ? "bg-gold/10" : "bg-success/10"
          } opacity-0 group-hover:opacity-100`}
        />
        <span
          className={`relative inline-flex h-10 w-10 items-center justify-center rounded-xl ${iconWrap}`}
        >
          <Icon className="h-5 w-5" strokeWidth={2} aria-hidden />
        </span>
        <p className="relative mt-4 text-base font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
          {point.title}
        </p>
        <p className="relative mt-1.5 text-sm leading-relaxed text-[var(--text-dim,#475569)]">
          {point.detail}
        </p>
      </div>
    </Reveal>
  );
}

export function RedoCredibility() {
  return (
    <section
      id="credibility"
      aria-labelledby="credibility-heading"
      className="relative border-t border-[var(--border,rgba(30,64,175,0.1))] px-6 py-20"
    >
      <div className="mx-auto max-w-7xl">
        {/* ── (A) PROOF POINTS ─────────────────────────────────────────── */}
        <div className="mx-auto max-w-2xl text-center">
          <Reveal animation="fade-in">
            <span className="inline-flex items-center gap-2 rounded-full border border-[var(--accent,#6366f1)]/15 bg-[var(--accent,#6366f1)]/5 px-4 py-1.5 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--accent,#6366f1)]">
              <Shield className="h-3.5 w-3.5" aria-hidden />
              Built for institutional trust
            </span>
          </Reveal>
          <Reveal animation="fade-in" delay={120}>
            <h2
              id="credibility-heading"
              className="mt-6 text-3xl font-bold tracking-tight text-[var(--text,#0f172a)] sm:text-4xl"
            >
              Security and coverage your compliance desk can{" "}
              <span className="text-gradient">stand behind</span>
            </h2>
          </Reveal>
          <Reveal animation="fade-in" delay={200}>
            <p className="mt-4 text-lg leading-relaxed text-[var(--text-dim,#475569)]">
              Enterprise-grade controls and continuous regulatory coverage — so
              the prospect on your desk is one you can act on with confidence.
            </p>
          </Reveal>
        </div>

        <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {PROOF_POINTS.map((point, i) => (
            <ProofChip key={point.title} point={point} index={i} />
          ))}
        </div>

        {/* ── (B) LOGO / KEYWORD RAIL ─────────────────────────────────────
            Purely illustrative — text word-marks stand in for unavailable
            logos. The region is labelled; the duplicated track is aria-hidden
            so a screen reader gets exactly one clean pass over the names. */}
        <Reveal animation="fade-in" delay={120}>
          <div
            role="region"
            aria-label="Trusted data sources"
            className="group relative mt-16 overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/50 py-6 backdrop-blur"
          >
            <p className="mb-5 text-center text-[11px] font-semibold uppercase tracking-[0.25em] text-[var(--text-muted,#94a3b8)]">
              Sourced direct from the regulators &amp; clearing network
            </p>
            <div
              className="relative flex overflow-hidden"
              style={{ maskImage: RAIL_MASK, WebkitMaskImage: RAIL_MASK }}
            >
              {/* Two identical tracks back-to-back. The keyframe translates
                  by -50%, so the second track seamlessly takes the first's
                  place. Pauses on hover; freezes fully under reduced motion. */}
              {[false, true].map((isDuplicate) => (
                <ul
                  key={isDuplicate ? "rail-dup" : "rail"}
                  aria-hidden={isDuplicate || undefined}
                  className="animate-marquee-left flex shrink-0 items-center"
                >
                  {RAIL_WORDS.map((word) => (
                    <li
                      key={(isDuplicate ? "d-" : "") + word}
                      className="flex items-center"
                    >
                      <span className="select-none whitespace-nowrap px-7 font-mono text-sm font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)] transition-colors duration-300 group-hover:text-[var(--text-dim,#475569)] hover:!text-[var(--text,#0f172a)]">
                        {word}
                      </span>
                      {/* Hairline separator between word-marks. */}
                      <span
                        aria-hidden
                        className="h-3.5 w-px bg-[var(--border-2,rgba(30,64,175,0.16))]"
                      />
                    </li>
                  ))}
                </ul>
              ))}
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
