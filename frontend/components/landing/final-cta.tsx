import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { Reveal } from "@/components/landing/reveal";

// Closing CTA band — gradient panel that drives to signup. Server component;
// the reveal-on-scroll is delegated to the client <Reveal> wrapper.
export function FinalCta() {
  return (
    <section className="relative py-24">
      <div className="mx-auto max-w-7xl px-6">
        <Reveal animation="scale-in">
          <div className="animate-gradient relative overflow-hidden rounded-[32px] bg-gradient-to-br from-[var(--accent,#6366f1)] via-[var(--accent-2,#8b5cf6)] to-[var(--accent,#6366f1)] px-8 py-16 text-center shadow-2xl shadow-[var(--accent,#6366f1)]/25 sm:px-16">
            {/* Decorative orbs — non-interactive. */}
            <div aria-hidden className="pointer-events-none absolute -right-16 -top-16 h-64 w-64 rounded-full bg-white/10 blur-3xl" />
            <div aria-hidden className="pointer-events-none absolute -bottom-16 -left-16 h-64 w-64 rounded-full bg-white/10 blur-3xl" />

            <div className="relative">
              <h2 className="mx-auto max-w-2xl text-3xl font-bold tracking-tight text-white sm:text-4xl">
                Stop refreshing EDGAR. Start closing.
              </h2>
              <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-white/80">
                Every new broker-dealer, FOCUS filing, and clearing change — scored and
                ready the moment it lands. See who&apos;s worth your next call.
              </p>
              <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
                <Link
                  href="/signup"
                  className="group inline-flex items-center gap-2 rounded-2xl bg-white px-7 py-4 text-sm font-semibold text-[var(--accent,#6366f1)] shadow-lg transition hover:-translate-y-0.5 hover:shadow-xl"
                >
                  Get started
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
                </Link>
                <Link
                  href="/login"
                  className="rounded-2xl border border-white/30 bg-white/10 px-7 py-4 text-sm font-semibold text-white backdrop-blur transition hover:bg-white/20"
                >
                  Sign in
                </Link>
              </div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
