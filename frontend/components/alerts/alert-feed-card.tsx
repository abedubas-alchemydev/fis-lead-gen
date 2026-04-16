"use client";

import Link from "next/link";

import { AlertPriorityBadge } from "@/components/alerts/alert-priority-badge";
import { formatRelativeTime } from "@/lib/format";
import type { AlertListItem } from "@/lib/types";

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
        <Link href="/alerts" className="text-sm font-medium text-blue">
          View all
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
            : alerts.map((alert) => (
              <div key={alert.id} className="rounded-2xl border border-slate-100 px-4 py-4 transition hover:bg-slate-50/80">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <span className={`h-2.5 w-2.5 rounded-full ${alert.is_read ? "bg-slate-200" : "bg-blue"}`} />
                    <AlertPriorityBadge priority={alert.priority} />
                    <p className="text-xs uppercase tracking-[0.18em] text-slate-500">{alert.form_type}</p>
                  </div>
                  <p className="text-xs text-slate-500">{formatRelativeTime(alert.filed_at)}</p>
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <Link href={`/master-list/${alert.bd_id}`} className="text-sm font-semibold text-navy hover:text-blue">
                      {alert.firm_name}
                    </Link>
                    <p className="mt-1 text-sm text-slate-600">{alert.summary}</p>
                  </div>
                  {!alert.is_read && onMarkRead ? (
                    <button
                      type="button"
                      onClick={() => onMarkRead(alert.id)}
                      className="rounded-full border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600"
                    >
                      Mark read
                    </button>
                  ) : null}
                </div>
              </div>
            ))}
      </div>
    </article>
  );
}
