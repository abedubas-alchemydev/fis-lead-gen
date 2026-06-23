"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, ArrowRight, Lock, Radio, Shield, Zap } from "lucide-react";

import { Pill } from "@/components/ui/pill";
import { useCountUp } from "@/lib/hooks/use-count-up";
import { useInView } from "@/lib/hooks/use-in-view";
import { useTilt } from "@/lib/hooks/use-tilt";

// ─────────────────────────────────────────────────────────────────────────
// redo-hero — the dark "command" hero (landing redo §9.2). The transformation
// moment, above the fold: a deep-navy trading-desk-at-dusk surface that begins
// the page's dark → light descent (the section bottom-edge gradient resolves
// toward #0b0f1a, handing off to the wire band below).
//
// Architecture mirrors today's Hero + LiveDashboardMockup split, but the whole
// thing lives in this one owned file: a static copy/CTA column on the left and
// an interactive <FilingTerminal/> client leaf on the right. The file carries
// the "use client" directive because the centerpiece needs hooks (tilt, count-
// up, the live clock); the copy column is plain markup with no client state, so
// this stays cheap. The page's single <h1> lives here.
//
// All data is local mock; we reuse the firm-name fixtures that appear in the
// other landing sections so the page reads as one product.
// ─────────────────────────────────────────────────────────────────────────

type FilingVariant = "form" | "critical" | "self";

type FilingRow = {
  firm: string;
  crd: string;
  form: string;
  label: string;
  variant: FilingVariant;
  ago: string;
};

// Streaming wire fixtures — the four house firm names, each on a distinct
// filing type so all three Pill variants the spec calls for are represented.
const FILINGS: FilingRow[] = [
  { firm: "Cedar Ridge Securities", crd: "CRD 318204", form: "Form BD", label: "New registration", variant: "form", ago: "12s" },
  { firm: "Atlas Capital Markets", crd: "CRD 291866", form: "17a-11", label: "Net-capital alert", variant: "critical", ago: "47s" },
  { firm: "Brightwater Partners", crd: "CRD 274513", form: "X-17A-5", label: "Clearing change", variant: "self", ago: "1m" },
  { firm: "Meridian Trade Group", crd: "CRD 305927", form: "FOCUS", label: "Quarterly filing", variant: "form", ago: "2m" },
];

type Kpi = { label: string; value: number; suffix?: string };

// Count-up KPI tiles — ramp on mount (gated by `start` so the numbers ride in
// rather than appearing pre-counted on first paint).
const KPIS: Kpi[] = [
  { label: "Filings today", value: 1284 },
  { label: "New BDs (30d)", value: 127 },
  { label: "Hot prospects", value: 312 },
  { label: "Avg to signal", value: 9, suffix: "m" },
];

const COUNT_DURATION_MS = 1200;
const COUNT_STAGGER_MS = 130;

function KpiTile({ kpi, index, start }: { kpi: Kpi; index: number; start: boolean }) {
  const value = useCountUp(kpi.value, COUNT_DURATION_MS, index * COUNT_STAGGER_MS, start);
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-3.5 backdrop-blur">
      {/* Info-bearing label lifted from white/45 (~3.8:1 on the navy panel —
          fails AA for this 10px text) to white/65 (~7.5:1, comfortably AA). */}
      <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-white/65">
        {kpi.label}
      </p>
      <p className="mt-2 text-2xl font-bold tabular-nums tracking-tight text-white">
        {value}
        {kpi.suffix ? <span className="text-base text-white/65">{kpi.suffix}</span> : null}
      </p>
    </div>
  );
}

function FilingRowItem({ row }: { row: FilingRow }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2.5 transition-colors duration-300 hover:border-white/15 hover:bg-white/[0.06]">
      <Pill variant={row.variant}>{row.form}</Pill>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-semibold text-white/90">{row.firm}</p>
        {/* Info label lifted white/45 → white/65 (~3.8:1 → ~7.5:1 on the panel). */}
        <p className="truncate text-[11px] text-white/65">{row.label}</p>
      </div>
      <div className="shrink-0 text-right">
        {/* CRD code white/50 → white/65; timestamp white/35 (~3:1) → white/65. */}
        <p className="font-mono text-[10px] uppercase tracking-[0.06em] text-white/65">{row.crd}</p>
        <p className="font-mono text-[10px] text-white/65">{row.ago} ago</p>
      </div>
    </div>
  );
}

