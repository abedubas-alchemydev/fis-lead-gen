"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import clsx from "clsx";
import { useUrlSyncedState } from "@/lib/use-url-synced-state";

import { ArrowDown, ArrowUp, ChevronDown, Heart, Search, X } from "lucide-react";

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
  charterTypeLabel,
  DIGITAL_ASSETS_DASH_EXPLANATION,
  NO_OCC_TIMELINE_EXPLANATION,
} from "@/components/banks/bank-status-pill";
import { BankEnrichmentNotice } from "@/components/banks/bank-enrichment-notice";
import { EstablishedDateRangeFilter } from "@/components/banks/filters/established-date-range-filter";
import { BulkListPicker } from "@/components/list-picker/bulk-list-picker";
import { ListPicker } from "@/components/list-picker/list-picker";
import { Button, buttonBase, buttonSizes } from "@/components/ui/button";
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

// "New & pending" quick-view preset — the recommended default landing view.
// Maps to the BE new_charters_only param: "new" → true (new / pending
// charters only), "all" → false (the full ~4,300-row FDIC + OCC directory).
const NEW_CHARTERS_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "new", label: "New & pending" },
  { value: "all", label: "All institutions" },
];

// Seed for the Charter Type multi-select. OCC's CharterType is a free-form,
// "descriptive only" field with no distinct-values endpoint, so the live
// option list is built at render time by unioning this seed with the values
// actually present on the loaded page and the current selection (see
// charterTypeOptions). Seeding the two confirmed OCC tokens keeps the control
// usable before the BE promotes charter_type onto the list payload.
const CHARTER_TYPE_SEED: ReadonlyArray<string> = ["National", "TrustCo-National"];

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
  if (state.newChartersOnly !== BANK_LIST_STATE_DEFAULTS.newChartersOnly) n += 1;
  if (state.states.length > 0) n += 1;
  if (state.charterAuthority !== BANK_LIST_STATE_DEFAULTS.charterAuthority) n += 1;
  if (state.charterTypes.length > 0) n += 1;
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

  // ── Bulk-select state (page-scoped) ─────────────────────────────────────
  // Mirrors the master-list workspace: selection is ephemeral (NOT URL-backed
  // — every list refetch clears it) so a user never bulk-acts on off-page
  // rows they can't see. Resolves on the bulk "Save to list" action.
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<number>>(
    () => new Set(),
  );
  const [bulkPickerOpen, setBulkPickerOpen] = useState(false);
  const headerCheckboxRef = useRef<HTMLInputElement | null>(null);
  const bulkActionTriggerRef = useRef<HTMLButtonElement | null>(null);

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
    // Sent explicitly in both directions: the BE default for this new param
    // is unknown at build time, so we never rely on omission to mean "all".
    params.new_charters_only = state.newChartersOnly ? "true" : "false";
    if (state.states.length > 0) params.state = state.states;
    if (state.charterAuthority !== "All") {
      params.charter_authority = [state.charterAuthority];
    }
    if (state.charterTypes.length > 0) {
      params.charter_type = state.charterTypes;
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
    state.newChartersOnly,
    state.states,
    state.charterAuthority,
    state.charterTypes,
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

  // ── Bulk-select effects ────────────────────────────────────────────────
  // Selection scope is "current page only" — every items refetch (page,
  // sort, filter, search) clears the set so the user never bulk-acts on
  // off-page rows they can't see.
  useEffect(() => {
    setSelectedIds(new Set());
  }, [items]);

  // Auto-dismiss the bulk popover when there's nothing left to act on.
  useEffect(() => {
    if (selectedIds.size === 0) setBulkPickerOpen(false);
  }, [selectedIds]);

  const allOnPageSelected =
    items.length > 0 && selectedIds.size === items.length;
  const someOnPageSelected =
    selectedIds.size > 0 && selectedIds.size < items.length;

  // Indeterminate must be set imperatively — React doesn't expose it as a
  // prop on <input>.
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

  // Charter Type options: OCC CharterType has no distinct-values endpoint, so
  // union the known-token seed with the values present on the loaded page
  // (populated once the BE promotes charter_type onto the list item) and the
  // current selection — the last keeps share-linked / off-page picks visible
  // and labeled. Never surfaces an option that can't match a row.
  const charterTypeOptions = useMemo<MultiSelectFilterOption[]>(() => {
    const byValue = new Map<string, MultiSelectFilterOption>();
    const add = (raw: string | null | undefined) => {
      const value = (raw ?? "").trim();
      if (value && !byValue.has(value)) {
        byValue.set(value, { value, label: charterTypeLabel(value) });
      }
    };
    CHARTER_TYPE_SEED.forEach(add);
    items.forEach((row) => add(row.charter_type));
    state.charterTypes.forEach(add);
    return Array.from(byValue.values()).sort((a, b) =>
      a.label.localeCompare(b.label),
    );
  }, [items, state.charterTypes]);

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
      {/* Dismissible "enrichment in progress" notice (localStorage-remembered). */}
      <BankEnrichmentNotice />
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
          <p className="mt-1 max-w-[680px] text-[12px] text-[var(--text-muted,#94a3b8)]">
            Every FDIC-insured and OCC-chartered U.S. banking institution, with
            newly filed, pending, and digital-asset charters available as
            filtered views. Sourced from FDIC BankFind and the OCC Corporate
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

        {/* Quick view preset — the recommended landing view. "New & pending"
            maps to new_charters_only=true so the lead-gen-relevant charters
            lead the page instead of sinking below thousands of established
            banks under the established_date-desc sort; "All institutions"
            opens the full FDIC + OCC directory. */}
        <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-4 py-3">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Quick view
            </p>
            <p className="mt-0.5 text-[12px] text-[var(--text-dim,#475569)]">
              Lead with newly filed &amp; pending charters, or browse every
              tracked institution.
            </p>
          </div>
          <div className="ml-auto">
            <Segmented
              value={state.newChartersOnly ? "new" : "all"}
              onChange={(next) =>
                updateState({ newChartersOnly: next === "new", page: 1 })
              }
              items={NEW_CHARTERS_ITEMS}
              ariaLabel="Quick view: new and pending, or all institutions"
            />
          </div>
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

          <div>
            {/* charter_TYPE (descriptive OCC CharterType) — distinct from the
                charter_AUTHORITY OCC/STATE segment further down. */}
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Charter Type
            </label>
            <MultiSelectFilter
              value={state.charterTypes}
              onChange={(next) => updateState({ charterTypes: next, page: 1 })}
              options={charterTypeOptions}
              triggerLabel="All charter types"
              placeholder="Search charter types…"
              emptyLabel="No charter types match your search"
              noOptionsLabel="No charter types tracked yet."
              ariaLabel="Charter type"
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
            Charter Authority maps to the BE's charter_authority filter (OCC
            national vs STATE — NOT the descriptive charter_type multi-select
            above); Digital Assets flips the tri-state digital_assets param. */}
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Charter Authority
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
              ariaLabel="Charter authority"
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
            {state.newChartersOnly !== BANK_LIST_STATE_DEFAULTS.newChartersOnly ? (
              <Tag
                onDismiss={() =>
                  updateState({
                    newChartersOnly: BANK_LIST_STATE_DEFAULTS.newChartersOnly,
                    page: 1,
                  })
                }
              >
                All institutions (incl. established)
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
                Charter authority: {charterAuthorityLabel(state.charterAuthority)}
              </Tag>
            ) : null}
            {state.charterTypes.map((charterType) => (
              <Tag
                key={`charter-type-${charterType}`}
                onDismiss={() =>
                  updateState({
                    charterTypes: state.charterTypes.filter(
                      (value) => value !== charterType,
                    ),
                    page: 1,
                  })
                }
              >
                Charter type: {charterTypeLabel(charterType)}
              </Tag>
            ))}
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
                className={clsx(
                  buttonBase,
                  buttonSizes.sm,
                  "rounded-[8px] border border-[rgba(99,102,241,0.4)] bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] text-[12px] text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)]",
                )}
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
                  anchorRef={bulkActionTriggerRef}
                  entityType="bank"
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
              {meta.total.toLocaleString()} institution
              {meta.total === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1044px] text-left">
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
                        ? "Deselect all banks on this page"
                        : "Select all banks on this page"
                    }
                    checked={allOnPageSelected}
                    onChange={toggleAllOnPage}
                    disabled={loading || items.length === 0}
                    className="h-4 w-4 rounded border-[var(--border-2,rgba(30,64,175,0.16))] text-[var(--accent,#6366f1)] focus:ring-[var(--accent,#6366f1)]"
                  />
                </th>
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
                      <td className="w-[44px] px-5 py-3.5">
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
                  <td colSpan={COLUMNS.length + 1} className="px-5 py-12">
                    {/* Two empty cases. With no active filters the default
                        "New & pending" preset is on, so an empty result just
                        means no charters are in flight right now (the normal
                        case — a handful per half-year nationally); point the
                        user at the full directory rather than implying the
                        whole dataset is empty. */}
                    <div className="mx-auto max-w-[460px] text-center">
                      <p className="text-sm font-semibold text-[var(--text,#0f172a)]">
                        {filtersActive
                          ? "No banks match the current filters"
                          : "No new or pending charters right now"}
                      </p>
                      <p className="mt-1 text-sm text-[var(--text-muted,#94a3b8)]">
                        {filtersActive
                          ? "Try widening the established-date window, or clear a filter to see more institutions."
                          : "New and pending charters are rare — roughly a handful per half-year nationally. Switch to All institutions to browse every FDIC-insured and OCC-chartered bank, or check back after the nightly watcher run."}
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
                      ) : (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="mt-3"
                          onClick={() =>
                            updateState({ newChartersOnly: false, page: 1 })
                          }
                        >
                          Show all institutions
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ) : (
                items.map((row) => (
                  <BankRow
                    key={row.id}
                    row={row}
                    href={detailHref(row.id)}
                    selected={selectedIds.has(row.id)}
                    onToggle={toggleRow}
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

function BankRow({
  row,
  href,
  selected,
  onToggle,
}: {
  row: BankListItem;
  href: Route;
  selected: boolean;
  onToggle: (id: number) => void;
}) {
  const location = [row.city, row.state].filter(Boolean).join(", ");

  return (
    <tr className="border-t border-[var(--border,rgba(30,64,175,0.1))] align-top transition hover:bg-[var(--row-hover,rgba(99,102,241,0.04))]">
      <td className="w-[44px] px-5 py-3.5 align-top">
        <input
          type="checkbox"
          aria-label={`Select ${row.name}`}
          checked={selected}
          onChange={() => onToggle(row.id)}
          onClick={(e) => e.stopPropagation()}
          className="mt-0.5 h-4 w-4 rounded border-[var(--border-2,rgba(30,64,175,0.16))] text-[var(--accent,#6366f1)] focus:ring-[var(--accent,#6366f1)]"
        />
      </td>
      <td className="min-w-[220px] px-5 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
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
          </div>
          <ListPicker firmId={row.id} variant="row" entityType="bank" />
        </div>
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
