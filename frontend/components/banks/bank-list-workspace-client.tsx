"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useUrlSyncedState } from "@/lib/use-url-synced-state";

import { ArrowDown, ArrowUp, Search, X } from "lucide-react";

import { apiRequest, buildApiPath } from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  BANK_LIST_STATE_DEFAULTS,
  type BankCharterAuthorityFilter,
  type BankDigitalAssetsFilter,
  type BankListQueryState,
  buildBankListUrl,
  clearAllFilters,
  encodeReturnParam,
  fromSearchParams,
  hasActiveFilters,
} from "@/lib/bank-list-state";
import { STATE_OPTIONS } from "@/lib/states";
import {
  BankStatusPill,
  CHARTER_STATUS_OPTIONS,
  charterAuthorityLabel,
  charterStatusLabel,
  DIGITAL_ASSETS_DASH_EXPLANATION,
  NO_OCC_TIMELINE_EXPLANATION,
} from "@/components/banks/bank-status-pill";
import { EstablishedDateRangeFilter } from "@/components/banks/filters/established-date-range-filter";
import { Button } from "@/components/ui/button";
import {
  MultiSelectFilter,
  type MultiSelectFilterOption,
} from "@/components/ui/multi-select-filter";
import { Pill } from "@/components/ui/pill";
import { Segmented, type SegmentedItem } from "@/components/ui/segmented";
import { Tag } from "@/components/ui/tag";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import type { BankListItem, BankListResponse } from "@/lib/types";

// ── Column catalog ────────────────────────────────────────────────────────
// Sortable keys must exist in the BE's ALLOWED_SORT_FIELDS
// (backend/app/services/banks.py); "digital_assets" is display-only.
const COLUMNS = [
  { key: "name", label: "Institution" },
  { key: "fdic_cert", label: "FDIC CERT" },
  { key: "state", label: "State" },
  { key: "charter_authority", label: "Charter" },
  { key: "charter_status", label: "Status" },
  {
    key: "digital_assets",
    label: "Digital Assets",
    title:
      "Matched on the OCC Digital Assets Licensing Applications page",
  },
  { key: "established_date", label: "Established" },
  { key: "last_action_date", label: "Last Action" },
] as const;

const NON_SORTABLE_KEYS = new Set<string>(["digital_assets"]);

// Charter-type + digital-assets segmented catalogs — module-level so the
// arrays stay referentially stable across renders. Mirror the advisor
// list's STATUS / 13F-scope row.
const AUTHORITY_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "All", label: "All" },
  { value: "OCC", label: "National (OCC)" },
  { value: "STATE", label: "State" },
];

const DIGITAL_ASSETS_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "all", label: "All banks" },
  { value: "tagged", label: "Digital assets only" },
];

// Sort options shown in the toolbar Combo. Backed by the BE-recognized
// sort_by keys on /api/v1/banks.
const SORT_OPTIONS = [
  { key: "established_date", label: "Established Date" },
  { key: "application_received_date", label: "Application Received" },
  { key: "last_action_date", label: "Last Action" },
  { key: "name", label: "Institution Name" },
  { key: "state", label: "State" },
  { key: "fdic_cert", label: "FDIC CERT" },
  { key: "charter_status", label: "Charter Status" },
  { key: "asset", label: "Total Assets" },
  { key: "deposits", label: "Total Deposits" },
] as const;

// ── Pagination helper ─────────────────────────────────────────────────────
// Mirrors the BD/advisor helper. Produces [1, 2, 3, …, last] with ellipses
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
function countActiveFilters(state: BankListQueryState): number {
  let n = 0;
  if (state.search !== BANK_LIST_STATE_DEFAULTS.search) n += 1;
  if (state.states.length > 0) n += 1;
  if (state.charterAuthority !== BANK_LIST_STATE_DEFAULTS.charterAuthority) n += 1;
  if (state.charterStatuses.length > 0) n += 1;
  if (state.digitalAssets !== BANK_LIST_STATE_DEFAULTS.digitalAssets) n += 1;
  if (state.establishedAfter !== null || state.establishedBefore !== null) n += 1;
  return n;
}

// Full state name for a 2-letter code; falls back to the code itself for
// anything outside the canonical list (defensive — source data is federal).
const STATE_NAME_BY_CODE: Readonly<Record<string, string>> = Object.fromEntries(
  STATE_OPTIONS.map((s) => [s.code, s.name]),
);
function stateNameFromCode(code: string): string {
  return STATE_NAME_BY_CODE[code] ?? code;
}

