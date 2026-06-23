"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { BrandMark } from "@/components/brand/brand-mark";

// Sticky glass nav — the page chrome — that condenses on scroll. Copies the
// scroll-condense mechanism from site-header.tsx verbatim: a 1px sentinel is
// parked at the very top of the page; an IntersectionObserver watches it, and
// the moment it leaves the viewport (the user has scrolled past the hero's top
// edge) we strengthen the header's background, border, and shadow. Using the
// observer instead of a scroll listener keeps this off the scroll thread.
//
// The hero below is a dark "command" zone, so at rest the nav floats over navy.
// To keep it legible there we (a) thicken the backdrop blur and (b) bloom a
// faint accent glow behind the brand mark — colored light reads as intentional
// over the dark hero. The text colors are SCROLL-STATE-AWARE: at rest over the
// navy hero the wordmark + nav links go light (white / white-tinted, ≥4.5:1 on
// navy); once condensed onto the white working surfaces below the fold they
// flip to the dark ink token (≥4.5:1 on white). A single fixed color can't
// satisfy both backdrops, so we hook the color into the existing condense flag.
export function RedoHeader() {
  const [condensed, setCondensed] = useState(false);

  useEffect(() => {
    const sentinel = document.getElementById("scroll-sentinel");
    if (!sentinel || typeof IntersectionObserver === "undefined") return;

    const observer = new IntersectionObserver(
      ([entry]) => setCondensed(!entry.isIntersecting),
      { rootMargin: "0px", threshold: 0 },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, []);

  return (
    <>
      {/* Park the sentinel above everything; the header watches it. */}
      <span id="scroll-sentinel" aria-hidden className="pointer-events-none absolute left-0 top-0 h-px w-px" />
      <header
        className={`redo-header animate-fade-in fixed inset-x-0 top-0 z-50 backdrop-blur-xl transition-all duration-300 ${
          condensed
            ? "border-b border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/90 shadow-[0_4px_24px_rgba(15,23,42,0.06)]"
            : "border-b border-transparent bg-[var(--surface,#ffffff)]/60"
        }`}
      >
        <div
          className={`mx-auto flex max-w-7xl items-center justify-between px-6 transition-all duration-300 ${
            condensed ? "py-3" : "py-4"
          }`}
        >
          <Link
            href="/"
            className="group flex items-center gap-3 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]/40"
          >
            {/* Faint accent halo so the mark reads over the dark hero; it
             * settles down once the nav condenses onto a white surface. */}
            <span className="relative inline-flex">
              <span
                aria-hidden
                className={`redo-header__glow pointer-events-none absolute -inset-1 rounded-[14px] transition-opacity duration-300 ${
                  condensed ? "opacity-0" : "opacity-100"
                }`}
              />
              <BrandMark size={36} className="relative transition-transform duration-300 group-hover:scale-105" />
            </span>
            {/* Wordmark — the one element that must always be crisp. Light over
             * the navy hero, dark ink once condensed onto the white surfaces. */}
            <span
              className={`text-sm font-semibold tracking-tight transition-colors duration-300 ${
                condensed ? "text-[var(--text,#0f172a)]" : "text-white"
              }`}
            >
              DOX
            </span>
          </Link>
          <div className="flex items-center gap-3">
            <Link
              href="/login"
              className={`rounded-xl px-4 py-2.5 text-sm font-medium outline-none transition focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]/40 ${
                condensed
                  ? "text-[var(--text,#0f172a)] hover:bg-[var(--surface-2,#f1f6fd)]"
                  : "text-white/90 hover:bg-white/10"
              }`}
            >
              Sign in
            </Link>
            <Link
              href="/signup"
              className="rounded-xl bg-[var(--accent,#6366f1)] px-5 py-2.5 text-sm font-medium text-white shadow-[0_6px_20px_-6px_rgba(99,102,241,0.6)] outline-none transition-all duration-200 hover:-translate-y-0.5 hover:bg-[var(--accent-2,#8b5cf6)] hover:shadow-[0_10px_28px_-8px_rgba(139,92,246,0.7)] focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]/40"
            >
              Get started
            </Link>
          </div>
        </div>
      </header>

      <style jsx>{`
        .redo-header__glow {
          background: radial-gradient(
            circle at 50% 50%,
            rgba(99, 102, 241, 0.55) 0%,
            rgba(139, 92, 246, 0.3) 45%,
            transparent 72%
          );
          filter: blur(9px);
          animation: redo-header-glow 4.5s ease-in-out infinite;
        }
        @keyframes redo-header-glow {
          0%,
          100% {
            opacity: 1;
            transform: scale(1);
          }
          50% {
            opacity: 0.7;
            transform: scale(1.08);
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .redo-header__glow {
            animation: none;
          }
        }
      `}</style>
    </>
  );
}
