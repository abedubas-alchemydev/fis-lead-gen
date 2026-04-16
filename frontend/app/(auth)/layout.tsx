import type { ReactNode } from "react";
import { BarChart3, Shield, Target, Zap } from "lucide-react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="animate-scale-in grid w-full max-w-[1100px] gap-0 overflow-hidden rounded-[36px] border border-white/60 bg-white/40 shadow-2xl shadow-navy/8 backdrop-blur-sm lg:grid-cols-[1.15fr_0.85fr]">
        {/* ── Left panel: Brand showcase ──────────────────────── */}
        <section className="relative hidden overflow-hidden bg-gradient-to-br from-navy via-[#0f2d52] to-[#163768] p-12 text-white lg:flex lg:flex-col">
          {/* Decorative blurs */}
          <div className="pointer-events-none absolute -right-20 -top-20 h-64 w-64 rounded-full bg-blue/15 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-16 -left-16 h-48 w-48 rounded-full bg-gold/12 blur-3xl" />

          <div className="relative">
            <div className="animate-fade-in flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white/10 backdrop-blur">
                <Target className="h-5 w-5" />
              </div>
              <span className="text-xs font-semibold uppercase tracking-[0.3em] text-white/60">Lead Gen Engine</span>
            </div>

            <h1 className="animate-fade-in delay-150 mt-10 text-[2rem] font-bold leading-tight">
              Client Clearing
              <br />
              <span className="bg-gradient-to-r from-white to-white/70 bg-clip-text text-transparent">
                Lead Gen Engine
              </span>
            </h1>
            <p className="animate-fade-in delay-300 mt-5 max-w-sm text-sm leading-relaxed text-white/65">
              Enterprise broker-dealer intelligence for surfacing clearing opportunities,
              tracking risk signals, and managing qualified leads in one system.
            </p>
          </div>

          {/* Feature cards */}
          <div className="relative mt-auto grid gap-3 pt-12">
            {[
              { icon: BarChart3, label: "SEC + FINRA Signals", desc: "Daily new registrations and filings" },
              { icon: Shield, label: "Competitor Mapping", desc: "Know who clears where" },
              { icon: Zap, label: "Scored Leads", desc: "Hot, warm, and cold classification" },
            ].map((item, i) => (
              <div
                key={item.label}
                className={`animate-fade-in-left delay-${(i + 4) * 100} flex items-center gap-4 rounded-2xl border border-white/8 bg-white/5 px-5 py-4 backdrop-blur`}
              >
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10">
                  <item.icon className="h-4.5 w-4.5 text-white/80" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-white/90">{item.label}</p>
                  <p className="mt-0.5 text-xs text-white/50">{item.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── Right panel: Auth form ─────────────────────────── */}
        <section className="flex items-center justify-center bg-white/90 px-8 py-12 backdrop-blur-xl sm:px-12 lg:px-14">
          <div className="w-full max-w-[380px]">
            {/* Mobile logo (shown only on small screens) */}
            <div className="mb-8 flex items-center gap-3 lg:hidden">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-navy text-white">
                <Target className="h-4.5 w-4.5" />
              </div>
              <span className="text-sm font-semibold text-navy">Lead Gen Engine</span>
            </div>
            {children}
          </div>
        </section>
      </div>
    </main>
  );
}
