"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useUrlSyncedState } from "@/lib/use-url-synced-state";

import { ArrowDown, ArrowUp, Search, X } from "lucide-react";

import { fetchInstitutionalInvestors } from "@/lib/api";
import { formatCurrency, formatDate, formatRelativeTime } from "@/lib/format";
import {
  INSTITUTIONAL_INVESTORS_STATE_DEFAULTS,
  type InstitutionalInvestorsQueryState,
  buildInstitutionalInvestorsUrl,
  clearAllFilters,
  fromSearchParams,
  hasActiveFilters,
} from "@/lib/institutional-investors-state";
import { STATE_NAMES, stateCodeFromName } from "@/lib/states";
import { Combo } from "@/components/ui/combo";
import { Pill } from "@/components/ui/pill";
import { Segmented, type SegmentedItem } from "@/components/ui/segmented";
import { Tag } from "@/components/ui/tag";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { TotalAumRangeFilter } from "@/components/institutional-investors/filters/total-aum-range-filter";
import { Filed13fDateRangeFilter } from "@/components/institutional-investors/filters/filed-13f-date-range-filter";
import type {
  InstitutionalInvestorListItem,
  InstitutionalInvestorListResponse,
} from "@/lib/types";

// ── Column catalog ────────────────────────────────────────────────────────
// `key` doubles as both the table header id and the BE sort_by value where
// the column is sortable. Keys not in ALLOWED_SORT_FIELDS on the BE
// (services/institutional_investors.py) belong in NON_SORTABLE_KEYS so the
// header renders a plain label instead of a sort button.
const COLUMNS = [
  { key: "name", label: "Name" },
  { key: "cik", label: "CIK" },
  { key: "state", label: "Location" },
  { key: "total_aum", label: "Total AUM", align: "right" as const },
  { key: "latest_13f_filing_date", label: "Latest 13F" },
  { key: "advisor_id", label: "Also RIA" },
] as const;

// `advisor_id` is presented as the "Also RIA" badge column — sorting on
// it would order by FK id (meaningless). The Also-RIA filter pins the
// slice instead.
const NON_SORTABLE_KEYS = new Set<string>(["advisor_id"]);

const ADVISOR_LINK_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "All", label: "All" },
  { value: "RIA", label: "Also RIA", dot: "healthy" },
];

// Sort options shown in the toolbar Combo. Backed by the BE-recognized
// keys in ALLOWED_SORT_FIELDS (services/institutional_investors.py:30).
// `status` is omitted while every row is "pending" — it would group the
// entire list under one bucket. Re-add once enrichment populates other
// status values.
const SORT_OPTIONS = [
  { key: "total_aum", label: "Total AUM" },
  { key: "name", label: "Name" },
  { key: "cik", label: "CIK" },
  { key: "state", label: "State" },
  { key: "holdings_count", label: "Holdings count" },
  { key: "latest_13f_filing_date", label: "Latest 13F" },
] as const;

// ── Pagination helper ─────────────────────────────────────────────────────
// Mirrors advisor-list's helper. Produces [1, 2, 3, …, last] with ellipses
// as string literals so React can key them distinctly.
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
// The filters-card eyebrow shows "N ACTIVE" using this count.
function countActiveFilters(state: InstitutionalInvestorsQueryState): number {
  let n = 0;
  if (state.search !== INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.search) n += 1;
  if (state.states.length > 0) n += 1;
  if (
    state.onlyWithAdvisor !==
    INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.onlyWithAdvisor
  )
    n += 1;
  if (state.minTotalAum !== null || state.maxTotalAum !== null) n += 1;
  if (state.filed13fAfter !== null || state.filed13fBefore !== null) n += 1;
  return n;
}

// State persistence stores full names ("New York") for display in the
// Combo, but the BE column holds 2-letter codes ("NY") sourced from
// EDGAR 13F-HR filings. Convert at the request boundary, falling through
// for already-2-letter input so a hand-edited URL keeps working.
function stateNameToBeCode(name: string): string {
  return stateCodeFromName(name) ?? name;
}

