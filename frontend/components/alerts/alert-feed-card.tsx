"use client";

import Link from "next/link";

import { AlertPriorityBadge } from "@/components/alerts/alert-priority-badge";
import { formatRelativeTime } from "@/lib/format";
import type { AlertListItem } from "@/lib/types";

// Priority → timeline dot color. Mirrors the AlertPriorityBadge tone map.
const PRIORITY_DOT: Record<string, string> = {
  critical: "bg-danger ring-danger/25",
  high: "bg-amber-500 ring-amber-400/25",
  medium: "bg-blue ring-blue/25",
  low: "bg-slate-400 ring-slate-300/40"
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
    <article className="rounded-2xl border border-slate-200/80 bg-white p-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">
            Activity Feed
          </p>
          <h2 className="mt-2 text-xl font-semibold text-navy">Recent filing alerts</h2>
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

      <div className="mt-6">
        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, index) => (
              <div key={`alert-loading-${index}`} className="rounded-2xl border border-slate-100 px-4 py-4">
                <div className="h-4 w-48 animate-pulse rounded bg-slate-100" />
                <div className="mt-3 h-3 w-full animate-pulse rounded bg-slate-100" />
              </div>
            ))}
          </div>
        ) : alerts.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-8 text-sm text-slate-500">
            No filing alerts have been generated yet.
          </div>
        ) : (
          <ol className="relative space-y-4 pl-7">
            {/* Vertical connector line. */}
            <span
              aria-hidden
              className="absolute left-[11px] top-3 bottom-3 w-px bg-gradient-to-b from-slate-200 via-slate-200 to-transparent"
            />
            {alerts.map((alert) => {
              const dot = PRIORITY_DOT[alert.priority] ?? PRIORITY_DOT.low;
              return (
                <li
                  key={alert.id}
                  className={`relative rounded-2xl border border-slate-100 px-4 py-4 transition hover:border-slate-200 hover:bg-slate-50/70 hover:shadow-sm ${alert.is_read ? "opacity-60" : ""}`}
                >
                  {/* Timeline dot — absolutely positioned so it straddles the connector line. */}
                  <span
                    aria-hidden
                    className={`absolute -left-[23px] top-5 h-3 w-3 rounded-full ring-4 ${dot}`}
                  />
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
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
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </article>
  );
}
