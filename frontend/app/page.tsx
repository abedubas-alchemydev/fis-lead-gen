import Link from "next/link";
import { redirect } from "next/navigation";
import { Activity, BarChart3, Lock, Shield, Target, Zap } from "lucide-react";

import { BrandMark } from "@/components/brand/brand-mark";
import { getOptionalSession } from "@/lib/auth-server";

export default async function HomePage() {
  const session = await getOptionalSession();
  if (session) redirect("/dashboard");

  return (
    <div className="min-h-screen">
      {/* ── Nav ─────────────────────────────────────────────── */}
      <nav className="animate-fade-in fixed inset-x-0 top-0 z-50 border-b border-white/60 bg-white/70 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <BrandMark size={36} />
            <span className="text-sm font-semibold tracking-tight text-navy">DOX</span>
          </div>
          <div className="flex items-center gap-3">
            <Link
              href="/login"
              className="rounded-xl px-4 py-2.5 text-sm font-medium text-navy transition hover:bg-slate-100"
            >
              Sign in
            </Link>
            <Link
              href="/signup"
              className="rounded-xl bg-navy px-5 py-2.5 text-sm font-medium text-white transition hover:bg-[#112b54]"
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
                <span className="inline-flex items-center gap-2 rounded-full border border-blue/15 bg-blue/5 px-4 py-1.5 text-xs font-semibold uppercase tracking-[0.2em] text-blue">
                  <Zap className="h-3.5 w-3.5" />
                  Enterprise Intelligence
                </span>
              </div>
              <h1 className="animate-fade-in delay-150 mt-8 text-4xl font-bold leading-[1.15] tracking-tight text-navy sm:text-5xl lg:text-[3.5rem]">
                Broker-Dealer
                <br />
                <span className="bg-gradient-to-r from-blue to-[#2d7fd3] bg-clip-text text-transparent">
                  Clearing Intelligence
                </span>
              </h1>
              <p className="animate-fade-in delay-300 mt-6 max-w-lg text-lg leading-relaxed text-slate-600">
                Aggregate SEC and FINRA data. Map clearing relationships.
                Score and surface high-value leads for firms offering settlement
                and clearing services.
              </p>
              <div className="animate-fade-in delay-400 mt-10 flex flex-wrap items-center gap-4">
                <Link
                  href="/signup"
                  className="group relative overflow-hidden rounded-2xl bg-navy px-7 py-4 text-sm font-semibold text-white shadow-lg shadow-navy/20 transition hover:-translate-y-0.5 hover:shadow-xl hover:shadow-navy/25"
                >
                  <span className="relative z-10">Start free trial</span>
                  <div className="absolute inset-0 bg-gradient-to-r from-blue to-navy opacity-0 transition-opacity group-hover:opacity-100" />
                </Link>
                <Link
                  href="/login"
                  className="rounded-2xl border border-slate-200 bg-white px-7 py-4 text-sm font-semibold text-navy shadow-sm transition hover:-translate-y-0.5 hover:border-blue/30 hover:shadow-md"
                >
                  Sign in to dashboard
                </Link>
              </div>

              {/* Trust bar */}
              <div className="animate-fade-in delay-600 mt-14 flex flex-wrap items-center gap-6 border-t border-slate-200/70 pt-8">
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Shield className="h-4 w-4 text-success" />
                  SOC 2 ready architecture
                </div>
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Lock className="h-4 w-4 text-success" />
                  Encrypted at rest
                </div>
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Activity className="h-4 w-4 text-success" />
                  99.5% uptime SLA
                </div>
              </div>
            </div>

            {/* Right: Dashboard preview card */}
            <div className="animate-fade-in-right delay-300 relative hidden lg:block">
              <div className="animate-float relative rounded-[28px] border border-white/60 bg-white/80 p-2 shadow-2xl shadow-navy/10 backdrop-blur">
                <div className="rounded-[22px] bg-gradient-to-br from-navy via-[#0f2d52] to-[#163768] p-8">
                  {/* Mini dashboard mockup */}
                  <div className="mb-6 flex items-center justify-between">
                    <div>
                      <p className="text-[10px] uppercase tracking-[0.3em] text-white/50">Live Platform</p>
                      <p className="mt-1 text-sm font-semibold text-white">DOX Intelligence Workspace</p>
                    </div>
                    <div className="h-2 w-2 rounded-full bg-success animate-pulse" />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    {[
                      { label: "Active BDs", value: "3,847", color: "bg-white/10" },
                      { label: "New (30d)", value: "127", color: "bg-blue/20" },
                      { label: "Hot Leads", value: "312", color: "bg-gold/20" },
                      { label: "Deficiencies", value: "18", color: "bg-danger/20" },
                    ].map((kpi) => (
                      <div key={kpi.label} className={`rounded-2xl ${kpi.color} p-4 backdrop-blur`}>
                        <p className="text-[10px] uppercase tracking-[0.2em] text-white/50">{kpi.label}</p>
                        <p className="mt-2 text-xl font-bold text-white">{kpi.value}</p>
                      </div>
                    ))}
                  </div>
                  <div className="mt-4 rounded-2xl bg-white/5 p-4">
                    <p className="text-[10px] uppercase tracking-[0.2em] text-white/40">Clearing Distribution</p>
                    <div className="mt-3 flex gap-1">
                      {[40, 25, 15, 12, 8].map((w, i) => (
                        <div
                          key={i}
                          className="h-2 rounded-full"
                          style={{
                            width: `${w}%`,
                            backgroundColor: ["#1B5E9E", "#2d7fd3", "#E8A838", "#6d8097", "#27AE60"][i],
                          }}
                        />
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              {/* Glow behind card */}
              <div className="pointer-events-none absolute -inset-4 -z-10 rounded-[36px] bg-gradient-to-br from-blue/15 via-transparent to-gold/10 blur-2xl" />
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ────────────────────────────────────────── */}
      <section className="relative py-24">
        <div className="mx-auto max-w-7xl px-6">
          <div className="text-center">
            <p className="text-sm font-semibold uppercase tracking-[0.25em] text-blue">Platform Capabilities</p>
            <h2 className="mx-auto mt-4 max-w-2xl text-3xl font-bold tracking-tight text-navy sm:text-4xl">
              Every signal, one workspace
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-base text-slate-500">
              From new BD registrations to clearing partner changes, the platform monitors
              SEC and FINRA in real time and delivers qualified leads inside one system.
            </p>
          </div>

          <div className="mt-16 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {[
              {
                icon: BarChart3,
                title: "Financial Health Scoring",
                desc: "Net capital analysis, YoY growth tracking, and automated health classification from FOCUS reports.",
                accent: "from-blue/10 to-blue/5",
                iconBg: "bg-blue/10 text-blue",
              },
              {
                icon: Target,
                title: "Lead Scoring Engine",
                desc: "Weighted scoring model with configurable factors. Hot, Warm, and Cold lead classification.",
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
                iconBg: "bg-navy/10 text-navy",
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
                iconBg: "bg-blue/10 text-blue",
              },
            ].map((feature) => (
              <div
                key={feature.title}
                className="group rounded-[24px] border border-white/70 bg-gradient-to-br p-7 shadow-sm transition duration-300 hover:-translate-y-1 hover:shadow-lg"
                style={{ backgroundImage: `linear-gradient(to bottom right, var(--tw-gradient-stops))` }}
              >
                <div className={`inline-flex rounded-2xl p-3 ${feature.iconBg}`}>
                  <feature.icon className="h-5 w-5" />
                </div>
                <h3 className="mt-5 text-lg font-semibold text-navy">{feature.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-500">{feature.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA ─────────────────────────────────────────────── */}
      <section className="py-24">
        <div className="mx-auto max-w-7xl px-6">
          <div className="relative overflow-hidden rounded-[32px] bg-gradient-to-br from-navy via-[#0f2d52] to-[#163768] px-8 py-16 text-center text-white shadow-2xl shadow-navy/20 sm:px-16">
            <div className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-blue/10 blur-3xl" />
            <div className="pointer-events-none absolute -bottom-10 -left-10 h-56 w-56 rounded-full bg-gold/10 blur-3xl" />
            <p className="relative text-sm font-semibold uppercase tracking-[0.25em] text-white/60">Ready to start</p>
            <h2 className="relative mt-4 text-3xl font-bold sm:text-4xl">
              Surface your next{" "}
              <span className="bg-gradient-to-r from-gold to-[#f0c060] bg-clip-text text-transparent">
                $22M deal
              </span>
            </h2>
            <p className="relative mx-auto mt-4 max-w-lg text-base text-white/70">
              DOX has surfaced eight-figure clearing opportunities through similar intelligence.
              Every clearing lead starts with a signal you can see first.
            </p>
            <div className="relative mt-10 flex flex-wrap items-center justify-center gap-4">
              <Link
                href="/signup"
                className="rounded-2xl bg-white px-8 py-4 text-sm font-semibold text-navy shadow-lg transition hover:-translate-y-0.5 hover:shadow-xl"
              >
                Create your account
              </Link>
              <Link
                href="/login"
                className="rounded-2xl border border-white/20 px-8 py-4 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:border-white/40 hover:bg-white/5"
              >
                Sign in
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* ── Footer ──────────────────────────────────────────── */}
      <footer className="border-t border-slate-200/60 py-10">
        <div className="mx-auto flex max-w-7xl flex-col items-center gap-4 px-6 sm:flex-row sm:justify-between">
          <div className="flex items-center gap-2">
            <BrandMark size={28} />
            <span className="text-xs font-semibold text-navy">DOX — Institutional Finance Intelligence</span>
          </div>
          <p className="text-xs text-slate-400">&copy; {new Date().getFullYear()} Alchemy Dev. All rights reserved. Confidential.</p>
        </div>
      </footer>
    </div>
  );
}
