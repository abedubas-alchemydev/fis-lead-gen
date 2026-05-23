"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useUrlSyncedState } from "@/lib/use-url-synced-state";

import { ArrowDown, ArrowUp, ChevronDown, Heart, Search, X } from "lucide-react";

import { apiRequest, buildApiPath, getInvestmentAdvisorLatest13fPath } from "@/lib/api";
import { formatCurrency, formatDate, formatRelativeTime } from "@/lib/format";
import {
  ADVISOR_LIST_STATE_DEFAULTS,
  type AdvisorListQueryState,
  type AdvisorScopeFilter,
  type AdvisorStatusFilter,
  buildAdvisorListUrl,
  clearAllFilters,
  encodeReturnParam,
  fromSearchParams,
  hasActiveFilters,
} from "@/lib/advisor-list-state";
import { STATE_NAMES } from "@/lib/states";
import { AdvisoryActivitiesFilter } from "@/components/advisor-list/filters/advisory-activities-filter";
import { ClientTypesFilter } from "@/components/advisor-list/filters/client-types-filter";
import { RegulatoryAumRangeFilter } from "@/components/advisor-list/filters/regulatory-aum-range-filter";
// Cross-module reuse: the date-range picker is generic and the BD master
// list owns it today. Future cleanup: move shared range/date filters to
// components/ui/filters/ so neither side imports across module boundaries.
import { RegistrationDateRangeFilter } from "@/components/master-list/filters/registration-date-range-filter";
import { BulkListPicker } from "@/components/list-picker/bulk-list-picker";
import { ListPicker } from "@/components/list-picker/list-picker";
import { Combo } from "@/components/ui/combo";
import { Pill } from "@/components/ui/pill";
import { agencyLabel } from "@/components/master-list/detail/clearing-membership-helpers";
import { Segmented, type SegmentedItem } from "@/components/ui/segmented";
import { Tag } from "@/components/ui/tag";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import type {
  InvestmentAdvisorListItem,
  InvestmentAdvisorListResponse,
} from "@/lib/types";

// ── Column catalog ────────────────────────────────────────────────────────
const COLUMNS = [
  { key: "name", label: "Firm Name" },
  { key: "crd_number", label: "CRD" },
  { key: "state", label: "State" },
  { key: "regulatory_aum", label: "Regulatory AUM" },
  { key: "total_clients", label: "Clients" },
  { key: "files_13f", label: "13F" },
  { key: "memberships", label: "Reputable Dispute Memberships" },
  { key: "last_filing_date", label: "Last Filing" },
] as const;

// Display-only column with no backend sort key — header renders a plain
// label (no sort button). The "Sort by" dropdown is driven by SORT_OPTIONS,
// not COLUMNS, so it already excludes this.
const NON_SORTABLE_KEYS = new Set<string>(["memberships"]);

// Status + 13F-scope segmented catalogs — module-level so the arrays
// stay referentially stable across renders. Mirror the master-list
// HEALTH_ITEMS / PRIORITY_ITEMS row.
const STATUS_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "All", label: "All" },
  { value: "active", label: "Active", dot: "healthy" },
  { value: "withdrawn", label: "Withdrawn", dot: "risk" },
];

const THIRTEEN_F_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "13F", label: "13F filers only" },
  { value: "all", label: "All advisors" },
];

// Sort options shown in the toolbar Combo. Backed by the BE-recognized
// sort_by keys in /api/v1/investment-advisors. Drops "state" (low signal)
// and "files_13f" (boolean; the 13F-only filter already pins the page).
const SORT_OPTIONS = [
  { key: "name", label: "Firm Name" },
  { key: "cik", label: "CIK" },
  { key: "crd_number", label: "CRD" },
  { key: "regulatory_aum", label: "Regulatory AUM" },
  { key: "discretionary_aum", label: "Discretionary AUM" },
  { key: "total_clients", label: "Clients" },
  { key: "registration_date", label: "Registration Date" },
  { key: "last_filing_date", label: "Last Filing" },
  { key: "latest_13f_filing_date", label: "Latest 13F" },
  { key: "status", label: "Status" },
] as const;

// ── Pagination helper ─────────────────────────────────────────────────────
// Mirrors the BD master-list helper. Produces [1, 2, 3, …, last] with
// ellipses as string literals so React can key them distinctly.
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

