"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { AlertPriorityBadge } from "@/components/alerts/alert-priority-badge";
import { apiRequest, buildApiPath } from "@/lib/api";
import { formatDate, formatRelativeTime } from "@/lib/format";
import type {
  AlertListItem,
  AlertListResponse,
  AlertsBulkReadResponse,
  AlertReadResponse
} from "@/lib/types";

const formTypeOptions = ["All", "Form BD", "Form 17a-11"];
const priorityOptions = ["All", "critical", "high", "medium"];

export function AlertsClient({
  initialFormType = "All",
  initialPriority = "All"
}: {
  initialFormType?: string;
  initialPriority?: string;
}) {
  const [items, setItems] = useState<AlertListItem[]>([]);
  const [formType, setFormType] = useState(initialFormType);
  const [priority, setPriority] = useState(initialPriority);
  const [readFilter, setReadFilter] = useState<"all" | "unread" | "read">("all");
  const [page, setPage] = useState(1);
  const [meta, setMeta] = useState<AlertListResponse["meta"]>({
    page: 1,
    limit: 20,
    total: 0,
    total_pages: 1
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const queryPath = useMemo(
    () =>
      buildApiPath("/api/v1/alerts", {
        form_type: formType === "All" ? undefined : [formType],
        priority: priority === "All" ? undefined : [priority],
        read: readFilter === "all" ? undefined : readFilter === "read",
        page,
        limit: 20
      }),
    [formType, priority, readFilter, page]
  );

  async function loadAlerts() {
    setLoading(true);
    setError(null);

    try {
      const response = await apiRequest<AlertListResponse>(queryPath);
      setItems(response.items);
      setMeta(response.meta);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load alerts.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAlerts();
  }, [queryPath]);

  async function markRead(alertId: number) {
    await apiRequest<AlertReadResponse>(`/api/v1/alerts/${alertId}/read`, { method: "PATCH" });
    setItems((current) => current.map((item) => (item.id === alertId ? { ...item, is_read: true } : item)));
  }

  async function markAllRead() {
    await apiRequest<AlertsBulkReadResponse>(
      buildApiPath("/api/v1/alerts/mark-all-read", {
        form_type: formType === "All" ? undefined : [formType],
        priority: priority === "All" ? undefined : [priority]
      }),
      { method: "POST" }
    );
    setItems((current) => current.map((item) => ({ ...item, is_read: true })));
  }

  return (
    <section className="space-y-6">
      <div className="rounded-[30px] border border-white/80 bg-white/92 p-6 shadow-shell">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <p className="text-sm font-medium uppercase tracking-[0.24em] text-blue">Alerts</p>
            <h1 className="mt-3 text-3xl font-semibold text-navy">Daily filing monitor</h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
              Review new Form BD registrations and Form 17a-11 deficiency notices, then click through
              to the firm profile for full context.
            </p>
          </div>
          <button
            type="button"
            onClick={markAllRead}
            className="rounded-2xl border border-slate-200 px-4 py-3 text-sm font-medium text-slate-700"
          >
            Mark all as read
          </button>
        </div>

        <div className="mt-6 grid gap-3 md:grid-cols-3">
          <label className="text-sm font-medium text-slate-700">
            Type
            <select
              value={formType}
              onChange={(event) => {
                setFormType(event.target.value);
                setPage(1);
              }}
              className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
            >
              {formTypeOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            Priority
            <select
              value={priority}
              onChange={(event) => {
                setPriority(event.target.value);
                setPage(1);
              }}
              className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm capitalize"
            >
              {priorityOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            Status
            <select
              value={readFilter}
              onChange={(event) => {
                setReadFilter(event.target.value as "all" | "unread" | "read");
                setPage(1);
              }}
              className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
            >
              <option value="all">All</option>
              <option value="unread">Unread</option>
              <option value="read">Read</option>
            </select>
          </label>
        </div>
      </div>

      <div className="overflow-hidden rounded-[30px] border border-white/80 bg-white/92 shadow-shell">
        <div className="border-b border-slate-200 px-6 py-4 text-sm text-slate-600">
          {meta.total.toLocaleString()} alerts found
        </div>

        {error ? <div className="px-6 py-5 text-sm text-danger">{error}</div> : null}

        <div className="overflow-x-auto">
          <table className="min-w-full text-left">
            <thead className="bg-slate-50">
              <tr>
                {["Type", "Firm", "Date", "Priority", "Status", "Summary"].map((label) => (
                  <th key={label} className="px-6 py-4 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 8 }).map((_, index) => (
                  <tr key={`loading-${index}`} className="border-t border-slate-100">
                    {Array.from({ length: 6 }).map((__, cellIndex) => (
                      <td key={cellIndex} className="px-6 py-4">
                        <div className="h-4 w-full animate-pulse rounded bg-slate-100" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-sm text-slate-500">
                    No alerts match the current filters.
                  </td>
                </tr>
              ) : (
                items.map((alert) => (
                  <tr key={alert.id} className="border-t border-slate-100 hover:bg-slate-50/80">
                    <td className="px-6 py-4 text-sm font-medium text-slate-700">{alert.form_type}</td>
                    <td className="px-6 py-4 text-sm">
                      <Link href={`/master-list/${alert.bd_id}`} className="font-medium text-navy hover:text-blue">
                        {alert.firm_name}
                      </Link>
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-600">
                      <div>{formatDate(alert.filed_at)}</div>
                      <div className="mt-1 text-xs text-slate-500">{formatRelativeTime(alert.filed_at)}</div>
                    </td>
                    <td className="px-6 py-4 text-sm">
                      <AlertPriorityBadge priority={alert.priority} />
                    </td>
                    <td className="px-6 py-4 text-sm">
                      {alert.is_read ? (
                        <span className="text-slate-500">Read</span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => void markRead(alert.id)}
                          className="rounded-full bg-blue/10 px-3 py-1 text-xs font-medium text-blue"
                        >
                          Unread
                        </button>
                      )}
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-600">{alert.summary}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between border-t border-slate-200 px-6 py-4 text-sm text-slate-600">
          <p>
            Page {meta.page} of {meta.total_pages}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={meta.page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              className="rounded-2xl border border-slate-200 px-4 py-2 disabled:opacity-50"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={meta.page >= meta.total_pages}
              onClick={() => setPage((current) => Math.min(meta.total_pages, current + 1))}
              className="rounded-2xl border border-slate-200 px-4 py-2 disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