export function InstitutionalInvestorsWorkspaceClient() {
  const { state, updateState, replaceState } = useUrlSyncedState(
    fromSearchParams,
    buildInstitutionalInvestorsUrl,
  );
  const [searchInput, setSearchInput] = useState(state.search);

  useEffect(() => {
    setSearchInput(state.search);
  }, [state.search]);

  const [data, setData] = useState<InstitutionalInvestorListResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  // Fetch the list whenever URL state changes. The Combo emits full
  // state names; convert to 2-letter codes here so the BE's
  // `Investor.state.in_([...])` filter matches.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const states = state.states.map(stateNameToBeCode);

    fetchInstitutionalInvestors({
      q: state.search || undefined,
      state: states.length > 0 ? states : undefined,
      min_total_aum: state.minTotalAum ?? undefined,
      max_total_aum: state.maxTotalAum ?? undefined,
      filed_13f_after: state.filed13fAfter ?? undefined,
      filed_13f_before: state.filed13fBefore ?? undefined,
      only_with_advisor_link: state.onlyWithAdvisor ? true : undefined,
      sort_by: state.sortBy,
      sort_dir: state.sortDir,
      page: state.page,
      limit: state.limit,
    })
      .then((response) => {
        if (cancelled) return;
        setData(response);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to load investors",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [
    state.search,
    state.states,
    state.onlyWithAdvisor,
    state.minTotalAum,
    state.maxTotalAum,
    state.filed13fAfter,
    state.filed13fBefore,
    state.sortBy,
    state.sortDir,
    state.page,
    state.limit,
  ]);

  const items = useMemo(() => data?.items ?? [], [data]);
  const meta = data?.meta;
  const filtersActive = hasActiveFilters(state);
  const activeFilterCount = countActiveFilters(state);

  // The Combo is single-select but our state shape is `states: string[]`
  // so the URL stays forward-compatible with a future multi-state UI.
  // Adapt at the edges: pick element[0] for the value, wrap on change.
  const comboState = state.states[0] ?? "";

  function handleClearFilters() {
    setSearchInput("");
    replaceState(clearAllFilters(state));
  }

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* ── Topbar: breadcrumb + h1 + right rail ─────────────────────────── */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            Institutional Investors
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Institutional Investors
          </h1>
          <p className="mt-1 max-w-[640px] text-[12px] text-[var(--text-muted,#94a3b8)]">
            13F-HR filers — institutional investment managers with $100M+ in
            qualified securities. Sourced from SEC EDGAR; rows that also
            appear as registered RIAs are flagged.
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
              placeholder="Search firm name or CIK…"
              aria-label="Search institutional investors"
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
            {meta.total.toLocaleString()} filer{meta.total === 1 ? "" : "s"}
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

        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              State
            </label>
            <Combo
              value={comboState}
              onChange={(next) =>
                updateState({ states: next ? [next] : [], page: 1 })
              }
              options={STATE_NAMES}
              placeholder="Search states…"
              emptyLabel="All states"
              ariaLabel="State"
            />
          </div>
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              RIA crossover
            </p>
            <Segmented
              value={state.onlyWithAdvisor ? "RIA" : "All"}
              onChange={(next) =>
                updateState({ onlyWithAdvisor: next === "RIA", page: 1 })
              }
              items={ADVISOR_LINK_ITEMS}
              ariaLabel="Filter to 13F filers that are also registered RIAs"
            />
          </div>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <TotalAumRangeFilter
            min={state.minTotalAum}
            max={state.maxTotalAum}
            onChange={(patch) =>
              updateState({
                ...(patch.min !== undefined ? { minTotalAum: patch.min } : {}),
                ...(patch.max !== undefined ? { maxTotalAum: patch.max } : {}),
                page: 1,
              })
            }
          />

          <Filed13fDateRangeFilter
            filed13fAfter={state.filed13fAfter}
            filed13fBefore={state.filed13fBefore}
            onChange={(patch) =>
              updateState({
                ...(patch.filed13fAfter !== undefined
                  ? { filed13fAfter: patch.filed13fAfter }
                  : {}),
                ...(patch.filed13fBefore !== undefined
                  ? { filed13fBefore: patch.filed13fBefore }
                  : {}),
                page: 1,
              })
            }
          />
        </div>

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
            {state.states.map((value) => (
              <Tag
                key={`state-${value}`}
                onDismiss={() =>
                  updateState({
                    states: state.states.filter((v) => v !== value),
                    page: 1,
                  })
                }
              >
                State: {value}
              </Tag>
            ))}
            {state.onlyWithAdvisor ? (
              <Tag
                onDismiss={() =>
                  updateState({ onlyWithAdvisor: false, page: 1 })
                }
              >
                Also RIA
              </Tag>
            ) : null}
            {state.minTotalAum !== null ? (
              <Tag
                onDismiss={() => updateState({ minTotalAum: null, page: 1 })}
              >
                AUM ≥ {formatCurrency(state.minTotalAum)}
              </Tag>
            ) : null}
            {state.maxTotalAum !== null ? (
              <Tag
                onDismiss={() => updateState({ maxTotalAum: null, page: 1 })}
              >
                AUM ≤ {formatCurrency(state.maxTotalAum)}
              </Tag>
            ) : null}
            {state.filed13fAfter !== null ? (
              <Tag
                onDismiss={() => updateState({ filed13fAfter: null, page: 1 })}
              >
                Filed after {state.filed13fAfter}
              </Tag>
            ) : null}
            {state.filed13fBefore !== null ? (
              <Tag
                onDismiss={() =>
                  updateState({ filed13fBefore: null, page: 1 })
                }
              >
                Filed before {state.filed13fBefore}
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
              Search filers
            </label>
            <div className="flex h-[38px] w-full items-center gap-2.5 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 transition focus-within:border-[var(--accent,#6366f1)] focus-within:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]">
              <Search
                className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
                strokeWidth={2}
              />
              <input
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="Firm name or CIK"
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
          Couldn&apos;t load institutional investors: {error}
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
              Institutional-investor list
            </h3>
          </div>
          {meta ? (
            <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
              {meta.total.toLocaleString()} filer{meta.total === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-left">
            <thead>
              <tr>
                {COLUMNS.map((column) => {
                  const isSorted = state.sortBy === column.key;
                  const align =
                    "align" in column && column.align === "right"
                      ? "text-right"
                      : "";
                  if (NON_SORTABLE_KEYS.has(column.key)) {
                    return (
                      <th
                        key={column.key}
                        className={`whitespace-nowrap border-b border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] ${align}`}
                      >
                        {column.label}
                      </th>
                    );
                  }
                  return (
                    <th
                      key={column.key}
                      className={`whitespace-nowrap border-b border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] ${align}`}
                    >
                      <button
                        type="button"
                        onClick={() => toggleSort(column.key)}
                        className={`inline-flex items-center gap-1 transition hover:text-[var(--text,#0f172a)] ${
                          align === "text-right" ? "ml-auto" : ""
                        }`}
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
                    colSpan={COLUMNS.length}
                    className="px-5 py-12 text-center text-sm text-[var(--text-muted,#94a3b8)]"
                  >
                    No institutional investors match the current filters.
                  </td>
                </tr>
              ) : (
                items.map((row) => <InvestorRow key={row.id} row={row} />)
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Pagination ───────────────────────────────────────────────────── */}
      {meta && meta.total > 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">
            Showing {meta.total === 0 ? 0 : (meta.page - 1) * meta.limit + 1}
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
                updateState({
                  page: Math.min(meta.total_pages, meta.page + 1),
                })
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

function InvestorRow({ row }: { row: InstitutionalInvestorListItem }) {
  const href = `/institutional-investors/${row.id}` as Route;
  const location = [row.city, row.state].filter(Boolean).join(", ");

  return (
    <tr className="border-t border-[var(--border,rgba(30,64,175,0.1))] align-top transition hover:bg-[var(--row-hover,rgba(99,102,241,0.04))]">
      <td className="min-w-[220px] px-5 py-3.5">
        <Link
          href={href}
          className="block font-semibold text-[var(--text,#0f172a)] transition hover:text-[var(--accent,#6366f1)]"
        >
          {row.name}
        </Link>
        {row.legal_name && row.legal_name !== row.name ? (
          <div className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
            {row.legal_name}
          </div>
        ) : null}
      </td>
      <td className="px-5 py-3.5 font-mono text-[12px] text-[var(--text-dim,#475569)]">
        {row.cik ?? "—"}
      </td>
      <td className="px-5 py-3.5 text-[var(--text-dim,#475569)]">
        {location || "—"}
      </td>
      <td className="px-5 py-3.5 text-right font-semibold tabular-nums text-[var(--text,#0f172a)]">
        {row.total_aum != null ? formatCurrency(row.total_aum) : "—"}
      </td>
      <td className="px-5 py-3.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
        {row.latest_13f_filing_date
          ? formatDate(row.latest_13f_filing_date)
          : "—"}
      </td>
      <td className="px-5 py-3.5">
        {row.advisor_id != null ? (
          <Pill variant="healthy">RIA</Pill>
        ) : (
          <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
            —
          </span>
        )}
      </td>
    </tr>
  );
}
