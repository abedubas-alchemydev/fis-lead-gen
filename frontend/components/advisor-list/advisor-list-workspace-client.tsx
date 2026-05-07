"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";

import { ArrowDown, ArrowUp, Search, X } from "lucide-react";

import { apiRequest, buildApiPath } from "@/lib/api";
import { formatCurrency, formatDate, formatRelativeTime } from "@/lib/format";
import {
  ADVISOR_LIST_STATE_DEFAULTS,
  type AdvisorListQueryState,
  buildAdvisorListUrl,
  clearAllFilters,
  encodeReturnParam,
  fromSearchParams,
  hasActiveFilters,
} from "@/lib/advisor-list-state";
import { Pill } from "@/components/ui/pill";
import { Tag } from "@/components/ui/tag";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import type {
  InvestmentAdvisorListItem,
  InvestmentAdvisorListResponse,
} from "@/lib/types";

const COLUMNS = [
  { key: "name", label: "Firm Name" },
  { key: "crd_number", label: "CRD" },
  { key: "state", label: "State" },
  { key: "regulatory_aum", label: "Regulatory AUM" },
  { key: "total_clients", label: "Clients" },
  { key: "files_13f", label: "13F" },
  { key: "last_filing_date", label: "Last Filing" },
] as const;

const PAGE_SIZE_OPTIONS = [25, 50, 100] as const;

