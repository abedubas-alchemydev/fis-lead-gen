"use client";

import { AlertTriangle } from "lucide-react";

// Reusable error block for dashboard tiles whose initial fetch fails.
// Matches the LoadErrorCard pattern shipped on /alerts and /visited-firms
// — dashed-border surface-2 panel, red medallion, Retry button — so the
// dashboard's four tile error surfaces (stats KPI row, clearing
// distribution, top leads, lead volume trend) stay visually consistent
// with the rest of the app.
//
// Borderless on its own outer; callers either render it bare (e.g.
// inside an existing card body) or wrap it externally to match a
// surrounding tile.
export function DashboardErrorCard({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="my-2 rounded-2xl border border-dashed border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[rgba(239,68,68,0.1)] text-[var(--pill-red-text,#b91c1c)]">
        <AlertTriangle className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        {title}
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        {message}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-5 inline-flex h-[34px] items-center rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-4 text-[13px] font-semibold text-[var(--text-dim,#475569)] transition hover:text-[var(--text,#0f172a)]"
      >
        Retry
      </button>
    </div>
  );
}
