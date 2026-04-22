"use client";

import Link from "next/link";

import { formatRelativeTime } from "@/lib/format";
import type { AlertListItem } from "@/lib/types";

// Priority → mockup dot + pill styling.
type PriorityKey = "critical" | "high" | "medium" | "low";

const DOT_STYLE: Record<PriorityKey, string> = {
  critical: "bg-red-500 shadow-[0_0_0_4px_rgba(239,68,68,0.15)]",
  high: "bg-amber-500 shadow-[0_0_0_4px_rgba(245,158,11,0.15)]",
  medium: "bg-blue-500 shadow-[0_0_0_4px_rgba(59,130,246,0.15)]",
  low: "bg-slate-400 shadow-[0_0_0_4px_rgba(148,163,184,0.15)]"
};

const PILL_STYLE: Record<PriorityKey, string> = {
  critical: "bg-red-500/12 text-red-700 border-red-500/25",
  high: "bg-amber-500/12 text-amber-700 border-amber-500/25",
  medium: "bg-blue-500/12 text-blue-700 border-blue-500/25",
  low: "bg-slate-100 text-slate-600 border-slate-200"
};

const PILL_LABEL: Record<PriorityKey, string> = {
  critical: "Critical",
  high: "Warning",
  medium: "Info",
  low: "Info"
};

function resolvePriority(raw: string): PriorityKey {
  if (raw === "critical" || raw === "high" || raw === "medium" || raw === "low") return raw;
  return "low";
}

export function AlertFeedCard({
  alerts,
  loading,
  error,
  onMarkRead
}: {
  alerts: AlertListItem[];
  loading: boolean;
  error: string | null;
  onMarkRead?: (alertId: number) => void;
}) {
  return (
    <article className="rounded-2xl border border-slate-200/70 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05)]">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-slate-900">Activity feed</h2>
          <p className="mt-0.5 text-xs text-slate-500">Recent filing alerts</p>
        </div>
        <Link
          href="/alerts"
          className="inline-flex items-center gap-1 text-xs font-semibold text-violet-600 transition hover:text-violet-700"
        >
          View all
          <svg width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
            <path d="M9 18l6-6-6-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </Link>
      </div>

      {error ? (
        <div className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
      ) : null}

      <div>
        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={`alert-loading-${index}`} className="border-t border-slate-200/70 py-3.5 first:border-t-0">
                <div className="h-3 w-32 animate-pulse rounded bg-slate-100" />
                <div className="mt-2 h-4 w-48 animate-pulse rounded bg-slate-100" />
                <div className="mt-2 h-3 w-full animate-pulse rounded bg-slate-100" />
              </div>
            ))}
          </div>
        ) : alerts.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-200 px-4 py-8 text-center text-sm text-slate-500">
            No filing alerts have been generated yet.
          </div>
        ) : (
          <div>
            {alerts.map((alert) => {
              const priority = resolvePriority(alert.priority);
              return (
                <div
                  key={alert.id}
                  className={`flex gap-3 border-t border-slate-200/70 py-3.5 first:border-t-0 ${
                    alert.is_read ? "opacity-60" : ""
                  }`}
                >
                  <span
                    aria-hidden
                    className={`mt-2 h-2 w-2 shrink-0 rounded-full ${DOT_STYLE[priority]}`}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="mb-1 flex items-center gap-2">
                      <span
                        className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.04em] ${PILL_STYLE[priority]}`}
                      >
                        {PILL_LABEL[priority]}
                      </span>
                      <span className="rounded-full border border-slate-200 bg-slate-100 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-slate-600">
                        {alert.form_type}
                      </span>
                      <span className="ml-auto text-[11px] text-slate-500">
                        {formatRelativeTime(alert.filed_at)}
                      </span>
                    </div>
                    <Link
                      href={`/master-list/${alert.bd_id}`}
                      className="mb-1 block text-sm font-semibold text-slate-900 transition hover:text-violet-600"
                    >
                      {alert.firm_name}
                    </Link>
                    <p className="text-[12.5px] leading-5 text-slate-600">{alert.summary}</p>
                    <div className="mt-2.5 flex gap-2">
                      <Link
                        href={`/master-list/${alert.bd_id}`}
                        className="rounded-md border border-violet-500/30 px-2.5 py-1 text-[11px] font-semibold text-violet-600 transition hover:bg-violet-500/5"
                      >
                        Review
                      </Link>
                      {!alert.is_read && onMarkRead ? (
                        <button
                          type="button"
                          onClick={() => onMarkRead(alert.id)}
                          className="rounded-md border border-slate-200 bg-transparent px-2.5 py-1 text-[11px] font-semibold text-slate-600 transition hover:bg-slate-50 hover:text-slate-900"
                        >
                          Mark read
                        </button>
                      ) : null}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </article>
  );
}
