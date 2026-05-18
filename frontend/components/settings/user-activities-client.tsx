"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  Bookmark,
  Clock,
  Eye,
  Loader2,
  LogIn,
  LogOut,
  Send,
} from "lucide-react";

import { ApiError, getUserActivities } from "@/lib/api";
import type {
  AdminUserActivityEventType,
  AdminUserActivityRow,
  AdminUserActivitiesResponse,
} from "@/lib/types";

const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";

const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";

const CARD_TITLE =
  "mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]";

const PAGE_SIZE = 50;

// BE collapses login + logout under one filter chip but each row keeps
// its own discriminator so the glyph differs in the table.
type Filter = "all" | "login" | "view" | "save" | "outreach";

const FILTERS: ReadonlyArray<{ value: Filter; label: string }> = [
  { value: "all", label: "All" },
  { value: "login", label: "Login" },
  { value: "view", label: "Viewed" },
  { value: "save", label: "Saved" },
  { value: "outreach", label: "Outreach" },
];

function targetHref(row: AdminUserActivityRow): Route | null {
  if (row.target_id == null) return null;
  if (row.target_type === "broker_dealer") {
    return `/master-list/${row.target_id}` as Route;
  }
  if (row.target_type === "advisor") {
    return `/advisor-list/${row.target_id}` as Route;
  }
  // institutional_investor — repointed to /advisor-list/{id} since
  // institutional investors are 13F-filing advisors (PR #433 reverted
  // the separate /investors tab; their detail still lives on the
  // advisor profile).
  if (row.target_type === "institutional_investor") {
    return `/advisor-list/${row.target_id}` as Route;
  }
  return null;
}

function targetTypeLabel(
  type: AdminUserActivityRow["target_type"]
): string {
  if (type === "broker_dealer") return "Broker-dealer";
  if (type === "advisor") return "Investment advisor";
  if (type === "institutional_investor") return "Institutional investor";
  return "";
}

function eventGlyph(eventType: AdminUserActivityEventType): JSX.Element {
  switch (eventType) {
    case "login":
      return <LogIn className="h-4 w-4" aria-hidden />;
    case "logout":
      return <LogOut className="h-4 w-4" aria-hidden />;
    case "view":
      return <Eye className="h-4 w-4" aria-hidden />;
    case "save":
      return <Bookmark className="h-4 w-4" aria-hidden />;
    case "outreach":
      return <Send className="h-4 w-4" aria-hidden />;
  }
}

