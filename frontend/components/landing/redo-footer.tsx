import type { Route } from "next";
import Link from "next/link";

import { BrandMark } from "@/components/brand/brand-mark";

// Page footer — the 9th and final owned section of the landing redo. Composed
// last, outside <main>, it closes the dark -> light -> dark descent on a calm,
// static note: no fetch, no ambient loops, no entrance reveal. The only motion
// is a CSS underline-on-hover micro-interaction on the link row (transition,
// not a keyframe), which keeps the section reduced-motion-safe by construction.
//
// SERVER component (no "use client"): pure static markup, so it renders on the
// server and never ships JS. `new Date().getFullYear()` runs at request time.

// Quiet middle-row links. `Get started`/`See live prospects` -> /signup,
// `Sign in` -> /login per the page's link contract; the rest stay in-page.
// `href` is typed as next/typedRoutes `Route`; the in-page hash targets are
// cast with `as Route` (the same idiom app-shell.tsx uses for its nav array).
const FOOTER_LINKS: ReadonlyArray<{ label: string; href: Route }> = [
  { label: "Product", href: "#features" as Route },
  { label: "Security", href: "#credibility" as Route },
  { label: "Sign in", href: "/login" as Route },
  { label: "Get started", href: "/signup" as Route },
];

export function RedoFooter() {
  return (
    <footer className="border-t border-[var(--border,rgba(30,64,175,0.1))] px-6 py-10">
      <div className="mx-auto flex max-w-7xl flex-col gap-8">
        {/* Middle row — muted nav links, separated from the credit line below
            by a faint hairline. Each is a real <Link> with an animated
            underline that grows from the left on hover/focus. */}
        <nav aria-label="Footer" className="flex flex-wrap items-center justify-center gap-x-7 gap-y-3 sm:justify-start">
          {FOOTER_LINKS.map((link) => (
            <Link
              key={link.label}
              href={link.href}
              className="group relative text-xs font-medium text-[var(--text-muted,#94a3b8)] transition-colors duration-200 hover:text-[var(--text,#0f172a)] focus-visible:text-[var(--text,#0f172a)] focus-visible:outline-none"
            >
              {link.label}
              <span
                aria-hidden
                className="absolute -bottom-1 left-0 h-px w-full origin-left scale-x-0 bg-gradient-to-r from-[var(--accent,#6366f1)] to-[var(--accent-2,#8b5cf6)] transition-transform duration-200 ease-out group-hover:scale-x-100 group-focus-visible:scale-x-100"
              />
            </Link>
          ))}
        </nav>

        {/* Credit row — brand mark + name on the left, copyright on the right,
            stacked on mobile. Sits above a hairline that visually seats it on
            the base of the page. */}
        <div className="flex flex-col items-center gap-4 border-t border-[var(--border,rgba(30,64,175,0.08))] pt-8 sm:flex-row sm:justify-between">
          <div className="flex items-center gap-2">
            <BrandMark size={28} />
            <span className="text-xs font-semibold text-[var(--text,#0f172a)]">
              DOX — Institutional Finance Intelligence
            </span>
          </div>
          <p className="text-xs text-[var(--text-muted,#94a3b8)]">
            &copy; {new Date().getFullYear()} Alchemy Dev. All rights reserved. Confidential.
          </p>
        </div>
      </div>
    </footer>
  );
}
