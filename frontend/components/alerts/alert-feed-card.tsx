"use client";

import Link from "next/link";

import { AlertPriorityBadge } from "@/components/alerts/alert-priority-badge";
import { formatRelativeTime } from "@/lib/format";
import type { AlertListItem } from "@/lib/types";

// Mirrors AlertPriorityBadge tone map. Applied to the card's left edge so
// priority is readable at a glance even before the pill is scanned.
const PRIORITY_EDGE: Record<string, string> = {
  critical: "border-l-[3px] border-l-danger",
  high: "border-l-[3px] border-l-amber-500",
  medium: "border-l-[3px] border-l-blue",
  low: "border-l-[3px] border-l-slate-300"
};

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
    <article className="rounded-[30px] border border-white/80 bg-white/88 p-7 shadow-shell backdrop-blur">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-medium uppercase tracking-[0.24em] text-blue">Activity Feed</p>
          <h2 className="mt-3 text-2xl font-semibold text-navy">Recent filing alerts</h2>
        </div>
        <Link
          href="/alerts"
          className="group inline-flex items-center gap-1 text-sm font-medium text-blue transition hover:gap-1.5"
        >
          View all
          <span aria-hidden className="transition group-hover:translate-x-0.5">→</span>
        </Link>
      </div>

      {error ? <div className="mt-5 rounded-2xl bg-red-50 px-4 py-3 text-sm text-danger">{error}</div> : null}

      <div className="mt-6 space-y-3">
        {loading
          ? Array.from({ length: 5 }).map((_, index) => (
              <div key={`alert-loading-${index}`} className="rounded-2xl border border-slate-100 px-4 py-4">
                <div className="h-4 w-48 animate-pulse rounded bg-slate-100" />
                <div className="mt-3 h-3 w-full animate-pulse rounded bg-slate-100" />
              </div>
            ))
          : alerts.length === 0
            ? (
              <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-8 text-sm text-slate-500">
                No filing alerts have been generated yet.
              </div>
              )
            : alerts.map((alert) => {
              const edge = PRIORITY_EDGE[alert.priority] ?? PRIORITY_EDGE.low;
              return (
                <div
                  key={alert.id}
                  className={`rounded-2xl border border-slate-100 px-4 py-4 transition hover:border-slate-200 hover:bg-slate-50/70 hover:shadow-sm ${edge} ${alert.is_read ? "opacity-65" : ""}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      {!alert.is_read ? (
                        <span aria-label="Unread" className="h-2 w-2 rounded-full bg-blue ring-2 ring-blue/20" />
                      ) : null}
                      <AlertPriorityBadge priority={alert.priority} />
                      <p className="text-xs uppercase tracking-[0.18em] text-slate-500">{alert.form_type}</p>
                    </div>
                    <p className="text-xs tabular-nums text-slate-500">{formatRelativeTime(alert.filed_at)}</p>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <Link
                        href={`/master-list/${alert.bd_id}`}
                        className="text-sm font-semibold text-navy transition hover:text-blue"
                      >
                        {alert.firm_name}
                      </Link>
                      <p className="mt-1 text-sm leading-5 text-slate-600">{alert.summary}</p>
                    </div>
                    {!alert.is_read && onMarkRead ? (
                      <button
                        type="button"
                        onClick={() => onMarkRead(alert.id)}
                        className="shrink-0 rounded-full border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600 transition hover:border-blue/40 hover:bg-blue/5 hover:text-blue"
                      >
                        Mark read
                      </button>
                    ) : null}
                  </div>
                </div>
              );
            })}
      </div>
    </article>
  );
}
