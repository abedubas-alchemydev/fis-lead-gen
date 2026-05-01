"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";

import { AlertTriangle, ArrowRight, Check, CheckCheck } from "lucide-react";

import { TopActions } from "@/components/layout/top-actions";
import { Pill, type PillVariant } from "@/components/ui/pill";
import { Segmented, type SegmentedItem } from "@/components/ui/segmented";
import { apiRequest, buildApiPath } from "@/lib/api";
import { formatDate, formatRelativeTime, viewableFilingUrl } from "@/lib/format";
import type {
  AlertListItem,
  AlertListResponse,
  AlertsBulkReadResponse,
  AlertReadResponse,
} from "@/lib/types";

import { AlertsLoadingSkeleton } from "./alerts-loading-skeleton";
import { EmptyAlertsState } from "./empty-alerts-state";

// ── Category tab catalog ──────────────────────────────────────────────
// Sprint 4 task #18: Deshorn flagged at the 2026-04-27 meeting that
// deficiency notices were leading the alerts page and felt noisy. Form
// BD filings are the primary alert category; deficiency notices the
// secondary. Three-tab UI on top of the existing filters card mirrors
// the master-list Primary / Alternative / All Firms pattern, wired to
// the BE `category` query param shipped in PR #122/#124.
type AlertCategory = "form_bd" | "deficiency" | "all";

const ALERT_CATEGORIES: ReadonlyArray<{ value: AlertCategory; label: string }> = [
  { value: "form_bd", label: "Form BD" },
  { value: "deficiency", label: "Deficiency Notices" },
  { value: "all", label: "All Alerts" },
];

const ALERT_CATEGORY_VALUES: ReadonlyArray<AlertCategory> = [
  "form_bd",
  "deficiency",
  "all",
];

// Default lands on Form BD per Deshorn's request. Default is omitted
// from the URL so plain `/alerts` is the canonical share-link.
const DEFAULT_CATEGORY: AlertCategory = "form_bd";

function parseCategoryParam(raw: string | null): AlertCategory {
  if (raw && (ALERT_CATEGORY_VALUES as ReadonlyArray<string>).includes(raw)) {
    return raw as AlertCategory;
  }
  return DEFAULT_CATEGORY;
}

// Filter option catalogs — kept as module-level constants so the arrays are
// referentially stable between renders (mirrors master-list-workspace-client).
const FORM_TYPE_OPTIONS = [
  { value: "All", label: "All form types" },
  { value: "Form BD", label: "Form BD" },
  { value: "Form 17a-11", label: "Form 17a-11" },
] as const;

const PRIORITY_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "All", label: "All" },
  { value: "critical", label: "Critical", dot: "risk" },
  { value: "high", label: "High", dot: "warm" },
  { value: "medium", label: "Medium", dot: "cold" },
];

const STATUS_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "all", label: "All" },
  { value: "unread", label: "Unread" },
  { value: "read", label: "Read" },
];

type ReadFilter = "all" | "unread" | "read";

type PriorityKey = "critical" | "high" | "medium" | "low";

const PRIORITY_PILL_VARIANT: Record<PriorityKey, PillVariant> = {
  critical: "critical",
  high: "warning",
  medium: "info",
  low: "info",
};

const PRIORITY_PILL_LABEL: Record<PriorityKey, string> = {
  critical: "Critical",
  high: "Warning",
  medium: "Info",
  low: "Info",
};

const PRIORITY_DOT_CLASS: Record<PriorityKey, string> = {
  critical: "bg-[var(--red,#ef4444)] shadow-[0_0_0_4px_rgba(239,68,68,0.15)]",
  high: "bg-[var(--amber,#f59e0b)] shadow-[0_0_0_4px_rgba(245,158,11,0.15)]",
  medium: "bg-[var(--blue,#3b82f6)] shadow-[0_0_0_4px_rgba(59,130,246,0.15)]",
  low: "bg-[var(--text-muted,#94a3b8)] shadow-[0_0_0_4px_rgba(148,163,184,0.15)]",
};