// ── Active-filter accounting ──────────────────────────────────────────────
// The filters-card eyebrow shows "N ACTIVE" using this count. Includes
// `search` because the search box is visually part of the filters area on
// the BD page (toolbar mirror), but a typed-but-not-yet-submitted query
// in the topbar only counts after the user hits Enter — we read from
// state, which is URL-derived.
function countActiveFilters(state: AdvisorListQueryState): number {
  let n = 0;
  if (state.search !== ADVISOR_LIST_STATE_DEFAULTS.search) n += 1;
  if (state.state !== ADVISOR_LIST_STATE_DEFAULTS.state) n += 1;
  if (state.status !== ADVISOR_LIST_STATE_DEFAULTS.status) n += 1;
  if (state.filesThirteenF !== ADVISOR_LIST_STATE_DEFAULTS.filesThirteenF) n += 1;
  if (state.advisoryActivities.length > 0) n += 1;
  if (state.clientTypes.length > 0) n += 1;
  if (state.minRegulatoryAum !== null || state.maxRegulatoryAum !== null) n += 1;
  if (state.registeredAfter !== null || state.registeredBefore !== null) n += 1;
  return n;
}

export function AdvisorListWorkspaceClient() {
  // URL is the canonical store, but a synchronous local mirror is the source
  // of truth for rendering + merging edits — router.replace is async, so
  // reading filter state straight off the URL drops rapid successive edits
  // (ticking several multi-select checkboxes would keep only the last). See
  // useUrlSyncedState.
  const { state, updateState, replaceState } = useUrlSyncedState(
    fromSearchParams,
    buildAdvisorListUrl,
  );
  const [searchInput, setSearchInput] = useState(state.search);

  // Mirror the URL search-input into local state so the user can type
  // freely without re-fetching on every keystroke. Sync back on URL
  // change (e.g. clearAllFilters).
  useEffect(() => {
    setSearchInput(state.search);
  }, [state.search]);

  const [data, setData] = useState<InvestmentAdvisorListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Bulk-select state (page-scoped, ephemeral). Mirrors the master-list
  // pattern: URL-backing would loop because every URL change refetches
  // items, which then clears the set. Scope is "the current page" — the
  // set resets on items change.
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<number>>(
    () => new Set(),
  );
  const [bulkPickerOpen, setBulkPickerOpen] = useState(false);
  const headerCheckboxRef = useRef<HTMLInputElement | null>(null);
  const bulkActionTriggerRef = useRef<HTMLButtonElement | null>(null);

  const toggleSort = useCallback(
    (key: string) => {
      if (state.sortBy === key) {
        updateState({
          sortDir: state.sortDir === "desc" ? "asc" : "desc",
          page: 1,
        });
      } else {
        updateState({ sortBy: key, sortDir: "asc", page: 1 });
      }
    },
    [state.sortBy, state.sortDir, updateState],
  );

  // Fetch the list whenever URL state changes. State changes always
  // route through router.push, which re-renders this component with a
  // fresh searchParams — cheap to re-fetch since the BE response is
  // already paginated to ≤100 rows.
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    const params: Record<string, string | number | string[]> = {
      sort_by: state.sortBy,
      sort_dir: state.sortDir,
      page: state.page,
      limit: state.limit,
    };
    if (state.search) params.q = state.search;
    if (state.state) params.state = [state.state];
    if (state.status !== "All") params.status = [state.status];
    // BE defaults files_13f=true, so we omit the param when scope is "13F"
    // and only send the explicit "false" override when broadening to all.
    if (state.filesThirteenF === "all") params.files_13f = "false";
    if (state.advisoryActivities.length > 0) {
      params.advisory_activities = state.advisoryActivities;
    }
    if (state.clientTypes.length > 0) {
      params.client_types = state.clientTypes;
    }
    if (state.minRegulatoryAum !== null) {
      params.min_regulatory_aum = state.minRegulatoryAum;
    }
    if (state.maxRegulatoryAum !== null) {
      params.max_regulatory_aum = state.maxRegulatoryAum;
    }
    if (state.registeredAfter !== null) {
      params.registered_after = state.registeredAfter;
    }
    if (state.registeredBefore !== null) {
      params.registered_before = state.registeredBefore;
    }

    apiRequest<InvestmentAdvisorListResponse>(
      buildApiPath("/api/v1/investment-advisors", params),
      { signal: controller.signal },
    )
      .then((response) => {
        setData(response);
        setLoading(false);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Failed to load advisors");
        setLoading(false);
      });

    return () => controller.abort();
  }, [
    state.search,
    state.state,
    state.status,
    state.filesThirteenF,
    state.advisoryActivities,
    state.clientTypes,
    state.minRegulatoryAum,
    state.maxRegulatoryAum,
    state.registeredAfter,
    state.registeredBefore,
    state.sortBy,
    state.sortDir,
    state.page,
    state.limit,
  ]);

  // Memoize so identity is stable across renders where `data` didn't
  // change — the bulk-selection effect depends on items, and a fresh
  // [] each render would clear the user's selection unexpectedly.
  const items = useMemo(() => data?.items ?? [], [data]);
  const meta = data?.meta;
  const filtersActive = hasActiveFilters(state);
  const activeFilterCount = countActiveFilters(state);

  // Selection scope = current page. Every items refetch (filter, sort,
  // pagination) clears the set so a bulk action never operates on off-
  // page rows the user can't see.
  useEffect(() => {
    setSelectedIds(new Set());
  }, [items]);

  // Auto-close the bulk picker when selection drops to zero (Clear,
  // refetch, etc.).
  useEffect(() => {
    if (selectedIds.size === 0) setBulkPickerOpen(false);
  }, [selectedIds]);

  const allOnPageSelected =
    items.length > 0 && selectedIds.size === items.length;
  const someOnPageSelected =
    selectedIds.size > 0 && selectedIds.size < items.length;

  // <input> doesn't have an indeterminate prop — it has to be set
  // imperatively on the DOM node. The checked=true + indeterminate=true
  // combination reads as "indeterminate" in the browser.
  useEffect(() => {
    if (headerCheckboxRef.current) {
      headerCheckboxRef.current.indeterminate = someOnPageSelected;
    }
  }, [someOnPageSelected]);

  const toggleRow = useCallback((id: number) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAllOnPage = useCallback(() => {
    setSelectedIds((current) => {
      if (current.size === items.length) return new Set();
      return new Set(items.map((item) => item.id));
    });
  }, [items]);

  function handleClearFilters() {
    setSearchInput("");
    replaceState(clearAllFilters(state));
  }

  // Encoded return-URL appended to every detail link so the detail page
  // can rebuild the same filtered/sorted list state.
  const returnEnvelope = useMemo(() => encodeReturnParam(state), [state]);
  const detailHref = useCallback(
    (id: number): Route => {
      const base = `/advisor-list/${id}`;
      return (returnEnvelope ? `${base}?return=${returnEnvelope}` : base) as Route;
    },
    [returnEnvelope],
  );

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* ── Topbar: breadcrumb + h1 + right rail ─────────────────────────── */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Investment
            Advisors
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Investment Advisors
          </h1>
          {/* Data-source provenance line — kept from the original page so
              users opening this for the first time understand the scope.
              The BD master list has no analog because the source there is
              implicit. */}
          <p className="mt-1 max-w-[640px] text-[12px] text-[var(--text-muted,#94a3b8)]">
            SEC-registered Investment Advisers that file Form 13F. Sourced from
            IAPD; AUM and advisory activities from the latest Form ADV.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2.5">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              updateState({ search: searchInput.trim(), page: 1 });
            }}
            className="hidden w-[320px] items-center gap-2.5 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3.5 py-2 text-[var(--text-dim,#475569)] transition focus-within:border-[var(--accent,#6366f1)] focus-within:shadow-[0_0_0_3px_rgba(99,102,241,0.15)] md:flex"
          >
            <Search className="h-4 w-4 shrink-0" strokeWidth={2} />
            <input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Search firm name, CIK, or CRD…"
              aria-label="Search investment advisors"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] outline-none placeholder:text-[var(--text-muted,#94a3b8)]"
            />
            <kbd className="rounded-[4px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-3,#dbeafe)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-dim,#475569)]">
              ⌘K
            </kbd>
          </form>
          <ThemeToggle />
        </div>
      </div>

      {/* ── Meta row: refresh stamp + live-match pill ───────────────────── */}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
        {meta?.pipeline_refreshed_at ? (
          <span>
            Pipeline refreshed {formatRelativeTime(meta.pipeline_refreshed_at)}
          </span>
        ) : null}
        {meta ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
            <span aria-hidden className="relative flex h-2 w-2">
              <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
              <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
            </span>
            {meta.total.toLocaleString()} firm{meta.total === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>

      {/* ── Filters card ─────────────────────────────────────────────────── */}
      <div
        className="mb-4 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
        style={{
          boxShadow:
            "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
        }}
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
              Refine the workspace
            </h3>
          </div>
          {filtersActive ? (
            <button
              type="button"
              onClick={handleClearFilters}
              className="inline-flex items-center gap-1.5 rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
            >
              <X aria-hidden className="h-3.5 w-3.5" strokeWidth={2} />
              Clear filters
            </button>
          ) : null}
        </div>

        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              State
            </label>
            <Combo
              value={state.state}
              onChange={(next) => updateState({ state: next, page: 1 })}
              options={STATE_NAMES}
              placeholder="Search states…"
              emptyLabel="All states"
              ariaLabel="State"
            />
          </div>

          <AdvisoryActivitiesFilter
            value={state.advisoryActivities}
            onChange={(next) =>
              updateState({ advisoryActivities: next, page: 1 })
            }
          />

          <ClientTypesFilter
            value={state.clientTypes}
            onChange={(next) => updateState({ clientTypes: next, page: 1 })}
          />
        </div>

        {/* Segmented row — mirrors the master-list Health / Priority pair.
            Status uses the BE's `status` filter; 13F Scope flips the
            hard-coded files_13f=true scope. Both default to "no filter
            applied" so the page lands identically to today's behavior. */}
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Status
            </p>
            <Segmented
              value={state.status}
              onChange={(next) =>
                updateState({
                  status: next as AdvisorStatusFilter,
                  page: 1,
                })
              }
              items={STATUS_ITEMS}
              ariaLabel="Advisor status"
            />
          </div>
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Scope
            </p>
            <Segmented
              value={state.filesThirteenF}
              onChange={(next) =>
                updateState({
                  filesThirteenF: next as AdvisorScopeFilter,
                  page: 1,
                })
              }
              items={THIRTEEN_F_ITEMS}
              ariaLabel="Advisor scope"
            />
          </div>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <RegulatoryAumRangeFilter
            min={state.minRegulatoryAum}
            max={state.maxRegulatoryAum}
            onChange={(patch) =>
              updateState({
                ...(patch.min !== undefined
                  ? { minRegulatoryAum: patch.min }
                  : {}),
                ...(patch.max !== undefined
                  ? { maxRegulatoryAum: patch.max }
                  : {}),
                page: 1,
              })
            }
          />

          <RegistrationDateRangeFilter
            registeredAfter={state.registeredAfter}
            registeredBefore={state.registeredBefore}
            onChange={(patch) =>
              updateState({
                ...(patch.registeredAfter !== undefined
                  ? { registeredAfter: patch.registeredAfter }
                  : {}),
                ...(patch.registeredBefore !== undefined
                  ? { registeredBefore: patch.registeredBefore }
                  : {}),
                page: 1,
              })
            }
          />
        </div>

        {/* Active-filter tags strip — one dismissible chip per applied
            filter. Mirrors the master-list pattern so users can drop a
            single filter without diving back into the dropdown. */}
        {activeFilterCount > 0 ? (
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-dashed border-[var(--border,rgba(30,64,175,0.1))] pt-3">
            <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Active
            </span>
            {state.search !== "" ? (
              <Tag
                onDismiss={() => {
                  setSearchInput("");
                  updateState({ search: "", page: 1 });
                }}
              >
                Search: {state.search}
              </Tag>
            ) : null}
            {state.state !== "" ? (
              <Tag onDismiss={() => updateState({ state: "", page: 1 })}>
                State: {state.state}
              </Tag>
            ) : null}
            {state.status !== "All" ? (
              <Tag onDismiss={() => updateState({ status: "All", page: 1 })}>
                Status: {state.status === "active" ? "Active" : "Withdrawn"}
              </Tag>
            ) : null}
            {state.filesThirteenF !== "13F" ? (
              <Tag
                onDismiss={() =>
                  updateState({ filesThirteenF: "13F", page: 1 })
                }
              >
                Scope: All advisors
              </Tag>
            ) : null}
            {state.advisoryActivities.map((activity) => (
              <Tag
                key={`activity-${activity}`}
                onDismiss={() =>
                  updateState({
                    advisoryActivities: state.advisoryActivities.filter(
                      (value) => value !== activity,
                    ),
                    page: 1,
                  })
                }
              >
                Activity: {activity}
              </Tag>
            ))}
            {state.clientTypes.map((clientType) => (
              <Tag
                key={`ctype-${clientType}`}
                onDismiss={() =>
                  updateState({
                    clientTypes: state.clientTypes.filter(
                      (value) => value !== clientType,
                    ),
                    page: 1,
                  })
                }
              >
                Client type: {clientType}
              </Tag>
            ))}
            {state.minRegulatoryAum !== null ? (
              <Tag
                onDismiss={() =>
                  updateState({ minRegulatoryAum: null, page: 1 })
                }
              >
                AUM ≥ {formatCurrency(state.minRegulatoryAum)}
              </Tag>
            ) : null}
            {state.maxRegulatoryAum !== null ? (
              <Tag
                onDismiss={() =>
                  updateState({ maxRegulatoryAum: null, page: 1 })
                }
              >
                AUM ≤ {formatCurrency(state.maxRegulatoryAum)}
              </Tag>
            ) : null}
            {state.registeredAfter !== null ? (
              <Tag
                onDismiss={() =>
                  updateState({ registeredAfter: null, page: 1 })
                }
              >
                Registered after {state.registeredAfter}
              </Tag>
            ) : null}
            {state.registeredBefore !== null ? (
              <Tag
                onDismiss={() =>
                  updateState({ registeredBefore: null, page: 1 })
                }
              >
                Registered before {state.registeredBefore}
              </Tag>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* ── Toolbar card (search + sort + direction + page-size) ─────────── */}
      <div
        className="mb-4 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
        style={{
          boxShadow:
            "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
        }}
      >
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto_auto_auto]">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              updateState({ search: searchInput.trim(), page: 1 });
            }}
            className="min-w-0"
          >
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Search firms
            </label>
            <div className="flex h-[38px] w-full items-center gap-2.5 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 transition focus-within:border-[var(--accent,#6366f1)] focus-within:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]">
              <Search
                className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
                strokeWidth={2}
              />
              <input
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="Firm name, CIK, or CRD"
                className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] outline-none placeholder:text-[var(--text-muted,#94a3b8)]"
              />
            </div>
          </form>

          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Sort by
            </label>
            <select
              value={state.sortBy}
              onChange={(event) =>
                updateState({ sortBy: event.target.value, page: 1 })
              }
              className="h-[38px] rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Direction
            </label>
            <select
              value={state.sortDir}
              onChange={(event) =>
                updateState({
                  sortDir: event.target.value as "asc" | "desc",
                  page: 1,
                })
              }
              className="h-[38px] rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            >
              <option value="asc">Ascending</option>
              <option value="desc">Descending</option>
            </select>
          </div>

          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Page size
            </label>
            <select
              value={state.limit}
              onChange={(event) =>
                updateState({ limit: Number(event.target.value), page: 1 })
              }
              className="h-[38px] rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            >
              {[25, 50, 100].map((pageSize) => (
                <option key={pageSize} value={pageSize}>
                  {pageSize}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {error ? (
        <div className="mb-4 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
          Couldn&apos;t load advisors: {error}
        </div>
      ) : null}

      {/* ── Table card ───────────────────────────────────────────────────── */}
      <div
        className="mb-4 overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]"
        style={{
          boxShadow:
            "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
        }}
      >
        <div className="flex items-center justify-between gap-4 border-b border-[var(--border,rgba(30,64,175,0.1))] px-5 py-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
              Workspace
            </p>
            <h3 className="mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              Investment-advisor list
            </h3>
          </div>
          {selectedIds.size > 0 ? (
            <div className="flex items-center gap-3">
              <span
                className="text-[12px] font-semibold text-[var(--text-dim,#475569)]"
                aria-live="polite"
              >
                {selectedIds.size} selected
              </span>
              <button
                type="button"
                onClick={() => setSelectedIds(new Set())}
                className="rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
              >
                Clear
              </button>
              <button
                ref={bulkActionTriggerRef}
                type="button"
                onClick={() => setBulkPickerOpen((v) => !v)}
                aria-haspopup="dialog"
                aria-expanded={bulkPickerOpen}
                className="inline-flex items-center gap-1.5 rounded-[8px] border border-[rgba(99,102,241,0.4)] bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] px-3 py-1.5 text-[12px] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)]"
              >
                <Heart className="h-3.5 w-3.5" strokeWidth={2.5} aria-hidden />
                Save to list
                <ChevronDown
                  className="h-3.5 w-3.5"
                  strokeWidth={2.5}
                  aria-hidden
                />
              </button>
              {bulkPickerOpen ? (
                <BulkListPicker
                  selectedIds={Array.from(selectedIds)}
                  entityType="advisor"
                  anchorRef={bulkActionTriggerRef}
                  onAdded={() => {
                    setBulkPickerOpen(false);
                    setSelectedIds(new Set());
                  }}
                  onDismiss={() => setBulkPickerOpen(false)}
                />
              ) : null}
            </div>
          ) : meta ? (
            <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
              {meta.total.toLocaleString()} firm{meta.total === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1000px] text-left">
            <thead>
              <tr>
                <th
                  scope="col"
                  className="w-[44px] whitespace-nowrap border-b border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-5 py-3"
                >
                  <input
                    ref={headerCheckboxRef}
                    type="checkbox"
                    aria-label={
                      allOnPageSelected
                        ? "Deselect all advisors on this page"
                        : "Select all advisors on this page"
                    }
                    checked={allOnPageSelected}
                    onChange={toggleAllOnPage}
                    disabled={loading || items.length === 0}
                    className="h-4 w-4 rounded border-[var(--border-2,rgba(30,64,175,0.16))] text-[var(--accent,#6366f1)] focus:ring-[var(--accent,#6366f1)]"
                  />
                </th>
                {COLUMNS.map((column) => {
                  const isSorted = state.sortBy === column.key;
                  if (NON_SORTABLE_KEYS.has(column.key)) {
                    return (
                      <th
                        key={column.key}
                        className="whitespace-nowrap border-b border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]"
                      >
                        {column.label}
                      </th>
                    );
                  }
                  return (
                    <th
                      key={column.key}
                      className="whitespace-nowrap border-b border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]"
                    >
                      <button
                        type="button"
                        onClick={() => toggleSort(column.key)}
                        className="inline-flex items-center gap-1 transition hover:text-[var(--text,#0f172a)]"
                      >
                        {column.label}
                        {isSorted ? (
                          state.sortDir === "asc" ? (
                            <ArrowUp className="h-3 w-3" strokeWidth={2} />
                          ) : (
                            <ArrowDown className="h-3 w-3" strokeWidth={2} />
                          )
                        ) : null}
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody className="text-[13px] text-[var(--text,#0f172a)]">
              {loading && items.length === 0 ? (
                Array.from({ length: Math.min(state.limit, 8) }).map(
                  (_, index) => (
                    <tr
                      key={`loading-${index}`}
                      className="border-t border-[var(--border,rgba(30,64,175,0.1))]"
                    >
                      <td className="px-5 py-3.5">
                        <div className="h-4 w-4 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                      </td>
                      {COLUMNS.map((column) => (
                        <td key={column.key} className="px-5 py-3.5">
                          <div className="h-4 w-full animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                        </td>
                      ))}
                    </tr>
                  ),
                )
              ) : items.length === 0 ? (
                <tr>
                  <td
                    colSpan={COLUMNS.length + 1}
                    className="px-5 py-12 text-center text-sm text-[var(--text-muted,#94a3b8)]"
                  >
                    No advisors match the current filters.
                  </td>
                </tr>
              ) : (
                items.map((row) => (
                  <AdvisorRow
                    key={row.id}
                    row={row}
                    href={detailHref(row.id)}
                    selected={selectedIds.has(row.id)}
                    onToggleSelect={() => toggleRow(row.id)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Pagination ───────────────────────────────────────────────────── */}
      {meta && meta.total > 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">
            Showing{" "}
            {meta.total === 0 ? 0 : (meta.page - 1) * meta.limit + 1}
            –
            {meta.total === 0
              ? 0
              : Math.min(meta.page * meta.limit, meta.total)}{" "}
            of {meta.total.toLocaleString()}
          </p>
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              disabled={meta.page <= 1}
              onClick={() =>
                updateState({ page: Math.max(1, meta.page - 1) })
              }
              className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
            >
              Previous
            </button>
            {paginationPages(meta.page, meta.total_pages).map((token, idx) =>
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
                  onClick={() => updateState({ page: token })}
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
              onClick={() =>
                updateState({ page: Math.min(meta.total_pages, meta.page + 1) })
              }
              className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
            >
              Next
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function AdvisorRow({
  row,
  href,
  selected,
  onToggleSelect,
}: {
  row: InvestmentAdvisorListItem;
  href: Route;
  selected: boolean;
  onToggleSelect: () => void;
}) {
  const location = [row.city, row.state].filter(Boolean).join(", ");

  return (
    <tr className="border-t border-[var(--border,rgba(30,64,175,0.1))] align-top transition hover:bg-[var(--row-hover,rgba(99,102,241,0.04))]">
      <td className="px-5 py-3.5">
        <input
          type="checkbox"
          aria-label={`${selected ? "Deselect" : "Select"} ${row.name}`}
          checked={selected}
          onChange={onToggleSelect}
          className="h-4 w-4 rounded border-[var(--border-2,rgba(30,64,175,0.16))] text-[var(--accent,#6366f1)] focus:ring-[var(--accent,#6366f1)]"
        />
      </td>
      <td className="min-w-[220px] px-5 py-3.5">
        <div className="flex items-start justify-between gap-2">
          <Link
            href={href}
            className="block font-semibold text-[var(--text,#0f172a)] transition hover:text-[var(--accent,#6366f1)]"
          >
            {row.name}
          </Link>
          <ListPicker variant="row" entityType="advisor" firmId={row.id} />
        </div>
        {location ? (
          <div className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
            {location}
          </div>
        ) : null}
      </td>
      <td className="px-5 py-3.5 font-mono text-[12px] text-[var(--text-dim,#475569)]">
        {row.crd_number ?? "—"}
      </td>
      <td className="px-5 py-3.5 text-[var(--text-dim,#475569)]">
        {row.state ?? "—"}
      </td>
      <td className="px-5 py-3.5 font-semibold tabular-nums text-[var(--text,#0f172a)]">
        {formatCurrency(row.regulatory_aum)}
      </td>
      <td className="px-5 py-3.5 tabular-nums text-[var(--text-dim,#475569)]">
        {row.total_clients?.toLocaleString() ?? "—"}
      </td>
      <td className="px-5 py-3.5">
        {row.files_13f && row.latest_13f_filing_date ? (
          <a
            href={getInvestmentAdvisorLatest13fPath(row.id)}
            target="_blank"
            rel="noopener noreferrer"
            title={`Open latest 13F-HR (filed ${formatDate(row.latest_13f_filing_date)}) on SEC EDGAR`}
            onClick={(e) => e.stopPropagation()}
            className="inline-flex flex-col items-start gap-0.5 transition hover:opacity-80"
          >
            <Pill variant="healthy">13F</Pill>
            <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
              {formatDate(row.latest_13f_filing_date)}
            </span>
          </a>
        ) : row.files_13f ? (
          <Pill variant="healthy">13F</Pill>
        ) : (
          <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">—</span>
        )}
      </td>
      <td className="px-5 py-3.5">
        {row.member_agencies.length > 0 ? (
          <span className="inline-flex flex-wrap items-center gap-1">
            {row.member_agencies.map((code) => (
              <Pill key={code} variant="member">
                {agencyLabel(code)}
              </Pill>
            ))}
          </span>
        ) : row.clearing_membership_checked_at !== null ? (
          <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
            Not a member
          </span>
        ) : (
          <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">—</span>
        )}
      </td>
      <td className="px-5 py-3.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
        {formatDate(row.last_filing_date)}
      </td>
    </tr>
  );
}