// Live "wall-clock" timestamp for the header strip. Renders a stable mock value
// on the server / first client paint (no hydration mismatch), then ticks once a
// second on the client. Under reduced motion it still shows a time — it's
// cosmetic chrome — but skips the per-second interval.
function useLiveClock(): string {
  const [now, setNow] = useState<string>("09:41:07");
  useEffect(() => {
    if (typeof window === "undefined") return;
    const fmt = () =>
      new Date().toLocaleTimeString("en-US", {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    setNow(fmt());
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = window.setInterval(() => setNow(fmt()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

// The interactive centerpiece — a glass-dark "live filing terminal" panel with
// pointer tilt, a breathing accent glow, a streaming header, count-up KPIs, the
// filing wire, and a scan beam sweeping down (the "reading the filing" tell).
function FilingTerminal() {
  const { ref: tiltRef, style: tiltStyle } = useTilt<HTMLDivElement>(6);
  // Gate the count-ups on the card entering view so they visibly ramp.
  const { ref: viewRef, inView } = useInView<HTMLDivElement>({ threshold: 0.3 });
  const clock = useLiveClock();

  return (
    <div ref={viewRef} className="animate-fade-in-right delay-300 relative">
      <div
        ref={tiltRef}
        style={tiltStyle}
        className="animate-glow relative overflow-hidden rounded-[28px] border border-white/10 bg-white/5 p-2 shadow-2xl backdrop-blur-xl"
      >
        <div className="relative overflow-hidden rounded-[22px] bg-gradient-to-br from-[#0a1228] via-[#0b1730] to-[#0a1f3f] p-5 sm:p-6">
          {/* Header strip — LIVE badge + pulsing dot, mono clock with blinking caret */}
          <div className="flex items-center justify-between">
            <div className="inline-flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
              </span>
              <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.22em] text-success">
                Live
              </span>
              {/* Wire-source caption + clock lifted to white/65 so they read as
                  intentional metadata rather than near-invisible chrome. */}
              <span className="text-[11px] text-white/65">· SEC / FINRA wire</span>
            </div>
            <p className="font-mono text-[11px] text-white/65">
              {clock}
              <span className="animate-blink ml-0.5 text-[var(--accent,#6366f1)]">_</span>
            </p>
          </div>

          {/* Count-up KPI tiles */}
          <div className="mt-5 grid grid-cols-2 gap-2.5">
            {KPIS.map((kpi, i) => (
              <KpiTile key={kpi.label} kpi={kpi} index={i} start={inView} />
            ))}
          </div>

          {/* Streaming filing wire */}
          <div className="mt-4">
            <div className="mb-2 flex items-center justify-between px-1">
              <p className="text-[10px] font-medium uppercase tracking-[0.2em] text-white/65">
                Incoming filings
              </p>
              <span className="inline-flex items-center gap-1 text-[10px] text-white/55">
                <Radio className="h-3 w-3" />
                streaming
              </span>
            </div>
            <div className="space-y-1.5">
              {/* The freshest row tickers in; the rest are the steady backlog. */}
              <div className="animate-ticker-in">
                <FilingRowItem row={FILINGS[0]} />
              </div>
              {FILINGS.slice(1).map((row) => (
                <FilingRowItem key={row.firm} row={row} />
              ))}
            </div>
          </div>

          {/* Scan beam — a horizontal indigo→violet line sweeping down the panel,
              the "we're reading the filing" tell. Decorative; stilled under
              reduced motion via the .animate-scan reduce reset in globals.css. */}
          <div
            aria-hidden
            className="animate-scan pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[var(--accent,#6366f1)] to-transparent"
            style={{ boxShadow: "0 0 18px 1px rgba(99,102,241,0.55)" }}
          />
        </div>
      </div>

      {/* Blurred gradient halo sibling behind the card. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -inset-4 -z-10 rounded-[36px] bg-gradient-to-br from-[var(--accent,#6366f1)]/25 via-[var(--accent-2,#8b5cf6)]/15 to-gold/10 blur-2xl"
      />
    </div>
  );
}

export function RedoHero() {
  return (
    <section className="relative overflow-hidden bg-navy px-6 pb-28 pt-44 text-white lg:pt-48">
      {/* ── Decorative backdrop: terminal grid + drifting aurora orbs + a slow
          mesh wash, plus the bottom-edge gradient that begins the dark → light
          descent into the next section. All aria-hidden / non-interactive. ── */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
        <div className="bg-grid absolute inset-0 opacity-[0.6]" />
        <div
          className="animate-gradient-drift absolute inset-0 opacity-80"
          style={{
            background:
              "radial-gradient(55% 50% at 12% 18%, rgba(99,102,241,0.18), transparent 60%), radial-gradient(50% 45% at 88% 12%, rgba(139,92,246,0.16), transparent 60%), radial-gradient(45% 50% at 72% 85%, rgba(232,168,56,0.10), transparent 60%)",
          }}
        />
        <div className="animate-aurora absolute -left-32 -top-32 h-[560px] w-[560px] rounded-full bg-[radial-gradient(circle,rgba(99,102,241,0.18),transparent_65%)] blur-3xl" />
        <div className="animate-aurora absolute -right-40 top-10 h-[600px] w-[600px] rounded-full bg-[radial-gradient(circle,rgba(139,92,246,0.16),transparent_65%)] blur-3xl [animation-delay:-8s]" />
        <div className="animate-aurora absolute -bottom-32 left-1/3 h-[460px] w-[460px] rounded-full bg-[radial-gradient(circle,rgba(232,168,56,0.10),transparent_65%)] blur-3xl [animation-delay:-15s]" />
        {/* Dark → light seam at the bottom edge. */}
        <div className="absolute inset-x-0 bottom-0 h-40 bg-gradient-to-b from-transparent to-[#0b0f1a]" />
      </div>

      <div className="mx-auto grid max-w-7xl items-center gap-12 lg:grid-cols-[1.05fr_1fr] lg:gap-16">
        {/* ── Left: copy + CTAs ── */}
        <div>
          <span className="animate-fade-in inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-4 py-1.5 text-xs font-semibold uppercase tracking-[0.2em] text-white/80 backdrop-blur">
            <Zap className="h-3.5 w-3.5 text-[var(--accent,#6366f1)]" />
            Real-time regulatory intelligence
          </span>

          <h1 className="animate-fade-in delay-150 mt-6 text-5xl font-bold leading-[1.05] tracking-tight sm:text-6xl lg:text-[5.5rem]">
            See every filing.
            <br />
            <span className="text-gradient animate-text-shimmer">
              Call the right firm first.
            </span>
          </h1>

          <p className="animate-fade-in delay-300 mt-6 max-w-lg text-lg leading-relaxed text-white/70">
            See every new broker-dealer, FOCUS filing, and clearing-partner
            change the moment it hits SEC or FINRA — ranked by who&apos;s worth
            your next call.
          </p>

          <div className="animate-fade-in delay-400 mt-10 flex flex-wrap gap-4">
            {/* Label is full #ffffff; base fill deepened from accent indigo-500
                (#6366f1 → 4.47:1, just under AA) to indigo-600 (#4f46e5 →
                6.29:1) so the white label clears AA at rest. The hover gradient
                overlay + accent shadow/ring are unchanged. */}
            <Link
              href="/signup"
              className="group relative inline-flex items-center gap-2 overflow-hidden rounded-2xl bg-indigo-600 px-7 py-4 text-sm font-semibold text-white shadow-lg shadow-[var(--accent,#6366f1)]/30 transition duration-300 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-[var(--accent,#6366f1)]/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]/50 focus-visible:ring-offset-2 focus-visible:ring-offset-navy"
            >
              <span className="relative z-10">See live prospects</span>
              <ArrowRight className="relative z-10 h-4 w-4 transition-transform duration-300 group-hover:translate-x-0.5" />
              <span className="absolute inset-0 bg-gradient-to-r from-[var(--accent,#6366f1)] to-[var(--accent-2,#8b5cf6)] opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
            </Link>
            <Link
              href="/login"
              className="inline-flex items-center rounded-2xl border border-white/20 bg-white/10 px-7 py-4 text-sm font-semibold text-white backdrop-blur transition duration-300 hover:-translate-y-0.5 hover:border-white/30 hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40 focus-visible:ring-offset-2 focus-visible:ring-offset-navy"
            >
              Sign in
            </Link>
          </div>

          {/* Trust row */}
          <div className="animate-fade-in delay-600 mt-12 flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-white/10 pt-7">
            <span className="inline-flex items-center gap-2 text-sm text-white/60">
              <Shield className="h-4 w-4 text-success" />
              SOC 2-ready architecture
            </span>
            <span className="inline-flex items-center gap-2 text-sm text-white/60">
              <Lock className="h-4 w-4 text-success" />
              AES-256 encryption at rest
            </span>
            <span className="inline-flex items-center gap-2 text-sm text-white/60">
              <Activity className="h-4 w-4 text-success" />
              99.5% uptime SLA
            </span>
          </div>
        </div>

        {/* ── Right: live filing terminal (client leaf) ── */}
        <FilingTerminal />
      </div>
    </section>
  );
}