export function BankListWorkspaceClient() {
  // URL is the canonical store with a synchronous local mirror — see
  // useUrlSyncedState for the race this avoids on rapid multi-select edits.
  const { state, updateState, replaceState } = useUrlSyncedState(
    fromSearchParams,
    buildBankListUrl,
  );
  const [searchInput, setSearchInput] = useState(state.search);

  // Mirror the URL search-input into local state so the user can type
  // freely without re-fetching on every keystroke. Sync back on URL
  // change (e.g. clearAllFilters).
  useEffect(() => {
    setSearchInput(state.search);
  }, [state.search]);

  const [data, setData] = useState<BankListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Distinct states with at least one bank — fuels the state multi-select
  // so the dropdown only offers states that can actually match (bank
  // volume is low, so most states have zero rows).
  const [stateOptions, setStateOptions] = useState<MultiSelectFilterOption[]>([]);
  const [stateOptionsLoading, setStateOptionsLoading] = useState(true);

  useEffect(() => {
    let active = true;
    apiRequest<string[]>("/api/v1/banks/states")
      .then((codes) => {
        if (!active) return;
        setStateOptions(
          codes.map((code) => ({ value: code, label: stateNameFromCode(code) })),
        );
      })
      .catch(() => {
        /* silent — the filter just shows its no-options label */
      })
      .finally(() => {
        if (active) setStateOptionsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

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

  // Fetch the list whenever URL state changes. Cheap to re-fetch — the BE
  // response is paginated to ≤100 rows and total bank volume is tiny.
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
    if (state.search) params.search = state.search;
    if (state.states.length > 0) params.state = state.states;
    if (state.charterAuthority !== "All") {
      params.charter_authority = [state.charterAuthority];
    }
    if (state.charterStatuses.length > 0) {
      params.charter_status = state.charterStatuses;
    }
    if (state.digitalAssets === "tagged") params.digital_assets = "true";
    if (state.establishedAfter !== null) {
      params.established_after = state.establishedAfter;
    }
    if (state.establishedBefore !== null) {
      params.established_before = state.establishedBefore;
    }

    apiRequest<BankListResponse>(buildApiPath("/api/v1/banks", params), {
      signal: controller.signal,
    })
      .then((response) => {
        setData(response);
        setLoading(false);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Failed to load banks");
        setLoading(false);
      });

    return () => controller.abort();
  }, [
    state.search,
    state.states,
    state.charterAuthority,
    state.charterStatuses,
    state.digitalAssets,
    state.establishedAfter,
    state.establishedBefore,
    state.sortBy,
    state.sortDir,
    state.page,
    state.limit,
  ]);

  const items = useMemo(() => data?.items ?? [], [data]);
  const meta = data?.meta;
  const filtersActive = hasActiveFilters(state);
  const activeFilterCount = countActiveFilters(state);

  function handleClearFilters() {
    setSearchInput("");
    replaceState(clearAllFilters(state));
  }

  // Encoded return-URL appended to every detail link so the detail page
  // can rebuild the same filtered/sorted list state on back-nav.
  const returnEnvelope = useMemo(() => encodeReturnParam(state), [state]);
  const detailHref = useCallback(
    (id: number): Route => {
      const base = `/banks/${id}`;
      return (returnEnvelope ? `${base}?return=${returnEnvelope}` : base) as Route;
    },
    [returnEnvelope],
  );

  return (
    <div className="px-4 sm:px-7 pb-12 pt-7 lg:px-9">
      {/* ── Topbar: breadcrumb + h1 + right rail ─────────────────────────── */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Banks
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Banks
          </h1>
          {/* Data-source provenance line — mirrors the advisor list so users
              opening this for the first time understand the scope. */}
          <p className="mt-1 max-w-[640px] text-[12px] text-[var(--text-muted,#94a3b8)]">
            New national and state banking charters, including pending
            applications. Sourced from FDIC BankFind and the OCC Corporate
            Applications Search — both official public government sources.
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
              placeholder="Search bank name, city, CERT, or charter #…"
              aria-label="Search banks"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] outline-none placeholder:text-[var(--text-muted,#94a3b8)]"
            />
            <kbd className="rounded-[4px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-3,#dbeafe)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-dim,#475569)]">
              ⌘K
            </kbd>
          </form>
          <ThemeToggle />
        </div>
      </div>

      {/* ── Meta row: live-match pill ───────────────────────────────────── */}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
        {meta ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
            <span aria-hidden className="relative flex h-2 w-2">
              <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
              <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
            </span>
            {meta.total.toLocaleString()} institution{meta.total === 1 ? "" : "s"}
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
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleClearFilters}
            >
              <X aria-hidden className="h-3.5 w-3.5" strokeWidth={2} />
              Clear filters
            </Button>
          ) : null}
        </div>

        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              State
            </label>
            <MultiSelectFilter
              value={state.states}
              onChange={(next) => updateState({ states: next, page: 1 })}
              options={stateOptions}
              triggerLabel="All states"
              placeholder="Search states…"
              emptyLabel="No states match your search"
              noOptionsLabel="No banks tracked yet."
              loading={stateOptionsLoading}
              ariaLabel="State"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Charter Status
            </label>
            <MultiSelectFilter
              value={state.charterStatuses}
              onChange={(next) => updateState({ charterStatuses: next, page: 1 })}
              options={CHARTER_STATUS_OPTIONS}
              triggerLabel="All statuses"
              placeholder="Search statuses…"
              emptyLabel="No statuses match your search"
              ariaLabel="Charter status"
            />
          </div>

          <EstablishedDateRangeFilter
            establishedAfter={state.establishedAfter}
            establishedBefore={state.establishedBefore}
            onChange={(patch) =>
              updateState({
                ...(patch.establishedAfter !== undefined
                  ? { establishedAfter: patch.establishedAfter }
                  : {}),
                ...(patch.establishedBefore !== undefined
                  ? { establishedBefore: patch.establishedBefore }
                  : {}),
                page: 1,
              })
            }
          />
        </div>

        {/* Segmented row — mirrors the advisor list's Status / Scope pair.
            Charter Type maps to the BE's charter_authority filter; Digital
            Assets flips the tri-state digital_assets param to true. */}
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Charter Type
            </p>
            <Segmented
              value={state.charterAuthority}
              onChange={(next) =>
                updateState({
                  charterAuthority: next as BankCharterAuthorityFilter,
                  page: 1,
                })
              }
              items={AUTHORITY_ITEMS}
              ariaLabel="Charter type"
            />
          </div>
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Digital Assets
            </p>
            <Segmented
              value={state.digitalAssets}
              onChange={(next) =>
                updateState({
                  digitalAssets: next as BankDigitalAssetsFilter,
                  page: 1,
                })
              }
              items={DIGITAL_ASSETS_ITEMS}
              ariaLabel="Digital assets scope"
            />
          </div>
        </div>

        {/* Active-filter tags strip — one dismissible chip per applied
            filter, mirroring the sibling workspaces. */}
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
            {state.states.map((code) => (
              <Tag
                key={`state-${code}`}
                onDismiss={() =>
                  updateState({
                    states: state.states.filter((value) => value !== code),
                    page: 1,
                  })
                }
              >
                State: {stateNameFromCode(code)}
              </Tag>
            ))}
            {state.charterAuthority !== "All" ? (
              <Tag
                onDismiss={() => updateState({ charterAuthority: "All", page: 1 })}
              >
                Charter: {charterAuthorityLabel(state.charterAuthority)}
              </Tag>
            ) : null}
            {state.charterStatuses.map((status) => (
              <Tag
                key={`status-${status}`}
                onDismiss={() =>
                  updateState({
                    charterStatuses: state.charterStatuses.filter(
                      (value) => value !== status,
                    ),
                    page: 1,
                  })
                }
              >
                Status: {charterStatusLabel(status)}
              </Tag>
            ))}
            {state.digitalAssets === "tagged" ? (
              <Tag onDismiss={() => updateState({ digitalAssets: "all", page: 1 })}>
                Digital assets only
              </Tag>
            ) : null}
            {state.establishedAfter !== null ? (
              <Tag
                onDismiss={() => updateState({ establishedAfter: null, page: 1 })}
              >
                Established after {state.establishedAfter}
              </Tag>
            ) : null}
            {state.establishedBefore !== null ? (
              <Tag
                onDismiss={() => updateState({ establishedBefore: null, page: 1 })}
              >
                Established before {state.establishedBefore}
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
              Search institutions
            </label>
            <div className="flex h-[38px] w-full items-center gap-2.5 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 transition focus-within:border-[var(--accent,#6366f1)] focus-within:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]">
              <Search
                className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
                strokeWidth={2}
              />
              <input
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="Bank name, city, FDIC CERT, or OCC charter #"
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
              aria-label="Sort by"
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
              aria-label="Direction"
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
              aria-label="Page size"
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
          Couldn&apos;t load banks: {error}
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
              Bank charter list
            </h3>
          </div>
          {meta ? (
            <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
              {meta.total.toLocaleString()} institution
              {meta.total === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1000px] text-left">
            <thead>
              <tr>
                {COLUMNS.map((column) => {
                  const isSorted = state.sortBy === column.key;
                  const columnTitle = (column as { title?: string }).title;
                  if (NON_SORTABLE_KEYS.has(column.key)) {
                    return (
                      <th
                        key={column.key}
                        title={columnTitle}
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
                  <td colSpan={COLUMNS.length} className="px-5 py-12">
                    {/* Empty states matter here — new-charter volume is only
                        a handful per half-year, so an empty result is the
                        normal case for narrow windows, not an error. */}
                    <div className="mx-auto max-w-[460px] text-center">
                      <p className="text-sm font-semibold text-[var(--text,#0f172a)]">
                        {filtersActive
                          ? "No banks match the current filters"
                          : "No bank charters tracked yet"}
                      </p>
                      <p className="mt-1 text-sm text-[var(--text-muted,#94a3b8)]">
                        {filtersActive
                          ? "New-charter volume is low (roughly a handful per half-year) — try widening the established-date window or clearing a filter."
                          : "The nightly charter watcher populates this list from FDIC BankFind and the OCC Corporate Applications Search. Check back after the next run."}
                      </p>
                      {filtersActive ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="mt-3"
                          onClick={handleClearFilters}
                        >
                          <X aria-hidden className="h-3.5 w-3.5" strokeWidth={2} />
                          Clear filters
                        </Button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ) : (
                items.map((row) => (
                  <BankRow key={row.id} row={row} href={detailHref(row.id)} />
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
            Showing {meta.total === 0 ? 0 : (meta.page - 1) * meta.limit + 1}–
            {meta.total === 0
              ? 0
              : Math.min(meta.page * meta.limit, meta.total)}{" "}
            of {meta.total.toLocaleString()}
          </p>
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              disabled={meta.page <= 1}
              onClick={() => updateState({ page: Math.max(1, meta.page - 1) })}
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

// Muted "—" for an absent value that carries its reason: a native hover
// tooltip (the same title-attr pattern this table already uses on the
// Digital Assets column header) plus sr-only text so screen readers hear
// the explanation as cell content. Deliberately not focusable — a tab
// stop per dash across a 100-row page would wreck keyboard navigation.
function ExplainedDash({ explanation }: { explanation: string }) {
  return (
    <span
      title={explanation}
      className="cursor-help text-[12px] text-[var(--text-muted,#94a3b8)]"
    >
      <span aria-hidden>—</span>
      <span className="sr-only">{explanation}</span>
    </span>
  );
}

function BankRow({ row, href }: { row: BankListItem; href: Route }) {
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
        {location ? (
          <div className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
            {location}
          </div>
        ) : null}
      </td>
      <td className="px-5 py-3.5 font-mono text-[12px] text-[var(--text-dim,#475569)]">
        {row.fdic_cert ?? "—"}
      </td>
      <td className="px-5 py-3.5 text-[var(--text-dim,#475569)]">
        {row.state ?? "—"}
      </td>
      <td className="px-5 py-3.5 text-[var(--text-dim,#475569)]">
        <span>{charterAuthorityLabel(row.charter_authority)}</span>
        {row.occ_charter_number ? (
          <div className="mt-0.5 font-mono text-[11px] text-[var(--text-muted,#94a3b8)]">
            Charter #{row.occ_charter_number}
          </div>
        ) : null}
      </td>
      <td className="px-5 py-3.5">
        <BankStatusPill status={row.charter_status} />
      </td>
      <td className="px-5 py-3.5">
        {row.digital_assets ? (
          <Pill variant="self">Digital Assets</Pill>
        ) : (
          <ExplainedDash explanation={DIGITAL_ASSETS_DASH_EXPLANATION} />
        )}
      </td>
      <td className="px-5 py-3.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
        {row.established_date ? formatDate(row.established_date) : "—"}
      </td>
      <td className="px-5 py-3.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
        {/* Null last_action_date is the norm for state charters (they never
            file with the OCC) — explain that case. A non-STATE row could
            only get here via undated OCC actions, where the state-charter
            copy would mislead, so it keeps the bare dash. */}
        {row.last_action_date ? (
          formatDate(row.last_action_date)
        ) : row.charter_authority === "STATE" ? (
          <ExplainedDash explanation={NO_OCC_TIMELINE_EXPLANATION} />
        ) : (
          "—"
        )}
      </td>
    </tr>
  );
}
