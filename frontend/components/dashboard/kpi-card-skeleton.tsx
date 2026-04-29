"use client";

// Skeleton placeholder that mirrors KpiCard geometry so the KPI grid
// reserves its real layout while /api/v1/stats resolves. Renders one
// card; dashboard-home-client.tsx renders four in the grid slots.
//
// Uses the same inline animate-pulse / surface-2 token pattern as the
// alert + visited skeletons shipped earlier today — no shared
// <Skeleton /> primitive exists, and adding one is out of scope.
export function KpiCardSkeleton() {
  return (
    <article
      aria-busy
      className="relative overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
      style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
    >
      {/* Head: 36x36 icon chip + label */}
      <div className="mb-3.5 flex items-center gap-2.5">
        <div className="h-9 w-9 shrink-0 animate-pulse rounded-[10px] bg-[var(--surface-2,#f1f6fd)]" />
        <div className="h-3 w-28 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
      </div>

      {/* 34px value placeholder */}
      <div className="mb-1.5 h-9 w-24 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />

      {/* Helper line */}
      <div className="h-3 w-44 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />

      {/* Sparkline placeholder, mirrors KpiCard's mt-3 h-9 w-full */}
      <div className="mt-3 h-9 w-full animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
    </article>
  );
}
