import Link from "next/link";
import { Activity, Lock, Shield, Zap } from "lucide-react";

import { HeroAurora } from "@/components/landing/hero-aurora";
import { LiveDashboardMockup } from "@/components/landing/live-dashboard-mockup";

// Hero section — stays a server component (copy + CTAs are static links). The
// animated aurora is a sibling CSS layer; the dashboard preview is the only
// interactive leaf.
export function Hero() {
  return (
    <section className="relative overflow-hidden pb-20 pt-32 lg:pb-32 lg:pt-44">
      <HeroAurora />

      <div className="relative mx-auto max-w-7xl px-6">
        <div className="grid items-center gap-16 lg:grid-cols-2">
          {/* Left: Copy */}
          <div>
            <div className="animate-fade-in">
              <span className="inline-flex items-center gap-2 rounded-full border border-[var(--accent,#6366f1)]/15 bg-[var(--accent,#6366f1)]/5 px-4 py-1.5 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--accent,#6366f1)]">
                <Zap className="h-3.5 w-3.5" />
                Enterprise Intelligence
              </span>
            </div>
            <h1 className="animate-fade-in delay-150 mt-8 text-4xl font-bold leading-[1.15] tracking-tight text-[var(--text,#0f172a)] sm:text-5xl lg:text-[3.5rem]">
              Broker-Dealer
              <br />
              <span className="bg-gradient-to-r from-[var(--accent,#6366f1)] to-[var(--accent-2,#8b5cf6)] bg-clip-text text-transparent">
                Clearing Intelligence
              </span>
            </h1>
            <p className="animate-fade-in delay-300 mt-6 max-w-lg text-lg leading-relaxed text-[var(--text-dim,#475569)]">
              See every new broker-dealer, FOCUS filing, and clearing-partner
              change the moment it hits SEC or FINRA — ranked by who&apos;s worth
              your next call.
            </p>
            <div className="animate-fade-in delay-400 mt-10 flex flex-wrap items-center gap-4">
              <Link
                href="/signup"
                className="group relative overflow-hidden rounded-2xl bg-[var(--accent,#6366f1)] px-7 py-4 text-sm font-semibold text-white shadow-lg shadow-[var(--accent,#6366f1)]/25 transition hover:-translate-y-0.5 hover:shadow-xl hover:shadow-[var(--accent,#6366f1)]/30"
              >
                <span className="relative z-10">See live prospects</span>
                <div className="absolute inset-0 bg-gradient-to-r from-[var(--accent,#6366f1)] to-[var(--accent-2,#8b5cf6)] opacity-0 transition-opacity group-hover:opacity-100" />
              </Link>
              <Link
                href="/login"
                className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-7 py-4 text-sm font-semibold text-[var(--text,#0f172a)] shadow-sm transition hover:-translate-y-0.5 hover:border-[var(--accent,#6366f1)]/30 hover:shadow-md"
              >
                Sign in
              </Link>
            </div>

            {/* Trust bar */}
            <div className="animate-fade-in delay-600 mt-14 flex flex-wrap items-center gap-6 border-t border-[var(--border,rgba(30,64,175,0.1))] pt-8">
              <div className="flex items-center gap-2 text-sm text-[var(--text-muted,#94a3b8)]">
                <Shield className="h-4 w-4 text-success" />
                SOC 2-ready architecture
              </div>
              <div className="flex items-center gap-2 text-sm text-[var(--text-muted,#94a3b8)]">
                <Lock className="h-4 w-4 text-success" />
                AES-256 encryption at rest
              </div>
              <div className="flex items-center gap-2 text-sm text-[var(--text-muted,#94a3b8)]">
                <Activity className="h-4 w-4 text-success" />
                99.5% uptime SLA
              </div>
            </div>
          </div>

          {/* Right: Dashboard preview card */}
          <LiveDashboardMockup />
        </div>
      </div>
    </section>
  );
}
