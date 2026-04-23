import type { ReactNode } from "react";

// Variants mirror the `.pill.*` selectors in design-system.css. Classes use
// Tailwind arbitrary values reading the project's CSS custom properties
// (var(--pill-red-text), etc.) so dark mode flips automatically via the
// [data-theme="dark"] overrides in globals.css.
//
// fd/omni keep their light-mode tints hard-coded because the mockup didn't
// promote those two colors to tokens; if a dark-mode contrast issue surfaces
// later, add --pill-fd-text / --pill-omni-text in globals.css and swap in.
export type PillVariant =
  | "healthy"
  | "ok"
  | "risk"
  | "hot"
  | "warm"
  | "cold"
  | "self"
  | "fd"
  | "omni"
  | "unknown"
  | "competitor"
  | "critical"
  | "warning"
  | "info"
  | "form";

const variantClasses: Record<PillVariant, string> = {
  healthy: "bg-[rgba(16,185,129,0.12)] text-[var(--pill-green-text,#047857)] border-[rgba(16,185,129,0.25)]",
  ok: "bg-[rgba(59,130,246,0.12)] text-[var(--pill-blue-text,#1d4ed8)] border-[rgba(59,130,246,0.25)]",
  risk: "bg-[rgba(239,68,68,0.12)] text-[var(--pill-red-text,#b91c1c)] border-[rgba(239,68,68,0.25)]",
  hot: "bg-[rgba(239,68,68,0.12)] text-[var(--pill-red-text,#b91c1c)] border-[rgba(239,68,68,0.25)]",
  warm: "bg-[rgba(245,158,11,0.12)] text-[var(--pill-amber-text,#b45309)] border-[rgba(245,158,11,0.25)]",
  cold: "bg-[rgba(59,130,246,0.12)] text-[var(--pill-blue-text,#1d4ed8)] border-[rgba(59,130,246,0.25)]",
  self: "bg-[rgba(139,92,246,0.12)] text-[var(--pill-purple-text,#6d28d9)] border-[rgba(139,92,246,0.25)]",
  fd: "bg-[rgba(99,102,241,0.12)] text-[#4338ca] border-[rgba(99,102,241,0.25)]",
  omni: "bg-[rgba(6,182,212,0.12)] text-[#0e7490] border-[rgba(6,182,212,0.25)]",
  unknown:
    "bg-[var(--surface-2,#f1f6fd)] text-[var(--text-muted,#94a3b8)] border-[var(--border,rgba(30,64,175,0.1))]",
  competitor: "bg-[rgba(239,68,68,0.08)] text-[var(--pill-red-text,#b91c1c)] border-[rgba(239,68,68,0.2)]",
  critical: "bg-[rgba(239,68,68,0.12)] text-[var(--pill-red-text,#b91c1c)] border-[rgba(239,68,68,0.25)]",
  warning: "bg-[rgba(245,158,11,0.12)] text-[var(--pill-amber-text,#b45309)] border-[rgba(245,158,11,0.25)]",
  info: "bg-[rgba(59,130,246,0.12)] text-[var(--pill-blue-text,#1d4ed8)] border-[rgba(59,130,246,0.25)]",
  form:
    "bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)] border-[var(--border-2,rgba(30,64,175,0.16))] font-mono",
};

export interface PillProps {
  variant: PillVariant;
  children: ReactNode;
  className?: string;
}

export function Pill({ variant, children, className = "" }: PillProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-[3px] text-[11px] font-semibold tracking-[0.02em] ${variantClasses[variant]} ${className}`}
    >
      {children}
    </span>
  );
}
