"use client";

import { ThemeToggle } from "@/components/ui/theme-toggle";

// Surface / border / kbd colors come from the global token palette so the
// topbar follows the active theme. Theme toggle is delegated to the shared
// `<ThemeToggle>` primitive.

function SearchIconSvg() {
  return (
    <svg
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      viewBox="0 0 24 24"
      aria-hidden
    >
      <circle cx="11" cy="11" r="8" />
      <path d="M21 21l-4.35-4.35" />
    </svg>
  );
}

function BellIconSvg() {
  return (
    <svg
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      viewBox="0 0 24 24"
      aria-hidden
    >
      <path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.7 21a2 2 0 01-3.4 0" />
    </svg>
  );
}

export function TopActions() {
  return (
    <div className="flex items-center gap-2.5">
      {/* .search */}
      <div className="hidden w-[320px] items-center gap-2.5 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3.5 py-2 text-[var(--text-dim,#475569)] md:flex">
        <SearchIconSvg />
        <input
          type="text"
          placeholder="Search broker-dealers, firms, CRDs..."
          className="flex-1 bg-transparent text-[var(--text,#0f172a)] placeholder:text-[var(--text-muted,#94a3b8)] focus:outline-none"
        />
        <kbd className="rounded-[4px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-3,#dbeafe)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-dim,#475569)]">
          ⌘K
        </kbd>
      </div>

      {/* Theme toggle — delegates to the shared <ThemeToggle> so it actually
          persists + flips data-theme on <html>. The old static button with
          MoonIconSvg is retired. */}
      <ThemeToggle />

      {/* Notifications (.icon-btn with .dot) */}
      <button
        type="button"
        aria-label="Notifications"
        className="relative grid h-[38px] w-[38px] place-items-center rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
      >
        <BellIconSvg />
        <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full border-2 border-[var(--bg,#eaf3ff)] bg-[var(--red,#ef4444)]" />
      </button>
    </div>
  );
}
