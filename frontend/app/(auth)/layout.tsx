import type { ReactNode } from "react";
import { Lock } from "lucide-react";

import { AuthHero } from "@/components/auth/auth-hero";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="min-h-screen bg-[var(--bg,#eaf3ff)]">
      <div className="grid min-h-screen lg:grid-cols-2">
        {/* ── Hero column (lg only, left) ──────────────────────── */}
        <AuthHero />

        {/* ── Form column (always visible, right) ──────────────── */}
        <section className="flex min-h-screen flex-col bg-[var(--bg,#eaf3ff)]">
          <div className="flex flex-1 items-center justify-center px-8 sm:px-14">
            <div className="w-full max-w-[420px] py-12">{children}</div>
          </div>

          <footer className="flex items-center gap-2 px-8 pb-8 text-xs text-[var(--text-muted,#94a3b8)] sm:px-14">
            <Lock className="h-3.5 w-3.5" aria-hidden />
            <span>SOC 2 Type II · TLS 1.3 · Session-bound auth</span>
          </footer>
        </section>
      </div>
    </main>
  );
}
