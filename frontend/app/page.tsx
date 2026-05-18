import Link from "next/link";
import { redirect } from "next/navigation";
import { Activity, BarChart3, Lock, Shield, Target, Zap } from "lucide-react";

import { BrandMark } from "@/components/brand/brand-mark";
import { LiveDashboardMockup } from "@/components/landing/live-dashboard-mockup";
import { getOptionalSession } from "@/lib/auth-server";

export default async function HomePage() {
  const session = await getOptionalSession();
  if (session) redirect("/dashboard");

  return (
    <div className="min-h-screen">
      {/* ── Nav ─────────────────────────────────────────────── */}
      <nav className="animate-fade-in fixed inset-x-0 top-0 z-50 border-b border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <BrandMark size={36} />
            <span className="text-sm font-semibold tracking-tight text-[var(--text,#0f172a)]">DOX</span>
          </div>
          <div className="flex items-center gap-3">
            <Link
              href="/login"
              className="rounded-xl px-4 py-2.5 text-sm font-medium text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
            >
              Sign in
            </Link>
            <Link
              href="/signup"
              className="rounded-xl bg-[var(--accent,#6366f1)] px-5 py-2.5 text-sm font-medium text-white transition hover:bg-[var(--accent-2,#8b5cf6)]"
            >
              Get started
            </Link>
          </div>
        </div>
      </nav>

      {/* ── Hero ────────────────────────────────────────────── */}
      <section className="relative overflow-hidden pb-20 pt-32 lg:pb-32 lg:pt-44">
        {/* Background decorations */}
        <div className="pointer-events-none absolute -right-40 -top-40 h-[600px] w-[600px] rounded-full bg-gradient-to-br from-blue/8 to-transparent blur-3xl" />
        <div className="pointer-events-none absolute -bottom-20 -left-20 h-[400px] w-[400px] rounded-full bg-gradient-to-tr from-gold/10 to-transparent blur-3xl" />

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

      {/* ── Social proof strip ──────────────────────────────── */}
      <section className="animate-fade-in border-y border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/50 py-10 backdrop-blur-sm">
        <div className="mx-auto grid max-w-7xl grid-cols-2 gap-y-8 px-6 md:grid-cols-4">
          {[
            { metric: "3,800+", label: "broker-dealers tracked across SEC + FINRA" },
            { metric: "Daily", label: "Form BD + 17a-11 deficiency scans" },
            { metric: "X-17A-5", label: "clearing relationships extracted via LLM" },
            { metric: "< 24h", label: "from filing to scored prospect" },
          ].map((pillar) => (
            <div
              key={pillar.metric}
              className="flex flex-col gap-1 md:border-r md:border-[var(--border,rgba(30,64,175,0.1))] md:pr-6 md:last:border-r-0"
            >
              <p className="text-2xl font-bold tracking-tight text-[var(--text,#0f172a)]">{pillar.metric}</p>
              <p className="text-xs leading-snug text-[var(--text-muted,#94a3b8)]">{pillar.label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ────────────────────────────────────────── */}
      <section className="relative py-24">
        <div className="mx-auto max-w-7xl px-6">
          <div className="text-center">
            <p className="text-sm font-semibold uppercase tracking-[0.25em] text-[var(--accent,#6366f1)]">Platform Capabilities</p>
            <h2 className="mx-auto mt-4 max-w-2xl text-3xl font-bold tracking-tight text-[var(--text,#0f172a)] sm:text-4xl">
              Every signal, one workspace
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-base text-[var(--text-muted,#94a3b8)]">
              From new BD registrations to clearing partner changes, the platform monitors
              SEC and FINRA in real time and delivers qualified prospects inside one system.
            </p>
          </div>

          <div className="mt-16 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {[
              {
                icon: BarChart3,
                title: "Financial Health Scoring",
                desc: "Net capital analysis, YoY growth tracking, and automated health classification from FOCUS reports.",
                accent: "from-blue/10 to-blue/5",
                iconBg: "bg-[var(--accent,#6366f1)]/10 text-[var(--accent,#6366f1)]",
              },
              {
                icon: Target,
                title: "Prospect Scoring Engine",
                desc: "Weighted scoring model with configurable factors. Hot, Warm, and Cold prospect classification.",
                accent: "from-gold/10 to-gold/5",
                iconBg: "bg-gold/10 text-[#b88520]",
              },
              {
                icon: Activity,
                title: "Daily Filing Monitor",
                desc: "Automated scan for Form BD and 17a-11 deficiency filings with real-time alert routing.",
                accent: "from-success/10 to-success/5",
                iconBg: "bg-success/10 text-success",
              },
              {
                icon: Lock,
                title: "Clearing Relationship Map",
                desc: "LLM-powered extraction of clearing partners from X-17A-5 annual audit PDFs.",
                accent: "from-navy/8 to-navy/4",
                iconBg: "bg-[var(--accent,#6366f1)]/10 text-[var(--accent,#6366f1)]",
              },
              {
                icon: Shield,
                title: "Controlled Data Export",
                desc: "Restricted CSV with permitted fields only. 100-record cap and 3-export daily limit.",
                accent: "from-danger/8 to-danger/4",
                iconBg: "bg-danger/10 text-danger",
              },
              {
                icon: Zap,
                title: "Contact Enrichment",
                desc: "On-demand Apollo.io integration for executive email, phone, and LinkedIn data.",
                accent: "from-blue/8 to-gold/5",
                iconBg: "bg-[var(--accent,#6366f1)]/10 text-[var(--accent,#6366f1)]",
              },
            ].map((feature) => (
              <div
                key={feature.title}
                className="group rounded-[24px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/70 p-7 shadow-sm transition duration-300 hover:-translate-y-1 hover:shadow-lg"
              >
                <div className={`inline-flex rounded-2xl p-3 ${feature.iconBg}`}>
                  <feature.icon className="h-5 w-5" />
                </div>
                <h3 className="mt-5 text-lg font-semibold text-[var(--text,#0f172a)]">{feature.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted,#94a3b8)]">{feature.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Footer ──────────────────────────────────────────── */}
      <footer className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-10">
        <div className="mx-auto flex max-w-7xl flex-col items-center gap-4 px-6 sm:flex-row sm:justify-between">
          <div className="flex items-center gap-2">
            <BrandMark size={28} />
            <span className="text-xs font-semibold text-[var(--text,#0f172a)]">DOX — Institutional Finance Intelligence</span>
          </div>
          <p className="text-xs text-[var(--text-muted,#94a3b8)]">&copy; {new Date().getFullYear()} Alchemy Dev. All rights reserved. Confidential.</p>
        </div>
      </footer>
    </div>
  );
}
