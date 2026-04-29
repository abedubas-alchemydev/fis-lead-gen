"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";

import { ArrowDown, ArrowUp, Bell, Search, TrendingDown, TrendingUp, X } from "lucide-react";

import { apiRequest, buildApiPath } from "@/lib/api";
import { formatCurrency, formatRelativeTime } from "@/lib/format";
import {
  buildMasterListUrl,
  clearAllFilters,
  encodeReturnParam,
  fromSearchParams,
  hasActiveFilters,
  type MasterListQueryState,
} from "@/lib/master-list-state";
import { STATE_NAMES, stateCodeFromName } from "@/lib/states";
import { Combo } from "@/components/ui/combo";
import { ListPicker } from "@/components/list-picker/list-picker";
import { NetCapitalRangeFilter } from "@/components/master-list/filters/net-capital-range-filter";
import { RegistrationDateRangeFilter } from "@/components/master-list/filters/registration-date-range-filter";
import {
  MultiSelectFilter,
  type MultiSelectFilterOption,
} from "@/components/ui/multi-select-filter";
import { Dotmark, Segmented, type SegmentedItem } from "@/components/ui/segmented";
import { Pill, type PillVariant } from "@/components/ui/pill";
import { Tag } from "@/components/ui/tag";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import type {
  BrokerDealerListItem,
  BrokerDealerListResponse,
  CompetitorProvidersResponse,
  DashboardStats,
  TypeOfBusinessOption,
} from "@/lib/types";

// ── Column catalog ────────────────────────────────────────────────────────
// 9 columns (mockup Q1 resolution). Location is merged into the firm-cell
// as sub-text so the table has room for the Clearing Partner column, which
// frequently carries long compound provider names.
const columns = [
  { key: "name", label: "Firm Name" },
  { key: "cik", label: "CIK" },
  { key: "current_clearing_partner", label: "Clearing Partner" },
  { key: "current_clearing_type", label: "Clearing Type" },
  { key: "health_status", label: "Financial Health" },
  { key: "lead_score", label: "Lead Priority" },
  { key: "latest_net_capital", label: "Net Capital" },
  { key: "yoy_growth", label: "YoY Growth" },
  { key: "last_filing_date", label: "Last Filing" },
] as const;

// ── Segmented option catalogs ─────────────────────────────────────────────
// Kept as module-level constants so the arrays are referentially stable
// between renders (cheap child renders inside Segmented).
const HEALTH_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "All", label: "All" },
  { value: "healthy", label: "Healthy", dot: "healthy" },
  { value: "ok", label: "OK", dot: "ok" },
  { value: "at_risk", label: "At Risk", dot: "risk" },
];

const PRIORITY_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "All", label: "All" },
  { value: "hot", label: "Hot", dot: "hot" },
  { value: "warm", label: "Warm", dot: "warm" },
  { value: "cold", label: "Cold", dot: "cold" },
];

const CLEARING_TYPE_OPTS = [
  { value: "All", label: "All clearing types" },
  { value: "fully_disclosed", label: "Fully Disclosed" },
  { value: "self_clearing", label: "Self-Clearing" },
  { value: "omnibus", label: "Omnibus" },
  { value: "unknown", label: "Unknown" },
] as const;

type ListMode = "primary" | "alternative" | "all";

const LIST_MODES: ReadonlyArray<{ value: ListMode; label: string }> = [
  { value: "primary", label: "Primary List" },
  { value: "alternative", label: "Alternative List" },
  { value: "all", label: "All Firms" },
];

// ── Backend-enum → Pill variant / label mappings ──────────────────────────
// Central so row-renderer code stays declarative and the mockup's variant
// names ("fd", "self", "omni") stay inside the rendering layer rather than
// leaking into the backend query contract.
function healthVariant(status: string | null): PillVariant {
  if (status === "healthy") return "healthy";
  if (status === "ok") return "ok";
  if (status === "at_risk") return "risk";
  return "unknown";
}

function healthLabel(status: string | null): string {
  if (status === "healthy") return "Healthy";
  if (status === "ok") return "OK";
  if (status === "at_risk") return "At Risk";
  return "Unknown";
}

