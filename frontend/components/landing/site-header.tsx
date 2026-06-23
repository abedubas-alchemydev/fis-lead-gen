"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { BrandMark } from "@/components/brand/brand-mark";

// Sticky glass nav that condenses on scroll. A 1px sentinel is parked at the
// very top of the page; an IntersectionObserver watches it, and the moment it
// leaves the viewport (i.e. the user has scrolled past the hero's top edge)
// we strengthen the header's background, border, and shadow. Using the
// observer instead of a scroll listener keeps this off the scroll thread.
export function SiteHeader() {
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
        className={`animate-fade-in fixed inset-x-0 top-0 z-50 backdrop-blur-xl transition-all duration-300 ${
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
          <Link href="/" className="flex items-center gap-3">
            <BrandMark size={36} />
            <span className="text-sm font-semibold tracking-tight text-[var(--text,#0f172a)]">DOX</span>
          </Link>
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
      </header>
    </>
  );
}
