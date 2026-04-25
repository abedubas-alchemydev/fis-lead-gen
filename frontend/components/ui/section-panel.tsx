import type { ReactNode } from "react";

// Section panel for /master-list/{id}. Mirrors the panel pattern used on
// /dashboard and /master-list (filter card, table card, KPI strip): rounded-2xl,
// var(--surface) bg, var(--border) border, shadow-card elevation. Header row
// stacks an eyebrow + title with an optional right-rail action. Replaces the
// pre-restyle QuadrantCard helper which used rounded-[28px] + shadow-shell.
export function SectionPanel({
  eyebrow,
  title,
  headerAction,
  children,
}: {
  eyebrow: string;
  title: string;
  headerAction?: ReactNode;
  children: ReactNode;
}) {
  return (
    <article
      className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
      style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
    >
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
            {eyebrow}
          </p>
          <h2 className="mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            {title}
          </h2>
        </div>
        {headerAction}
      </div>
      {children}
    </article>
  );
}