function clearingTypeVariant(value: string | null): PillVariant {
  if (value === "fully_disclosed") return "fd";
  if (value === "self_clearing") return "self";
  if (value === "omnibus") return "omni";
  return "unknown";
}

function clearingTypeLabel(value: string | null): string {
  if (value === "fully_disclosed") return "Fully Disclosed";
  if (value === "self_clearing") return "Self-Clearing";
  if (value === "omnibus") return "Omnibus";
  return "Unknown";
}

function priorityLabel(priority: string | null): string {
  if (priority === "hot") return "Hot";
  if (priority === "warm") return "Warm";
  if (priority === "cold") return "Cold";
  return "—";
}

function priorityVariant(priority: string | null): PillVariant {
  if (priority === "hot") return "hot";
  if (priority === "warm") return "warm";
  if (priority === "cold") return "cold";
  return "unknown";
}

// ── Pagination helper ─────────────────────────────────────────────────────
// Produces the sequence shown in the mockup: [1, 2, 3, …, last]. Ellipses
// are string literals so React can key them distinctly from page numbers.
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

export function MasterListWorkspaceClient() {
  // ── URL is the source of truth for filter / sort / page / limit ─────
  // The workspace state used to live in component-local useState, which
  // meant returning from a firm-detail page mounted a fresh component
  // instance with default state and threw away every filter, the
  // current page, and the sort. Lifting it into URL search params makes
  // back-nav, share-links, and full reloads all restore the same view.
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryState = useMemo<MasterListQueryState>(
    () => fromSearchParams(searchParams),
    [searchParams],
  );

  // Commit a state change to the URL. router.replace (not push) so the
  // back button still goes to the previous *route*, not the previous
  // *filter combo* — Deshorn doesn't want to mash Back five times to
  // get past his last filter mutation.
  const commit = useCallback(
    (next: MasterListQueryState) => {
      router.replace(buildMasterListUrl(next) as Route, { scroll: false });
    },
    [router],
  );

  const updateState = useCallback(
    (patch: Partial<MasterListQueryState>) => {
      commit({ ...queryState, ...patch });
    },
    [commit, queryState],
  );

  const stateFilter = queryState.state;
  const search = queryState.search;
  const healthFilter = queryState.health;
  const leadPriorityFilter = queryState.leadPriority;
  const clearingTypeFilter = queryState.clearingType;
  const clearingPartnerFilter = queryState.clearingPartner;
  const typesOfBusinessFilter = queryState.typesOfBusiness;
  const minNetCapitalFilter = queryState.minNetCapital;
  const maxNetCapitalFilter = queryState.maxNetCapital;
  const registeredAfterFilter = queryState.registeredAfter;
  const registeredBeforeFilter = queryState.registeredBefore;
  const listMode = queryState.list;
  const sortBy = queryState.sortBy;
  const sortDir = queryState.sortDir;
  const page = queryState.page;
  const limit = queryState.limit;

  // ── Table data — fetched on every URL-state change ─────────────────
  const [items, setItems] = useState<BrokerDealerListItem[]>([]);
  const [clearingPartners, setClearingPartners] = useState<string[]>([]);
  const [competitorSeeds, setCompetitorSeeds] = useState<string[]>([]);
  // Distinct types-of-business values + per-type firm counts. Loaded once
  // from /broker-dealers/types-of-business; sorted by count desc inside the
  // filter so the most common types surface first. Empty array until the
  // one-shot fetch returns; the filter renders its loading row meanwhile.
  const [typesOfBusinessOptions, setTypesOfBusinessOptions] = useState<
    TypeOfBusinessOption[]
  >([]);
  const [typesOfBusinessLoading, setTypesOfBusinessLoading] = useState(true);
  const [listCounts, setListCounts] = useState<Record<ListMode, number | null>>({
    primary: null,
    alternative: null,
    all: null,
  });

  // Local, *unsubmitted* search input. Only the submitted value lands in
  // the URL — so typing into the search field doesn't churn the URL on
  // every keystroke. Hydrated from the URL on first render so reloading
  // a `/master-list?q=foo` page repopulates the visible input.
  const [searchInput, setSearchInput] = useState(search);
  useEffect(() => {
    setSearchInput(search);
  }, [search]);

  const [meta, setMeta] = useState<BrokerDealerListResponse["meta"]>({
    page: 1,
    limit: 25,
    total: 0,
    total_pages: 1,
    pipeline_refreshed_at: null,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Mirrors the sidebar's Alerts badge — drives the topbar bell's red pip
  // when unread deficiency alerts exist. Same `/api/v1/stats` endpoint the
  // sidebar hits; one extra GET per page-load, no per-render refetches.
  const [unreadAlertsCount, setUnreadAlertsCount] = useState(0);

  // Query-string composition — byte-for-byte identical to the pre-redesign
  // contract. Tweaking a single param here would ripple through the
  // FastAPI validator.
  const queryPath = useMemo(
    () =>
      buildApiPath("/api/v1/broker-dealers", {
        search,
        state: stateFilter ? [stateCodeFromName(stateFilter) ?? stateFilter] : undefined,
        health: healthFilter === "All" ? undefined : [healthFilter],
        lead_priority: leadPriorityFilter === "All" ? undefined : [leadPriorityFilter],
        clearing_partner: clearingPartnerFilter ? [clearingPartnerFilter] : undefined,
        clearing_type: clearingTypeFilter === "All" ? undefined : [clearingTypeFilter],
        types_of_business:
          typesOfBusinessFilter.length > 0 ? typesOfBusinessFilter : undefined,
        // null → undefined so buildApiPath omits the key. The BE rejects
        // negative values (ge=0) which the FE filter component already
        // refuses to emit, so passing the raw number through is safe.
        min_net_capital: minNetCapitalFilter ?? undefined,
        max_net_capital: maxNetCapitalFilter ?? undefined,
        registered_after: registeredAfterFilter ?? undefined,
        registered_before: registeredBeforeFilter ?? undefined,
        list: listMode,
        sort_by: sortBy,
        sort_dir: sortDir,
        page,
        limit,
      }),
    [
      search,
      stateFilter,
      healthFilter,
      leadPriorityFilter,
      clearingPartnerFilter,
      clearingTypeFilter,
      typesOfBusinessFilter,
      minNetCapitalFilter,
      maxNetCapitalFilter,
      registeredAfterFilter,
      registeredBeforeFilter,
      listMode,
      sortBy,
      sortDir,
      page,
      limit,
    ],
  );

  // Main table fetch — active-flag pattern to swallow late responses when
  // the user re-filters mid-flight.
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    async function loadTable() {
      try {
        const response = await apiRequest<BrokerDealerListResponse>(queryPath);
        if (!active) return;
        setItems(response.items);
        setMeta(response.meta);
      } catch (loadError) {
        if (!active) return;
        setError(loadError instanceof Error ? loadError.message : "Unable to load broker-dealers.");
      } finally {
        if (active) setLoading(false);
      }
    }

    void loadTable();
    return () => {
      active = false;
    };
  }, [queryPath]);

  // One-shot filter-metadata fetch. Pulls states + clearing-partners +
  // active-competitor names (for the Combo's quick-chips) + per-list totals
  // (for the tab-count badges). Per plan Q3 default (a): three limit=1
  // probes instead of a new backend endpoint.
  useEffect(() => {
    let active = true;

    async function loadFilters() {
      // Each fetch is settled independently so one failing endpoint (e.g.
      // a 403 on /settings/competitors for the Viewer role) does not wipe
      // every other filter on the page.
      const [
        partner,
        competitor,
        types,
        primary,
        alternative,
        all,
      ] = await Promise.allSettled([
        apiRequest<string[]>("/api/v1/broker-dealers/clearing-partners"),
        apiRequest<CompetitorProvidersResponse>("/api/v1/settings/competitors"),
        apiRequest<TypeOfBusinessOption[]>(
          "/api/v1/broker-dealers/types-of-business",
        ),
        apiRequest<BrokerDealerListResponse>(
          buildApiPath("/api/v1/broker-dealers", { list: "primary", limit: 1 }),
        ),
        apiRequest<BrokerDealerListResponse>(
          buildApiPath("/api/v1/broker-dealers", { list: "alternative", limit: 1 }),
        ),
        apiRequest<BrokerDealerListResponse>(
          buildApiPath("/api/v1/broker-dealers", { list: "all", limit: 1 }),
        ),
      ]);
      if (!active) return;

      setClearingPartners(partner.status === "fulfilled" ? partner.value : []);

      if (competitor.status === "fulfilled") {
        setCompetitorSeeds(
          competitor.value.items
            .filter((item) => item.is_active)
            .sort((a, b) => a.priority - b.priority)
            .map((item) => item.name),
        );
      } else {
        setCompetitorSeeds([]);
      }

      if (types.status === "fulfilled") {
        // Defensive parsing: the BE has occasionally returned thousands of
        // entries (mostly firm-specific free-text "other" values with
        // count=1) and a handful with null/empty `type` fields. Drop the
        // malformed ones, sort by count desc with an alpha tiebreak, and
        // hard-cap at 100 so a future BE regression can't wedge the DOM.
        const safeTypes: TypeOfBusinessOption[] = (
          Array.isArray(types.value) ? types.value : []
        )
          .filter(
            (opt): opt is TypeOfBusinessOption =>
              opt != null &&
              typeof opt.type === "string" &&
              opt.type.trim().length > 0 &&
              typeof opt.count === "number" &&
              opt.count > 0,
          )
          .sort(
            (a, b) =>
              b.count - a.count || a.type.localeCompare(b.type),
          )
          .slice(0, 100);
        setTypesOfBusinessOptions(safeTypes);
      } else {
        setTypesOfBusinessOptions([]);
      }

      setListCounts({
        primary: primary.status === "fulfilled" ? primary.value.meta.total : null,
        alternative:
          alternative.status === "fulfilled"
            ? alternative.value.meta.total
            : null,
        all: all.status === "fulfilled" ? all.value.meta.total : null,
      });

      setTypesOfBusinessLoading(false);
    }

    void loadFilters();
    return () => {
      active = false;
    };
  }, []);

  // Mirror the sidebar's Alerts badge so the topbar bell can show a pip.
  // The sidebar owns its own copy of this fetch inside AppShell; exposing
  // that state across the route boundary would require a provider/context,
  // which is out of scope. One extra GET on mount is the simpler trade.
  useEffect(() => {
    let active = true;
    apiRequest<DashboardStats>("/api/v1/stats")
      .then((stats) => {
        if (active) setUnreadAlertsCount(stats.deficiency_alerts);
      })
      .catch(() => {
        /* silent — bell just won't show a pip */
      });
    return () => {
      active = false;
    };
  }, []);

  function toggleSort(columnKey: string) {
    if (sortBy === columnKey) {
      updateState({
        sortDir: sortDir === "asc" ? "desc" : "asc",
        page: 1,
      });
      return;
    }
    updateState({ sortBy: columnKey, sortDir: "asc", page: 1 });
  }

  function clearFilters() {
    setSearchInput("");
    commit(clearAllFilters(queryState));
  }

  const filtersActive = hasActiveFilters(queryState);

  const activeFilterCount = useMemo(() => {
    let count = 0;
    if (search !== "") count += 1;
    if (stateFilter !== "") count += 1;
    if (healthFilter !== "All") count += 1;
    if (leadPriorityFilter !== "All") count += 1;
    if (clearingPartnerFilter !== "") count += 1;
    if (clearingTypeFilter !== "All") count += 1;
    if (typesOfBusinessFilter.length > 0) count += 1;
    if (minNetCapitalFilter !== null) count += 1;
    if (maxNetCapitalFilter !== null) count += 1;
    if (registeredAfterFilter !== null) count += 1;
    if (registeredBeforeFilter !== null) count += 1;
    return count;
  }, [
    search,
    stateFilter,
    healthFilter,
    leadPriorityFilter,
    clearingPartnerFilter,
    clearingTypeFilter,
    typesOfBusinessFilter,
    minNetCapitalFilter,
    maxNetCapitalFilter,
    registeredAfterFilter,
    registeredBeforeFilter,
  ]);

  // Memoized options shape for the multi-select. Sorted by count desc with
  // alphabetical tiebreak so the most-common types render at the top of the
  // popover. The empty-string short-circuit keeps the sort stable while the
  // initial fetch is in flight.
  const typesOfBusinessFilterOptions = useMemo<MultiSelectFilterOption[]>(
    () =>
      [...typesOfBusinessOptions]
        .sort((a, b) => {
          if (b.count !== a.count) return b.count - a.count;
          return a.type.localeCompare(b.type);
        })
        .map((item) => ({ value: item.type, label: item.type, count: item.count })),
    [typesOfBusinessOptions],
  );

  const pages = paginationPages(meta.page, meta.total_pages);

  // Encoded return-URL appended to every firm-detail link so the detail
  // page can rebuild the same filtered/sorted list state. Empty string
  // when the workspace is at its default state — no point shipping a
  // no-op envelope through every <Link>.
  const returnEnvelope = useMemo(
    () => encodeReturnParam(queryState),
    [queryState],
  );

  function detailHref(id: number): Route {
    const base = `/master-list/${id}`;
    return (returnEnvelope ? `${base}?return=${returnEnvelope}` : base) as Route;
  }

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* ── Topbar ──────────────────────────────────────────────────────── */}
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Master List
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Broker-Dealer Master List
          </h1>
        </div>
        {/* .topbar-actions — search + theme + notifications. The search
            form shares the searchInput / submitted-search contract with
            the toolbar-card search below, so both inputs drive the same
            URL-backed filter. The bell pip lights up whenever there are
            unread deficiency alerts. */}
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
              placeholder="Search broker-dealers, firms, CRDs…"
              aria-label="Search broker-dealers"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] outline-none placeholder:text-[var(--text-muted,#94a3b8)]"
            />
            <kbd className="rounded-[4px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-3,#dbeafe)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-dim,#475569)]">
              ⌘K
            </kbd>
          </form>
          <ThemeToggle />
          <button
            type="button"
            aria-label={
              unreadAlertsCount > 0
                ? `${unreadAlertsCount} unread deficiency alerts`
                : "Notifications"
            }
            className="relative grid h-[38px] w-[38px] place-items-center rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
          >
            <Bell className="h-[18px] w-[18px]" strokeWidth={2} />
            {unreadAlertsCount > 0 ? (
              <span
                aria-hidden
                className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full border-2 border-[var(--bg,#eaf3ff)] bg-[var(--red,#ef4444)]"
              />
            ) : null}
          </button>
        </div>
      </div>

      {/* ── Tabs (list-mode switch) ─────────────────────────────────────── */}
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <div className="inline-flex rounded-[12px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] p-1">
          {LIST_MODES.map((mode) => {
            const active = listMode === mode.value;
            const count = listCounts[mode.value];
            return (
              <button
                key={mode.value}
                type="button"
                onClick={() => updateState({ list: mode.value, page: 1 })}
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

        {/* .tabs-meta — refresh stamp (driven by backend meta.pipeline_refreshed_at)
            + live-match pill (meta.total from the current list query, pulsing
            green dot). Omits the refresh span when the field is null so we
            never fabricate a timestamp. */}
        <div className="flex items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
          {meta.pipeline_refreshed_at ? (
            <span>Pipeline refreshed {formatRelativeTime(meta.pipeline_refreshed_at)}</span>
          ) : null}
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
            <span aria-hidden className="relative flex h-2 w-2">
              <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
              <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
            </span>
            {meta.total.toLocaleString()} match{meta.total === 1 ? "" : "es"}
          </span>
        </div>
      </div>

      {/* ── Filters card ───────────────────────────────────────────────── */}
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
              Refine the workspace
            </h3>
          </div>
          {filtersActive ? (
            <button
              type="button"
              onClick={clearFilters}
              className="inline-flex items-center gap-1.5 rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
            >
              <X aria-hidden className="h-3.5 w-3.5" strokeWidth={2} />
              Clear filters
            </button>
          ) : null}
        </div>

        <div className="mb-4 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              State
            </label>
            <Combo
              value={stateFilter}
              onChange={(next) => updateState({ state: next, page: 1 })}
              options={STATE_NAMES}
              placeholder="Search states…"
              emptyLabel="All states"
              ariaLabel="State"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Clearing Partner
            </label>
            <Combo
              value={clearingPartnerFilter}
              onChange={(next) => updateState({ clearingPartner: next, page: 1 })}
              options={clearingPartners}
              quickChips={competitorSeeds}
              placeholder="Search partners…"
              emptyLabel="All providers"
              ariaLabel="Clearing Partner"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Clearing Type
            </label>
            <select
              value={clearingTypeFilter}
              onChange={(event) =>
                updateState({ clearingType: event.target.value, page: 1 })
              }
              className="h-[38px] w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            >
              {CLEARING_TYPE_OPTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Types of Business
            </label>
            <MultiSelectFilter
              value={typesOfBusinessFilter}
              onChange={(next) =>
                updateState({ typesOfBusiness: next, page: 1 })
              }
              options={typesOfBusinessFilterOptions}
              triggerLabel="Types of Business"
              placeholder="Search types…"
              emptyLabel="No types match your search"
              noOptionsLabel="No types of business available."
              loading={typesOfBusinessLoading}
              ariaLabel="Types of Business"
            />
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Financial Health
            </p>
            <Segmented
              value={healthFilter}
              onChange={(next) => updateState({ health: next, page: 1 })}
              items={HEALTH_ITEMS}
              ariaLabel="Financial health"
            />
          </div>
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Lead Priority
            </p>
            <Segmented
              value={leadPriorityFilter}
              onChange={(next) => updateState({ leadPriority: next, page: 1 })}
              items={PRIORITY_ITEMS}
              ariaLabel="Lead priority"
            />
          </div>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <NetCapitalRangeFilter
            min={minNetCapitalFilter}
            max={maxNetCapitalFilter}
            onChange={(patch) => {
              const next: Partial<MasterListQueryState> = { page: 1 };
              if (patch.min !== undefined) next.minNetCapital = patch.min;
              if (patch.max !== undefined) next.maxNetCapital = patch.max;
              updateState(next);
            }}
          />
          <RegistrationDateRangeFilter
            registeredAfter={registeredAfterFilter}
            registeredBefore={registeredBeforeFilter}
            onChange={(patch) => {
              const next: Partial<MasterListQueryState> = { page: 1 };
              if (patch.registeredAfter !== undefined) {
                next.registeredAfter = patch.registeredAfter;
              }
              if (patch.registeredBefore !== undefined) {
                next.registeredBefore = patch.registeredBefore;
              }
              updateState(next);
            }}
          />
        </div>

        {activeFilterCount > 0 ? (
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-dashed border-[var(--border,rgba(30,64,175,0.1))] pt-3">
            <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Active
            </span>
            {stateFilter !== "" ? (
              <Tag onDismiss={() => updateState({ state: "", page: 1 })}>
                State: {stateFilter}
              </Tag>
            ) : null}
            {clearingPartnerFilter ? (
              <Tag
                onDismiss={() =>
                  updateState({ clearingPartner: "", page: 1 })
                }
              >
                Partner: {clearingPartnerFilter}
              </Tag>
            ) : null}
            {healthFilter !== "All" ? (
              <Tag onDismiss={() => updateState({ health: "All", page: 1 })}>
                Health: {healthLabel(healthFilter)}
              </Tag>
            ) : null}
            {leadPriorityFilter !== "All" ? (
              <Tag
                onDismiss={() =>
                  updateState({ leadPriority: "All", page: 1 })
                }
              >
                Priority: {priorityLabel(leadPriorityFilter)}
              </Tag>
            ) : null}
            {clearingTypeFilter !== "All" ? (
              <Tag
                onDismiss={() =>
                  updateState({ clearingType: "All", page: 1 })
                }
              >
                Type: {clearingTypeLabel(clearingTypeFilter)}
              </Tag>
            ) : null}
            {typesOfBusinessFilter.map((type) => (
              <Tag
                key={`tob-${type}`}
                onDismiss={() =>
                  updateState({
                    typesOfBusiness: typesOfBusinessFilter.filter(
                      (value) => value !== type,
                    ),
                    page: 1,
                  })
                }
              >
                Business: {type}
              </Tag>
            ))}
            {minNetCapitalFilter !== null ? (
              <Tag
                onDismiss={() =>
                  updateState({ minNetCapital: null, page: 1 })
                }
              >
                Net capital ≥ {formatCurrency(minNetCapitalFilter)}
              </Tag>
            ) : null}
            {maxNetCapitalFilter !== null ? (
              <Tag
                onDismiss={() =>
                  updateState({ maxNetCapital: null, page: 1 })
                }
              >
                Net capital ≤ {formatCurrency(maxNetCapitalFilter)}
              </Tag>
            ) : null}
            {registeredAfterFilter !== null ? (
              <Tag
                onDismiss={() =>
                  updateState({ registeredAfter: null, page: 1 })
                }
              >
                Registered after {registeredAfterFilter}
              </Tag>
            ) : null}
            {registeredBeforeFilter !== null ? (
              <Tag
                onDismiss={() =>
                  updateState({ registeredBefore: null, page: 1 })
                }
              >
                Registered before {registeredBeforeFilter}
              </Tag>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* ── Toolbar card (search + sort + page-size) ────────────────────── */}
      <div
        className="mb-4 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
        style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
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
              <Search className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]" strokeWidth={2} />
              <input
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="Firm name, CIK, CRD, or SEC file number"
                className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] outline-none placeholder:text-[var(--text-muted,#94a3b8)]"
              />
            </div>
          </form>

          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Sort by
            </label>
            <select
              value={sortBy}
              onChange={(event) =>
                updateState({ sortBy: event.target.value, page: 1 })
              }
              className="h-[38px] rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            >
              {columns.map((column) => (
                <option key={column.key} value={column.key}>
                  {column.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Direction
            </label>
            <select
              value={sortDir}
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
              value={limit}
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
          {error}
        </div>
      ) : null}

      {/* ── Table card ──────────────────────────────────────────────────── */}
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
              Broker-dealer list
            </h3>
          </div>
          <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
            {meta.total.toLocaleString()} firm{meta.total === 1 ? "" : "s"}
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1080px] text-left">
            <thead>
              <tr>
                {columns.map((column) => {
                  const isSorted = sortBy === column.key;
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
                          sortDir === "asc" ? (
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
              {loading ? (
                Array.from({ length: Math.min(limit, 8) }).map((_, index) => (
                  <tr
                    key={`loading-${index}`}
                    className="border-t border-[var(--border,rgba(30,64,175,0.1))]"
                  >
                    {columns.map((column) => (
                      <td key={column.key} className="px-5 py-3.5">
                        <div className="h-4 w-full animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : items.length === 0 ? (
                <tr>
                  <td
                    colSpan={columns.length}
                    className="px-5 py-12 text-center text-sm text-[var(--text-muted,#94a3b8)]"
                  >
                    No broker-dealers matched the current filters.
                  </td>
                </tr>
              ) : (
                items.map((item) => {
                  const hot = item.lead_priority === "hot";
                  const location = [item.city, item.state].filter(Boolean).join(", ");
                  // Hot-row stripe lives on the firm-cell <td> as a
                  // background-image so Chromium doesn't render it as a
                  // phantom <tr>::before cell that shifts every td one
                  // column right.
                  const firmCellStyle = hot
                    ? {
                        backgroundImage:
                          "linear-gradient(180deg, var(--accent, #6366f1), var(--accent-2, #8b5cf6))",
                        backgroundSize: "3px 100%",
                        backgroundRepeat: "no-repeat",
                        paddingLeft: "22px",
                      }
                    : undefined;
                  return (
                    <tr
                      key={item.id}
                      className="border-t border-[var(--border,rgba(30,64,175,0.1))] align-top transition hover:bg-[var(--row-hover,rgba(99,102,241,0.04))]"
                    >
                      <td className="min-w-[220px] px-5 py-3.5" style={firmCellStyle}>
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0 flex-1">
                            <Link
                              href={detailHref(item.id)}
                              className="block font-semibold text-[var(--text,#0f172a)] transition hover:text-[var(--accent,#6366f1)]"
                            >
                              {item.name}
                            </Link>
                            {location ? (
                              <div className="mt-0.5 text-[11px] uppercase tracking-[0.04em] text-[var(--text-muted,#94a3b8)]">
                                {location}
                              </div>
                            ) : null}
                          </div>
                          <ListPicker firmId={item.id} variant="row" />
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-5 py-3.5 font-mono text-[12px] text-[var(--text-dim,#475569)]">
                        {item.cik ?? (
                          <span title="No SEC registration on file">—</span>
                        )}
                      </td>
                      <td className="px-5 py-3.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            className="max-w-[200px] truncate text-[var(--text-dim,#475569)]"
                            title={item.current_clearing_partner ?? "Unknown"}
                          >
                            {item.current_clearing_partner ?? "Unknown"}
                          </span>
                          {item.current_clearing_is_competitor ? (
                            <Pill variant="competitor">COMPETITOR</Pill>
                          ) : null}
                        </div>
                      </td>
                      <td className="px-5 py-3.5">
                        <Pill variant={clearingTypeVariant(item.current_clearing_type)}>
                          {clearingTypeLabel(item.current_clearing_type)}
                        </Pill>
                      </td>
                      <td className="px-5 py-3.5">
                        <Pill variant={healthVariant(item.health_status)}>
                          {healthLabel(item.health_status)}
                        </Pill>
                      </td>
                      <td className="px-5 py-3.5">
                        {item.lead_priority ? (
                          <Pill variant={priorityVariant(item.lead_priority)}>
                            <Dotmark
                              halo
                              tone={
                                item.lead_priority === "hot"
                                  ? "hot"
                                  : item.lead_priority === "warm"
                                    ? "warm"
                                    : "cold"
                              }
                            />
                            {item.lead_score !== null ? item.lead_score.toFixed(0) : "—"} ·{" "}
                            {priorityLabel(item.lead_priority)}
                          </Pill>
                        ) : (
                          <span className="text-[var(--text-muted,#94a3b8)]">—</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-5 py-3.5 tabular-nums">
                        {item.latest_net_capital !== null ? (
                          formatCurrency(item.latest_net_capital)
                        ) : (
                          <span
                            className="text-[var(--text-muted,#94a3b8)]"
                            title="No FOCUS report on file"
                          >
                            —
                          </span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-5 py-3.5">
                        {item.yoy_growth !== null ? (
                          <span
                            className={`inline-flex items-center gap-1 font-semibold tabular-nums ${
                              item.yoy_growth >= 0 ? "text-[#16a34a]" : "text-[#dc2626]"
                            }`}
                          >
                            {item.yoy_growth >= 0 ? (
                              <TrendingUp className="h-3.5 w-3.5" strokeWidth={2.5} />
                            ) : (
                              <TrendingDown className="h-3.5 w-3.5" strokeWidth={2.5} />
                            )}
                            {item.yoy_growth >= 0 ? "+" : ""}
                            {item.yoy_growth.toFixed(1)}%
                          </span>
                        ) : (
                          <span
                            className="text-[var(--text-muted,#94a3b8)]"
                            title="Needs ≥2 historical FOCUS filings"
                          >
                            —
                          </span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-5 py-3.5 text-[var(--text-muted,#94a3b8)]">
                        {item.last_filing_date ?? (
                          <span title="No Edgar filing on record">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Pagination ───────────────────────────────────────────────── */}
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
            onClick={() =>
              updateState({ page: Math.max(1, meta.page - 1) })
            }
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
    </div>
  );
}