export function AdvisorListWorkspaceClient() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // URL is the source of truth — every render derives state from search
  // params so back/forward buttons and share-links round-trip cleanly.
  const state = useMemo(
    () => fromSearchParams(searchParams),
    [searchParams],
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
  const [states, setStates] = useState<string[]>([]);

  const updateState = useCallback(
    (next: Partial<AdvisorListQueryState>) => {
      const merged = { ...state, ...next };
      router.push(buildAdvisorListUrl(merged) as Route);
    },
    [router, state],
  );

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

  // One-shot fetch for the state-filter dropdown options.
  useEffect(() => {
    apiRequest<string[]>("/api/v1/investment-advisors/states")
      .then(setStates)
      .catch(() => {
        // Silent failure — empty dropdown is the graceful degraded state.
      });
  }, []);

  const items = data?.items ?? [];
  const meta = data?.meta;
  const filtersActive = hasActiveFilters(state);

  function handleSearchSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    updateState({ search: searchInput.trim(), page: 1 });
  }

  function handleClearFilters() {
    const cleared = clearAllFilters(state);
    setSearchInput("");
    router.push(buildAdvisorListUrl(cleared) as Route);
  }

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Investment Advisors
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            SEC-registered Investment Advisers that file Form 13F. Sourced
            from IAPD; AUM and advisory activities from the latest Form ADV.
          </p>
        </div>
        <ThemeToggle />
      </header>

      <section className="rounded-xl border bg-card p-4 shadow-sm">
        <form
          onSubmit={handleSearchSubmit}
          className="flex flex-wrap items-end gap-3"
        >
          <label className="flex flex-1 min-w-[260px] flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">
              Search firm name, CIK, or CRD
            </span>
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <input
                type="search"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="e.g. BlackRock, 107218"
                className="w-full rounded-md border bg-background py-2 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">
              State
            </span>
            <select
              value={state.state}
              onChange={(event) =>
                updateState({ state: event.target.value, page: 1 })
              }
              className="rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">All states</option>
              {states.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>

          <button
            type="submit"
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            Search
          </button>

          {filtersActive && (
            <button
              type="button"
              onClick={handleClearFilters}
              className="inline-flex items-center gap-1 rounded-md border px-3 py-2 text-sm hover:bg-muted"
            >
              <X className="h-3.5 w-3.5" aria-hidden /> Clear filters
            </button>
          )}
        </form>

        {filtersActive && (
          <div className="mt-3 flex flex-wrap gap-2">
            {state.search && (
              <Tag onDismiss={() => updateState({ search: "", page: 1 })}>
                Search: {state.search}
              </Tag>
            )}
            {state.state && (
              <Tag onDismiss={() => updateState({ state: "", page: 1 })}>
                State: {state.state}
              </Tag>
            )}
          </div>
        )}
      </section>

      <section className="rounded-xl border bg-card shadow-sm">
        <header className="flex items-center justify-between border-b px-4 py-3">
          <div className="flex items-baseline gap-3">
            <h2 className="text-sm font-semibold">Investment-advisor list</h2>
            {meta && (
              <span className="text-xs text-muted-foreground">
                {meta.total.toLocaleString()}{" "}
                {meta.total === 1 ? "firm" : "firms"}
                {meta.pipeline_refreshed_at && (
                  <>
                    {" · Refreshed "}
                    {formatRelativeTime(meta.pipeline_refreshed_at)}
                  </>
                )}
              </span>
            )}
          </div>
        </header>

        {error && (
          <p className="px-4 py-6 text-sm text-destructive">
            Couldn&apos;t load advisors: {error}
          </p>
        )}

        {!error && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b bg-muted/40">
                <tr>
                  {COLUMNS.map((col) => {
                    const isSorted = state.sortBy === col.key;
                    return (
                      <th
                        key={col.key}
                        className="px-3 py-2 text-left font-medium text-muted-foreground"
                      >
                        <button
                          type="button"
                          onClick={() => toggleSort(col.key)}
                          className="inline-flex items-center gap-1 hover:text-foreground"
                        >
                          {col.label}
                          {isSorted && state.sortDir === "desc" && (
                            <ArrowDown className="h-3 w-3" aria-hidden />
                          )}
                          {isSorted && state.sortDir === "asc" && (
                            <ArrowUp className="h-3 w-3" aria-hidden />
                          )}
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {loading && items.length === 0 && (
                  <tr>
                    <td
                      colSpan={COLUMNS.length}
                      className="px-3 py-8 text-center text-muted-foreground"
                    >
                      Loading…
                    </td>
                  </tr>
                )}
                {!loading && items.length === 0 && (
                  <tr>
                    <td
                      colSpan={COLUMNS.length}
                      className="px-3 py-8 text-center text-muted-foreground"
                    >
                      No advisors match the current filters.
                    </td>
                  </tr>
                )}
                {items.map((row) => (
                  <AdvisorRow key={row.id} row={row} state={state} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {meta && meta.total > 0 && (
          <footer className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-sm">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">Rows per page</span>
              <select
                value={state.limit}
                onChange={(event) =>
                  updateState({ limit: Number(event.target.value), page: 1 })
                }
                className="rounded-md border bg-background px-2 py-1 text-sm"
              >
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-muted-foreground">
                Page {meta.page} of {meta.total_pages}
              </span>
              <button
                type="button"
                disabled={meta.page <= 1}
                onClick={() => updateState({ page: meta.page - 1 })}
                className="rounded-md border px-3 py-1 disabled:opacity-50 hover:bg-muted"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={meta.page >= meta.total_pages}
                onClick={() => updateState({ page: meta.page + 1 })}
                className="rounded-md border px-3 py-1 disabled:opacity-50 hover:bg-muted"
              >
                Next
              </button>
            </div>
          </footer>
        )}
      </section>
    </div>
  );
}

function AdvisorRow({
  row,
  state,
}: {
  row: InvestmentAdvisorListItem;
  state: AdvisorListQueryState;
}) {
  // Encode the user's current filter/sort state so the detail page's
  // back-link returns them to the same view. Same envelope shape the BD
  // detail page uses.
  const returnParam = encodeReturnParam(state);
  const detailHref = (returnParam
    ? `/advisor-list/${row.id}?return=${returnParam}`
    : `/advisor-list/${row.id}`) as Route;

  return (
    <tr className="border-b last:border-0 hover:bg-muted/30">
      <td className="px-3 py-2">
        <Link
          href={detailHref}
          className="font-medium text-primary hover:underline"
        >
          {row.name}
        </Link>
        {(row.city || row.state) && (
          <div className="text-xs text-muted-foreground">
            {[row.city, row.state].filter(Boolean).join(", ")}
          </div>
        )}
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        {row.crd_number ?? "—"}
      </td>
      <td className="px-3 py-2">{row.state ?? "—"}</td>
      <td className="px-3 py-2 tabular-nums">
        {formatCurrency(row.regulatory_aum)}
      </td>
      <td className="px-3 py-2 tabular-nums">
        {row.total_clients?.toLocaleString() ?? "—"}
      </td>
      <td className="px-3 py-2">
        {row.files_13f ? (
          <Pill variant="healthy">13F</Pill>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">
        {formatDate(row.last_filing_date)}
      </td>
    </tr>
  );
}
