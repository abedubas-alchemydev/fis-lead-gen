"use client";

import { ThemeToggle } from "@/components/ui/theme-toggle";

// Verbatim replication of dashboard-redesign.html .topbar-actions block:
//   .topbar-actions { margin-left:auto; display:flex; align-items:center; gap:10px; }
//   .search { ...white bg, blue-800/10 border, 10px radius, 8px 14px padding, 320px wide, text-dim color, 10px gap }
//   .search kbd { JetBrains Mono, 11px, #dbeafe bg, 2px 6px padding, 4px radius, blue-800/16 border, text-dim color }
//   .icon-btn { 38x38, grid center, 10px radius, white bg, blue-800/10 border, text-dim color }
//   .icon-btn:hover { text color var(--text), bg #f1f6fd }
//   .icon-btn .dot { absolute top:6px right:6px, 8x8, red bg, round, 2px solid var(--bg) }
//
// Theme toggle is delegated to the shared `<ThemeToggle>` primitive so the
// button actually works (it was a static button before) and the same
// behavior is reusable on master-list + future authenticated pages.

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
      <div className="hidden w-[320px] items-center gap-2.5 rounded-[10px] border border-[rgba(30,64,175,0.1)] bg-white px-3.5 py-2 text-slate-600 md:flex">
        <SearchIconSvg />
        <input
          type="text"
          placeholder="Search broker-dealers, firms, CRDs..."
          className="flex-1 bg-transparent text-slate-900 placeholder:text-slate-600 focus:outline-none"
        />
        <kbd className="rounded-[4px] border border-[rgba(30,64,175,0.16)] bg-[#dbeafe] px-1.5 py-0.5 font-mono text-[11px] text-slate-600">
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
        className="relative grid h-[38px] w-[38px] place-items-center rounded-[10px] border border-[rgba(30,64,175,0.1)] bg-white text-slate-600 transition hover:bg-[#f1f6fd] hover:text-slate-900"
      >
        <BellIconSvg />
        <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full border-2 border-[#eaf3ff] bg-red-500" />
      </button>
    </div>
  );
}
