"use client";

import { AlertTriangle } from "lucide-react";

// Generic error medallion used by both /email-extractor pages — hub
// (history fetch failed) and detail (scan load failed OR scan.status
// === "failed"). Mirrors the DashboardErrorCard / LoadErrorCard
// shape: dashed border + red medallion + Retry button — so the error
// surfaces stay visually consistent with /alerts, /visited-firms,
// and /dashboard.
export function EmailExtractorErrorCard({
  title,
  message,
  onRetry,
  retryLabel = "Retry",
}: {
  title: string;
  message: string;
  onRetry: () => void;
  retryLabel?: string;
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
        {retryLabel}
      </button>
    </div>
  );
}