function eventLabel(eventType: AdminUserActivityEventType): string {
  switch (eventType) {
    case "login":
      return "Logged in";
    case "logout":
      return "Logged out";
    case "view":
      return "Viewed";
    case "save":
      return "Saved";
    case "outreach":
      return "Sent outreach";
  }
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

function summarizeDetails(row: AdminUserActivityRow): string {
  const d = row.details;
  if (!d) return "";
  if (row.event_type === "login" || row.event_type === "logout") {
    const ip = typeof d.ip === "string" ? d.ip : null;
    const ua = typeof d.user_agent === "string" ? d.user_agent : null;
    if (ip && ua) return `${ip} · ${ua}`;
    if (ip) return ip;
    if (ua) return ua;
    return "";
  }
  if (row.event_type === "view" && typeof d.visit_count === "number") {
    return `${d.visit_count} visit${d.visit_count === 1 ? "" : "s"}`;
  }
  if (row.event_type === "save" && typeof d.list_name === "string") {
    return d.list_name;
  }
  if (row.event_type === "outreach") {
    const status = typeof d.status === "string" ? d.status : null;
    const subject = typeof d.subject === "string" ? d.subject : null;
    const error = typeof d.error === "string" ? d.error : null;
    if (status === "failed" && error) return `failed · ${error}`;
    if (subject) return subject;
    if (status) return status;
    return "";
  }
  return "";
}

export function UserActivitiesClient({
  user,
}: {
  user: { id: string; email: string; name: string };
}) {
  const [data, setData] = useState<AdminUserActivitiesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [offset, setOffset] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getUserActivities(user.id, {
        limit: PAGE_SIZE,
        offset,
        type:
          filter === "all"
            ? undefined
            : (filter as Exclude<AdminUserActivityEventType, "logout">),
      });
      setData(result);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail || err.message
          : err instanceof Error
            ? err.message
            : "Failed to load activity feed."
      );
    } finally {
      setLoading(false);
    }
  }, [user.id, offset, filter]);

  useEffect(() => {
    void load();
  }, [load]);

  const total = data?.total ?? 0;
  const items = data?.items ?? [];
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;

  function selectFilter(next: Filter) {
    setFilter(next);
    setOffset(0);
  }

  const displayName = user.name?.trim() || user.email;

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Workspace <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link href="/settings/users" className="hover:underline">Users</Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link
              href={`/settings/users/${user.id}` as Route}
              className="hover:underline"
            >
              {displayName}
            </Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Activity
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Activity for {displayName}
          </h1>
          <p className="mt-2 max-w-3xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Login history, firm views, saves, and outreach sends — one timestamp-ordered stream.
          </p>
        </div>
        <Link
          href={`/settings/users/${user.id}` as Route}
          className="ml-auto inline-flex items-center gap-1.5 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-2 text-xs font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          Back to user
        </Link>
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      <div className={CARD}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className={EYEBROW}>Activity</p>
            <h2 className={CARD_TITLE}>
              {total === 0
                ? "No activity yet"
                : `${total.toLocaleString()} ${total === 1 ? "event" : "events"}`}
            </h2>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {FILTERS.map((f) => {
            const active = filter === f.value;
            return (
              <button
                key={f.value}
                type="button"
                onClick={() => selectFilter(f.value)}
                className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition ${
                  active
                    ? "border-[var(--accent,#1e40af)] bg-[var(--accent,#1e40af)] text-white"
                    : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text,#0f172a)] hover:bg-[var(--surface-2,#f1f6fd)]"
                }`}
              >
                {f.label}
              </button>
            );
          })}
        </div>

        {loading ? (
          <div className="mt-6 flex items-center justify-center gap-2 py-12 text-sm text-[var(--text-dim,#475569)]">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            Loading activity…
          </div>
        ) : items.length === 0 ? (
          <div className="mt-4 rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] px-4 py-12 text-center">
            <p className="text-sm font-semibold text-[var(--text,#0f172a)]">
              {filter === "all"
                ? "This user hasn't done anything yet"
                : "No matching events"}
            </p>
            <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
              {filter === "all"
                ? "Once they log in and start browsing, their actions will land here."
                : "Try a different filter or clear it to see everything."}
            </p>
          </div>
        ) : (
          <div className="mt-4 overflow-x-auto rounded-xl border border-[var(--border,rgba(30,64,175,0.1))]">
            <table className="w-full text-left text-sm">
              <thead className="bg-[var(--surface-2,#f1f6fd)] text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
                <tr>
                  <th className="px-5 py-3">Event</th>
                  <th className="px-5 py-3">Target</th>
                  <th className="px-5 py-3">Details</th>
                  <th className="px-5 py-3">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
                {items.map((row, idx) => {
                  const href = targetHref(row);
                  const summary = summarizeDetails(row);
                  return (
                    <tr
                      key={`${row.event_type}-${row.timestamp}-${row.target_id ?? "none"}-${idx}`}
                      className="hover:bg-[var(--surface-2,#f1f6fd)]/50"
                    >
                      <td className="px-5 py-4 font-semibold text-[var(--text,#0f172a)]">
                        <span className="inline-flex items-center gap-2 text-[var(--text,#0f172a)]">
                          <span className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--surface-2,#f1f6fd)] text-[var(--accent,#1e40af)]">
                            {eventGlyph(row.event_type)}
                          </span>
                          {eventLabel(row.event_type)}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-[var(--text-dim,#475569)]">
                        {row.target_name ? (
                          <span className="inline-flex flex-col">
                            {href ? (
                              <Link
                                href={href}
                                className="font-semibold text-[var(--text,#0f172a)] hover:underline"
                              >
                                {row.target_name}
                              </Link>
                            ) : (
                              <span className="font-semibold text-[var(--text,#0f172a)]">
                                {row.target_name}
                              </span>
                            )}
                            <span className="text-[11px] uppercase tracking-[0.04em] text-[var(--text-muted,#94a3b8)]">
                              {targetTypeLabel(row.target_type)}
                            </span>
                          </span>
                        ) : (
                          <span className="text-[var(--text-muted,#94a3b8)]">—</span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-[var(--text-dim,#475569)]">
                        <span
                          className="line-clamp-2"
                          title={summary || undefined}
                        >
                          {summary || "—"}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-[var(--text-muted,#94a3b8)]">
                        <span className="inline-flex items-center gap-1.5">
                          <Clock className="h-3.5 w-3.5" aria-hidden />
                          {formatDateTime(row.timestamp)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {total > PAGE_SIZE ? (
          <div className="mt-4 flex items-center justify-between gap-3">
            <p className="text-xs text-[var(--text-muted,#94a3b8)]">
              Showing {Math.min(offset + 1, total)}–{Math.min(offset + PAGE_SIZE, total)} of {total}
            </p>
            <div className="inline-flex gap-2">
              <button
                type="button"
                disabled={!canPrev || loading}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-2 text-xs font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                Prev
              </button>
              <button
                type="button"
                disabled={!canNext || loading}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-2 text-xs font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