function resolvePriority(raw: string): PriorityKey {
  if (raw === "critical" || raw === "high" || raw === "medium" || raw === "low") return raw;
  return "low";
}

// Pagination helper — same shape as the one in master-list-workspace-client.
type PageToken = number | "…";
function paginationPages(current: number, total: number): PageToken[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const pages: PageToken[] = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) pages.push("…");
  for (let i = start; i <= end; i++) pages.push(i);
  if (end < total - 1) pages.push("…");
  pages.push(total);
  return pages;
}

interface AlertsClientProps {
  initialFormType?: string;
  initialPriority?: string;
}

export function AlertsClient({
  initialFormType = "All",
  initialPriority = "All",
}: AlertsClientProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Active category tab — read on every render from the URL so reload,
  // back-nav, and share-link drops all restore the correct tab without
  // a separate useState mirror that could drift.
  const tab = useMemo(
    () => parseCategoryParam(searchParams.get("tab")),
    [searchParams],
  );

  const [items, setItems] = useState<AlertListItem[]>([]);
  const [formType, setFormType] = useState(initialFormType);
  const [priority, setPriority] = useState(initialPriority);
  const [readFilter, setReadFilter] = useState<ReadFilter>("all");
  const [page, setPage] = useState(1);
  const [meta, setMeta] = useState<AlertListResponse["meta"]>({
    page: 1,
    limit: 20,
    total: 0,
    total_pages: 1,
  });
  const [loading, setLoading] = useState(true);
  // `loadError` is for the initial-fetch failure path — surfaces as a
  // centered "Couldn't load alerts" card with Retry inside the list
  // body. `error` (kept) is for inline action failures (markRead /
  // markAllRead) and renders as the existing red banner above the list.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [markAllPending, setMarkAllPending] = useState(false);
  // Per-tab unfiltered totals shown in the tab badges. null until the
  // bootstrap fetch lands; null sticks if a category fetch fails so
  // the badge just hides for that tab (Promise.allSettled, mirrors
  // the master-list bootstrap fix).
  const [categoryCounts, setCategoryCounts] = useState<
    Record<AlertCategory, number | null>
  >({
    form_bd: null,
    deficiency: null,
    all: null,
  });

  // Tab click handler. router.replace (not push) so tab clicks don't
  // pollute browser history with one entry per click — same Deshorn-
  // driven decision as the master-list filter commits. Default tab
  // (form_bd) is stripped from the URL so /alerts stays the canonical
  // share-link.
  const setTab = useCallback(
    (next: AlertCategory) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === DEFAULT_CATEGORY) {
        params.delete("tab");
      } else {
        params.set("tab", next);
      }
      const query = params.toString();
      const url = query ? `/alerts?${query}` : "/alerts";
      router.replace(url as Route, { scroll: false });
      setPage(1);
    },
    [router, searchParams],
  );

  const queryPath = useMemo(
    () =>
      buildApiPath("/api/v1/alerts", {
        // Always pin the category to whatever tab the URL says we're
        // on. The BE accepts `all` explicitly (it's a no-op there) so
        // the request shape stays uniform across tabs.
        category: tab,
        form_type: formType === "All" ? undefined : [formType],
        priority: priority === "All" ? undefined : [priority],
        read: readFilter === "all" ? undefined : readFilter === "read",
        page,
        limit: 20,
      }),
    [tab, formType, priority, readFilter, page],
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(null);

    async function loadAlerts() {
      try {
        const response = await apiRequest<AlertListResponse>(queryPath);
        if (!active) return;
        setItems(response.items);
        setMeta(response.meta);
      } catch (fetchError) {
        if (!active) return;
        setItems([]);
        setLoadError(
          fetchError instanceof Error
            ? fetchError.message
            : "Unable to load alerts.",
        );
      } finally {
        if (active) setLoading(false);
      }
    }

    void loadAlerts();
    return () => {
      active = false;
    };
  }, [queryPath, reloadKey]);

  // One-shot tab-count bootstrap. Three `?category=X&limit=1` probes in
  // parallel; Promise.allSettled so a single failed count never wipes
  // the tab row — mirrors the master-list filter-bootstrap resilience
  // pattern from the same sprint.
  useEffect(() => {
    let active = true;

    async function loadCategoryCounts() {
      const [formBdResult, deficiencyResult, allResult] = await Promise.allSettled([
        apiRequest<AlertListResponse>(
          buildApiPath("/api/v1/alerts", { category: "form_bd", limit: 1 }),
        ),
        apiRequest<AlertListResponse>(
          buildApiPath("/api/v1/alerts", { category: "deficiency", limit: 1 }),
        ),
        apiRequest<AlertListResponse>(
          buildApiPath("/api/v1/alerts", { category: "all", limit: 1 }),
        ),
      ]);
      if (!active) return;

      setCategoryCounts({
        form_bd:
          formBdResult.status === "fulfilled"
            ? formBdResult.value.meta.total
            : null,
        deficiency:
          deficiencyResult.status === "fulfilled"
            ? deficiencyResult.value.meta.total
            : null,
        all:
          allResult.status === "fulfilled" ? allResult.value.meta.total : null,
      });
    }

    void loadCategoryCounts();
    return () => {
      active = false;
    };
  }, []);

  async function markRead(alertId: number) {
    try {
      await apiRequest<AlertReadResponse>(`/api/v1/alerts/${alertId}/read`, { method: "PATCH" });
      setItems((current) =>
        current.map((alert) => (alert.id === alertId ? { ...alert, is_read: true } : alert)),
      );
    } catch (markError) {
      setError(markError instanceof Error ? markError.message : "Unable to update alert state.");
    }
  }

  async function markAllRead() {
    setMarkAllPending(true);
    try {
      await apiRequest<AlertsBulkReadResponse>(
        buildApiPath("/api/v1/alerts/mark-all-read", {
          form_type: formType === "All" ? undefined : [formType],
          priority: priority === "All" ? undefined : [priority],
        }),
        { method: "POST" },
      );
      setItems((current) => current.map((alert) => ({ ...alert, is_read: true })));
    } catch (bulkError) {
      setError(bulkError instanceof Error ? bulkError.message : "Unable to mark alerts as read.");
    } finally {
      setMarkAllPending(false);
    }
  }

  function clearFilters() {
    setFormType("All");
    setPriority("All");
    setReadFilter("all");
    setPage(1);
  }

  const activeFilterCount = useMemo(() => {
    let count = 0;
    if (formType !== "All") count += 1;
    if (priority !== "All") count += 1;
    if (readFilter !== "all") count += 1;
    return count;
  }, [formType, priority, readFilter]);

  const unreadCount = useMemo(
    () => items.reduce((sum, alert) => sum + (alert.is_read ? 0 : 1), 0),
    [items],
  );

  const pages = paginationPages(meta.page, meta.total_pages);

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* ── Topbar ───────────────────────────────────────────────────────── */}
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Alerts
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Daily filing monitor
          </h1>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      {/* ── Tabs (category switch — Form BD primary, Deficiency secondary,
              All Alerts everything) ──────────────────────────────────────── */}
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <div
          role="tablist"
          aria-label="Alert category"
          className="inline-flex rounded-[12px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] p-1"
        >
          {ALERT_CATEGORIES.map((mode) => {
            const active = tab === mode.value;
            const count = categoryCounts[mode.value];
            return (
              <button
                key={mode.value}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setTab(mode.value)}
                className={`inline-flex items-center gap-2 rounded-[10px] px-4 py-2 text-[13px] transition ${
                  active
                    ? "bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)]"
                    : "font-medium text-[var(--text-dim,#475569)] hover:bg-[var(--surface,#ffffff)] hover:text-[var(--text,#0f172a)]"
                }`}
              >
                {mode.label}
                {count !== null ? (
                  <span
                    className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                      active
                        ? "bg-white/20 text-white"
                        : "bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]"
                    }`}
                  >
                    {count.toLocaleString()}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Live-match pill (mirrors master-list) ────────────────────────── */}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          <span aria-hidden className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          {meta.total.toLocaleString()} match{meta.total === 1 ? "" : "es"}
        </span>
        {unreadCount > 0 ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--pill-red-text,#b91c1c)]">
            {unreadCount.toLocaleString()} unread on this page
          </span>
        ) : null}
      </div>

      {/* ── Filters card ─────────────────────────────────────────────────── */}
      <div
        className="mb-4 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
        style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
      >
        <div className="mb-4 flex items-center justify-between gap-4">
          <div>
            <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
              Filters
              {activeFilterCount > 0 ? (
                <span className="rounded-full bg-[rgba(99,102,241,0.12)] px-2 py-0.5 text-[10px] font-bold tracking-[0.04em] text-[#4338ca]">
                  {activeFilterCount} ACTIVE
                </span>
              ) : null}
            </p>
            <h3 className="mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              Refine the filing feed
            </h3>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={clearFilters}
              className="rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
            >
              Clear filters
            </button>
            <button
              type="button"
              onClick={markAllRead}
              disabled={markAllPending || items.every((alert) => alert.is_read)}
              className="inline-flex items-center gap-1.5 rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-45"
            >
              <CheckCheck className="h-3.5 w-3.5" strokeWidth={2} />
              {markAllPending ? "Marking…" : "Mark all read"}
            </button>
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,260px)_minmax(0,1fr)_minmax(0,1fr)]">
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Form type
            </label>
            <select
              value={formType}
              onChange={(event) => {
                setFormType(event.target.value);
                setPage(1);
              }}
              className="h-[38px] w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            >
              {FORM_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Priority
            </p>
            <Segmented
              value={priority}
              onChange={(next) => {
                setPriority(next);
                setPage(1);
              }}
              items={PRIORITY_ITEMS}
              ariaLabel="Priority"
            />
          </div>

          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Status
            </p>
            <Segmented
              value={readFilter}
              onChange={(next) => {
                setReadFilter(next as ReadFilter);
                setPage(1);
              }}
              items={STATUS_ITEMS}
              ariaLabel="Read status"
            />
          </div>
        </div>
      </div>

      {error ? (
        <div className="mb-4 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {/* ── List card ────────────────────────────────────────────────────── */}
      <div
        className="mb-4 overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]"
        style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
      >
        <div className="flex items-center justify-between gap-4 border-b border-[var(--border,rgba(30,64,175,0.1))] px-5 py-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
              Workspace
            </p>
            <h3 className="mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              Filing alerts
            </h3>
          </div>
          <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
            {meta.total.toLocaleString()} alert{meta.total === 1 ? "" : "s"}
          </span>
        </div>

        <div className="px-5 py-2">
          {loading ? (
            <AlertsLoadingSkeleton />
          ) : loadError ? (
            <LoadErrorCard
              message={loadError}
              onRetry={() => setReloadKey((k) => k + 1)}
            />
          ) : items.length === 0 ? (
            <EmptyAlertsState />
          ) : (
            <div>
              {items.map((alert) => {
                const priorityKey = resolvePriority(alert.priority);
                return (
                  <div
                    key={alert.id}
                    className={`flex gap-3 border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0 ${
                      alert.is_read ? "opacity-60" : ""
                    }`}
                  >
                    <span
                      aria-hidden
                      className={`mt-2 h-2 w-2 shrink-0 rounded-full ${PRIORITY_DOT_CLASS[priorityKey]}`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="mb-1.5 flex flex-wrap items-center gap-2">
                        <Pill variant={PRIORITY_PILL_VARIANT[priorityKey]}>
                          {PRIORITY_PILL_LABEL[priorityKey]}
                        </Pill>
                        <Pill variant="form">{alert.form_type}</Pill>
                        <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-[var(--text-muted,#94a3b8)]">
                          <span>{formatRelativeTime(alert.filed_at)}</span>
                          <span aria-hidden className="text-[var(--text-dim,#475569)]">·</span>
                          <span>{formatDate(alert.filed_at)}</span>
                        </span>
                      </div>
                      <Link
                        href={`/master-list/${alert.bd_id}`}
                        className="mb-1 block text-[14px] font-semibold text-[var(--text,#0f172a)] transition hover:text-[#6366f1]"
                      >
                        {alert.firm_name}
                      </Link>
                      <p className="text-[13px] leading-5 text-[var(--text-dim,#475569)]">
                        {alert.summary}
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Link
                          href={`/master-list/${alert.bd_id}`}
                          className="inline-flex items-center gap-1 rounded-md border border-[rgba(99,102,241,0.3)] px-2.5 py-1 text-[11px] font-semibold text-[#6366f1] transition hover:bg-[rgba(99,102,241,0.05)]"
                        >
                          Review
                          <ArrowRight className="h-3.5 w-3.5" strokeWidth={2} />
                        </Link>
                        {!alert.is_read ? (
                          <button
                            type="button"
                            onClick={() => void markRead(alert.id)}
                            className="inline-flex items-center gap-1 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
                          >
                            <Check className="h-3.5 w-3.5" strokeWidth={2} />
                            Mark read
                          </button>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-md border border-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-muted,#94a3b8)]">
                            <Check className="h-3.5 w-3.5" strokeWidth={2} />
                            Read
                          </span>
                        )}
                        {alert.source_filing_url ? (
                          <a
                            href={viewableFilingUrl(alert.source_filing_url) ?? alert.source_filing_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
                          >
                            View filing
                          </a>
                        ) : null}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Pagination ───────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">
          Showing {meta.total === 0 ? 0 : (meta.page - 1) * meta.limit + 1}–
          {meta.total === 0 ? 0 : Math.min(meta.page * meta.limit, meta.total)} of{" "}
          {meta.total.toLocaleString()}
        </p>
        <div className="flex flex-wrap gap-1">
          <button
            type="button"
            disabled={meta.page <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            Previous
          </button>
          {pages.map((token, idx) =>
            token === "…" ? (
              <span
                key={`ellipsis-${idx}`}
                className="px-2 py-1.5 text-[12px] text-[var(--text-muted,#94a3b8)]"
              >
                …
              </span>
            ) : (
              <button
                key={token}
                type="button"
                onClick={() => setPage(token)}
                aria-current={meta.page === token ? "page" : undefined}
                className={`min-w-[36px] rounded-[8px] border px-3 py-1.5 text-[12px] font-medium transition ${
                  meta.page === token
                    ? "border-transparent bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)]"
                    : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)]"
                }`}
              >
                {token}
              </button>
            ),
          )}
          <button
            type="button"
            disabled={meta.page >= meta.total_pages}
            onClick={() => setPage((current) => Math.min(meta.total_pages, current + 1))}
            className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

// Inline helper for the initial-fetch failure path. Mirrors the
// ErrorState shape used in /my-favorites' favorite-list-items-pane —
// dashed-border centered card with the error message and a Retry
// button. Single-use, so kept inline rather than extracted.
function LoadErrorCard({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="my-4 rounded-2xl border border-dashed border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[rgba(239,68,68,0.1)] text-[var(--pill-red-text,#b91c1c)]">
        <AlertTriangle className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        Couldn&apos;t load alerts
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        {message}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-5 inline-flex h-[34px] items-center rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-4 text-[13px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface,#ffffff)] hover:text-[var(--text,#0f172a)]"
      >
        Retry
      </button>
    </div>
  );
}
