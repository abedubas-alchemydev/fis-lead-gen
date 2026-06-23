import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { Reveal } from "@/components/landing/reveal";

// Closing moment of the landing redo (spec §9 (8)). Returns to the dark
// command-zone aesthetic to bookend the page: a drifting indigo→violet gradient
// panel with depth (corner orbs + a one-shot sheen sweep) and the final call to
// action. SERVER component — the scroll-reveal is delegated to the client
// <Reveal> wrapper, so this file ships no JS of its own.
//
// Motion budget (one hero beat): the panel `gradient-drift` pan + a single
// diagonal sheen pass as it scales in. Both are CSS-only and reduced-motion
// safe — `.animate-gradient-drift` is neutralised by globals.css, and the
// bespoke one-shot sheen below carries its own `reduce` reset. Under reduced
// motion the panel is fully visible (the <Reveal> flips immediately) with no
// drift and no sweep.
export function RedoFinalCta() {
  return (
    <section aria-label="Get started with DOX" className="relative px-6 py-24">
      <div className="mx-auto max-w-7xl">
        <Reveal animation="scale-in">
          <div className="redo-final-cta animate-gradient-drift relative overflow-hidden rounded-[32px] bg-gradient-to-br from-[var(--accent,#6366f1)] via-[var(--accent-2,#8b5cf6)] to-[var(--accent,#6366f1)] px-8 py-16 text-center shadow-2xl shadow-[var(--accent,#6366f1)]/25 sm:px-16">
            {/* Depth — two soft white orbs glowing out of opposite corners. */}
            <div
              aria-hidden
              className="pointer-events-none absolute -right-20 -top-20 h-64 w-64 rounded-full bg-white/10 blur-3xl"
            />
            <div
              aria-hidden
              className="pointer-events-none absolute -bottom-24 -left-20 h-72 w-72 rounded-full bg-white/10 blur-3xl"
            />

            {/* One-shot sheen — a skewed translucent band sweeps diagonally
                across the panel once as it reveals. Clipped to the panel via
                its own overflow-hidden layer so the band never escapes the
                rounded edge. Decorative + non-interactive. */}
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 overflow-hidden rounded-[32px]"
            >
              <div className="redo-final-cta__sheen absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-transparent via-white/25 to-transparent" />
            </div>

            {/* Content. */}
            <div className="relative z-10">
              <h2 className="mx-auto max-w-2xl text-3xl font-bold tracking-tight text-white sm:text-4xl lg:text-5xl">
                Stop refreshing EDGAR. Start closing.
              </h2>
              <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-white/80">
                Every new broker-dealer, FOCUS filing, and clearing change —
                scored and ready the moment it lands. See who&apos;s worth your
                next call.
              </p>
              <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
                <Link
                  href="/signup"
                  className="group inline-flex items-center gap-2 rounded-2xl bg-white px-7 py-4 text-sm font-semibold text-[var(--accent,#6366f1)] shadow-lg transition hover:-translate-y-0.5 hover:shadow-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60"
                >
                  Get started
                  <ArrowRight className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-0.5" />
                </Link>
                <Link
                  href="/login"
                  className="rounded-2xl border border-white/30 bg-white/10 px-7 py-4 text-sm font-semibold text-white backdrop-blur transition hover:-translate-y-0.5 hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60"
                >
                  Sign in
                </Link>
              </div>
            </div>

            {/* The one-shot sheen's CSS lives in globals.css as
                `.redo-final-cta__sheen` (resting-invisible + a single
                motion-gated pass, with a reduced-motion reset). It can't be a
                styled-jsx block here because this section is a SERVER component
                — styled-jsx pulls in `client-only` and would break the build —
                and the landing-redo motion kit is the designated home for
                shared landing CSS anyway. */}
          </div>
        </Reveal>
      </div>
    </section>
  );
}
