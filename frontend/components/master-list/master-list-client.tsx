"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { buildApiPath, apiRequest } from "@/lib/api";
import { ClearingTypeBadge } from "@/components/master-list/clearing-type-badge";
import { CompetitorBadge } from "@/components/master-list/competitor-badge";
import { HealthBadge } from "@/components/master-list/health-badge";
import { LeadPriorityBadge } from "@/components/master-list/lead-priority-badge";
import type { BrokerDealerListItem, BrokerDealerListResponse } from "@/lib/types";

/** All 50 US states + DC + territories — ensures no state is ever missing from the filter. */
const ALL_US_STATES = [
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL",
  "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
  "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
  "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "PR",
  "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "VI", "WA",
  "WV", "WI", "WY",
];

const STATE_NAMES: Record<string, string> = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California",
  CO: "Colorado", CT: "Connecticut", DE: "Delaware", DC: "District of Columbia", FL: "Florida",
  GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana",
  IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine",
  MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi",
  MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire",
  NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota",
  OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania", PR: "Puerto Rico",
  RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas",
  UT: "Utah", VT: "Vermont", VA: "Virginia", VI: "Virgin Islands", WA: "Washington",
  WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
};

const columns = [
  { key: "name", label: "Firm Name" },
  { key: "cik", label: "CIK" },
  { key: "current_clearing_partner", label: "Clearing Partner" },
  { key: "current_clearing_type", label: "Clearing Type" },
  { key: "clearing_classification", label: "Classification" },
  { key: "health_status", label: "Financial Health" },
  { key: "lead_score", label: "Lead Priority" },
  { key: "latest_net_capital", label: "Net Capital" },
  { key: "yoy_growth", label: "YoY Growth" },
  { key: "state", label: "Location" },
  { key: "last_filing_date", label: "Last Filing" }
] as const;

type MasterListClientProps = {
  initialClearingPartner?: string;
  initialClearingType?: string;
  initialLeadPriority?: string;
  initialListMode?: "primary" | "alternative" | "all";
};

export function MasterListClient({
  initialClearingPartner = "",
  initialClearingType = "All",
  initialLeadPriority = "All",
  initialListMode = "primary"
}: MasterListClientProps) {
  const [items, setItems] = useState<BrokerDealerListItem[]>([]);
  const [states, setStates] = useState<string[]>([]);
  const [statesWithData, setStatesWithData] = useState<Set<string>>(new Set());
  const [clearingPartners, setClearingPartners] = useState<string[]>([]);
  const [selectedStates, setSelectedStates] = useState<string[]>([]);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("All");
  const [healthFilter, setHealthFilter] = useState("All");
  const [leadPriorityFilter, setLeadPriorityFilter] = useState(initialLeadPriority);
  const [clearingTypeFilter, setClearingTypeFilter] = useState(initialClearingType);
  const [clearingPartnerFilter, setClearingPartnerFilter] = useState(initialClearingPartner);
  const [listMode, setListMode] = useState<"primary" | "alternative" | "all">(initialListMode);
  const [sortBy, setSortBy] = useState("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(25);
  const [meta, setMeta] = useState<BrokerDealerListResponse["meta"]>({
    page: 1,
    limit: 25,
    total: 0,
    total_pages: 1
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const queryPath = useMemo(
    () =>
      buildApiPath("/api/v1/broker-dealers", {
        search,
        state: selectedStates,
        status: statusFilter === "All" ? undefined : [statusFilter],
        health: healthFilter === "All" ? undefined : [healthFilter],
        lead_priority: leadPriorityFilter === "All" ? undefined : [leadPriorityFilter],
        clearing_partner: clearingPartnerFilter ? [clearingPartnerFilter] : undefined,
        clearing_type: clearingTypeFilter === "All" ? undefined : [clearingTypeFilter],
        list: listMode,
        sort_by: sortBy,
        sort_dir: sortDir,
        page,
        limit
      }),
    [search, selectedStates, statusFilter, healthFilter, leadPriorityFilter, clearingPartnerFilter, clearingTypeFilter, listMode, sortBy, sortDir, page, limit]
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    async function loadTable() {
      try {
        const response = await apiRequest<BrokerDealerListResponse>(queryPath);
        if (active) {
          setItems(response.items);
          setMeta(response.meta);
        }
      } catch (loadError) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Unable to load broker-dealers.");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void loadTable();
    return () => {
      active = false;
    };
  }, [queryPath]);

  useEffect(() => {
    let active = true;

    async function loadFilters() {
      try {
        const [stateResponse, partnerResponse] = await Promise.all([
          apiRequest<string[]>("/api/v1/broker-dealers/states"),
          apiRequest<string[]>("/api/v1/broker-dealers/clearing-partners")
        ]);
        if (active) {
          const dbStates = new Set(stateResponse);
          setStatesWithData(dbStates);
          // Show all states, but put states with data first
          const withData = ALL_US_STATES.filter((s) => dbStates.has(s));
          const withoutData = ALL_US_STATES.filter((s) => !dbStates.has(s));
          // Add any DB states not in our static list (international codes)
          const extra = stateResponse.filter((s) => !ALL_US_STATES.includes(s));
          setStates([...withData, ...extra, ...withoutData]);
          setClearingPartners(partnerResponse);
        }
      } catch {
        if (active) {
          setStates(ALL_US_STATES);
          setStatesWithData(new Set());
          setClearingPartners([]);
        }
      }
    }

    void loadFilters();
    return () => {
      active = false;
    };
  }, []);

  function toggleSort(columnKey: string) {
    if (sortBy === columnKey) {
      setSortDir((current) => (current === "asc" ? "desc" : "asc"));
      setPage(1);
      return;
    }

    setSortBy(columnKey);
    setSortDir("asc");
    setPage(1);
  }

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap gap-3">
        {[
          { value: "primary", label: "Primary List", helper: "Healthy lead workflow" },
          { value: "alternative", label: "Alternative List", helper: "Deficient and at-risk firms" },
          { value: "all", label: "All Firms", helper: "Unfiltered workspace" }
        ].map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => {
              setListMode(option.value as "primary" | "alternative" | "all");
              setPage(1);
            }}
            className={`rounded-[24px] border px-5 py-3 text-left transition ${
              listMode === option.value
                ? "border-navy bg-navy text-white"
                : "border-white/80 bg-white/92 text-slate-700 shadow-shell"
            }`}
          >
            <p className="text-sm font-medium">{option.label}</p>
            <p className={`mt-1 text-xs ${listMode === option.value ? "text-white/70" : "text-slate-500"}`}>{option.helper}</p>
          </button>
        ))}
      </div>

      <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="self-start rounded-[28px] border border-white/80 bg-white/92 p-5 shadow-shell xl:sticky xl:top-7">
          <p className="text-sm font-medium uppercase tracking-[0.24em] text-blue">Filters</p>
          <div className="mt-6 space-y-5">
            <div>
              <label className="text-sm font-medium text-slate-700">States</label>
              <select
                multiple
                value={selectedStates}
                onChange={(event) => {
                  const next = Array.from(event.target.selectedOptions).map((option) => option.value);
                  setSelectedStates(next);
                  setPage(1);
                }}
                className="mt-2 min-h-48 w-full rounded-2xl border border-slate-200 bg-white px-3 py-3 text-sm"
              >
                {states.map((stateValue) => {
                  const hasData = statesWithData.has(stateValue);
                  const label = STATE_NAMES[stateValue] ?? stateValue;
                  return (
                    <option key={stateValue} value={stateValue} disabled={!hasData} className={hasData ? "" : "text-slate-400"}>
                      {label} ({stateValue}){hasData ? "" : " -- no firms"}
                    </option>
                  );
                })}
              </select>
              <p className="mt-2 text-xs text-slate-500">Hold Ctrl/Cmd to select multiple states.</p>
            </div>

            <div>
              <p className="text-sm font-medium text-slate-700">Registration Status</p>
              <div className="mt-3 space-y-2">
                {["All", "Active", "Inactive"].map((option) => (
                  <label key={option} className="flex items-center gap-3 rounded-2xl border border-slate-200 px-3 py-3 text-sm">
                    <input
                      type="radio"
                      name="status-filter"
                      value={option}
                      checked={statusFilter === option}
                      onChange={(event) => {
                        setStatusFilter(event.target.value);
                        setPage(1);
                      }}
                    />
                    <span>{option}</span>
                  </label>
                ))}
              </div>
            </div>

            <div>
              <p className="text-sm font-medium text-slate-700">Financial Health</p>
              <div className="mt-3 space-y-2">
                {[
                  { value: "All", label: "All" },
                  { value: "healthy", label: "Healthy" },
                  { value: "ok", label: "OK" },
                  { value: "at_risk", label: "At Risk" }
                ].map((option) => (
                  <label key={option.value} className="flex items-center gap-3 rounded-2xl border border-slate-200 px-3 py-3 text-sm">
                    <input
                      type="radio"
                      name="health-filter"
                      value={option.value}
                      checked={healthFilter === option.value}
                      onChange={(event) => {
                        setHealthFilter(event.target.value);
                        setPage(1);
                      }}
                    />
                    <span>{option.label}</span>
                  </label>
                ))}
              </div>
            </div>

            <div>
              <p className="text-sm font-medium text-slate-700">Lead Priority</p>
              <div className="mt-3 space-y-2">
                {[
                  { value: "All", label: "All" },
                  { value: "hot", label: "Hot" },
                  { value: "warm", label: "Warm" },
                  { value: "cold", label: "Cold" }
                ].map((option) => (
                  <label key={option.value} className="flex items-center gap-3 rounded-2xl border border-slate-200 px-3 py-3 text-sm">
                    <input
                      type="radio"
                      name="lead-priority-filter"
                      value={option.value}
                      checked={leadPriorityFilter === option.value}
                      onChange={(event) => {
                        setLeadPriorityFilter(event.target.value);
                        setPage(1);
                      }}
                    />
                    <span>{option.label}</span>
                  </label>
                ))}
              </div>
            </div>

            <label className="block text-sm font-medium text-slate-700">
              Clearing partner
              <select
                value={clearingPartnerFilter}
                onChange={(event) => {
                  setClearingPartnerFilter(event.target.value);
                  setPage(1);
                }}
                className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
              >
                <option value="">All providers</option>
                {clearingPartners.map((partner) => (
                  <option key={partner} value={partner}>
                    {partner}
                  </option>
                ))}
              </select>
            </label>

            <div>
              <p className="text-sm font-medium text-slate-700">Clearing type</p>
              <div className="mt-3 space-y-2">
                {[
                  { value: "All", label: "All" },
                  { value: "fully_disclosed", label: "Fully Disclosed" },
                  { value: "self_clearing", label: "Self-Clearing" },
                  { value: "omnibus", label: "Omnibus" },
                  { value: "unknown", label: "Unknown" }
                ].map((option) => (
                  <label key={option.value} className="flex items-center gap-3 rounded-2xl border border-slate-200 px-3 py-3 text-sm">
                    <input
                      type="radio"
                      name="clearing-type-filter"
                      value={option.value}
                      checked={clearingTypeFilter === option.value}
                      onChange={(event) => {
                        setClearingTypeFilter(event.target.value);
                        setPage(1);
                      }}
                    />
                    <span>{option.label}</span>
                  </label>
                ))}
              </div>
            </div>

            <button
              type="button"
              onClick={() => {
                setSelectedStates([]);
                setSearch("");
                setSearchInput("");
                setStatusFilter("All");
                setHealthFilter("All");
                setLeadPriorityFilter("All");
                setClearingPartnerFilter("");
                setClearingTypeFilter("All");
                setSortBy("name");
                setSortDir("asc");
                setPage(1);
                setLimit(25);
              }}
              className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
            >
              Clear filters
            </button>
          </div>
        </aside>

        <div className="min-w-0 space-y-5">
          <div className="rounded-[28px] border border-white/80 bg-white/92 p-5 shadow-shell">
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(340px,1fr)] xl:items-end">
              <form
                className="flex min-w-0 flex-col gap-3 sm:flex-row"
                onSubmit={(event) => {
                  event.preventDefault();
                  setSearch(searchInput.trim());
                  setPage(1);
                }}
              >
                <div className="min-w-0 flex-1">
                  <label className="text-sm font-medium text-slate-700">Search firms</label>
                  <input
                    value={searchInput}
                    onChange={(event) => setSearchInput(event.target.value)}
                    placeholder="Search by firm name, CIK, CRD, or SEC file number"
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none focus:border-blue"
                  />
                </div>
                <button
                  type="submit"
                  className="h-[50px] shrink-0 rounded-2xl bg-navy px-5 text-sm font-medium text-white transition hover:bg-[#112b54] sm:self-end"
                >
                  Search
                </button>
              </form>

              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                <label className="text-sm font-medium text-slate-700">
                  Sort by
                  <select
                    value={sortBy}
                    onChange={(event) => {
                      setSortBy(event.target.value);
                      setPage(1);
                    }}
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
                  >
                    {columns.map((column) => (
                      <option key={column.key} value={column.key}>
                        {column.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-sm font-medium text-slate-700">
                  Direction
                  <select
                    value={sortDir}
                    onChange={(event) => {
                      setSortDir(event.target.value as "asc" | "desc");
                      setPage(1);
                    }}
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
                  >
                    <option value="asc">Ascending</option>
                    <option value="desc">Descending</option>
                  </select>
                </label>
                <label className="text-sm font-medium text-slate-700">
                  Page size
                  <select
                    value={limit}
                    onChange={(event) => {
                      setLimit(Number(event.target.value));
                      setPage(1);
                    }}
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
                  >
                    {[25, 50, 100].map((pageSize) => (
                      <option key={pageSize} value={pageSize}>
                        {pageSize}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
          </div>

          <div className="min-w-0 overflow-hidden rounded-[28px] border border-white/80 bg-white/92 shadow-shell">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-5 py-4">
              <div>
                <p className="text-sm font-medium uppercase tracking-[0.22em] text-blue">Master List</p>
                <p className="mt-1 text-sm text-slate-600">{meta.total.toLocaleString()} broker-dealers loaded</p>
              </div>
              <p className="text-sm text-slate-500">
                Page {meta.page} of {meta.total_pages}
              </p>
            </div>

            {error ? <div className="px-5 py-6 text-sm text-danger">{error}</div> : null}

            <div className="overflow-x-auto">
              <table className="min-w-[1260px] w-full text-left">
                <thead className="bg-slate-50">
                  <tr>
                    {columns.map((column) => (
                      <th
                        key={column.key}
                        className="whitespace-nowrap px-5 py-4 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500"
                      >
                        <button type="button" className="transition hover:text-navy" onClick={() => toggleSort(column.key)}>
                          {column.label}
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    Array.from({ length: Math.min(limit, 8) }).map((_, index) => (
                      <tr key={`loading-${index}`} className="border-t border-slate-100">
                        {columns.map((column) => (
                          <td key={column.key} className="px-5 py-4">
                            <div className="h-4 w-full animate-pulse rounded bg-slate-100" />
                          </td>
                        ))}
                      </tr>
                    ))
                  ) : items.length === 0 ? (
                    <tr>
                      <td colSpan={columns.length} className="px-5 py-10 text-center text-sm text-slate-500">
                        No broker-dealers matched the current filters.
                      </td>
                    </tr>
                  ) : (
                    items.map((item) => (
                      <tr
                        key={item.id}
                        className={`border-t border-slate-100 hover:bg-slate-50/80 ${
                          item.lead_priority === "hot" ? "border-l-4 border-l-gold" : ""
                        }`}
                      >
                        <td className="px-5 py-4 text-sm font-medium text-navy">
                          <Link href={`/master-list/${item.id}`} className="block max-w-[220px] whitespace-normal break-words hover:text-blue">
                            {item.name}
                          </Link>
                          {item.is_deficient ? (
                            <span className="mt-2 inline-flex rounded-full bg-red-100 px-2.5 py-1 text-xs font-medium text-danger">
                              Alternative List
                            </span>
                          ) : null}
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 font-mono text-sm text-slate-700">{item.cik ?? "—"}</td>
                        <td className="px-5 py-4 text-sm text-slate-700">
                          <div className="space-y-2">
                            <p className="max-w-[180px] whitespace-normal break-words">{item.current_clearing_partner ?? "Unknown"}</p>
                            <CompetitorBadge isCompetitor={item.current_clearing_is_competitor} />
                          </div>
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-700">
                          <ClearingTypeBadge type={item.current_clearing_type} />
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-700">
                          <div className="flex flex-wrap gap-1">
                            {item.clearing_classification === "true_self_clearing" ? (
                              <span className="inline-flex rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-700">
                                Self-Clearing
                              </span>
                            ) : item.clearing_classification === "introducing" ? (
                              <span className="inline-flex rounded-full bg-blue-100 px-2.5 py-1 text-xs font-medium text-blue-700">
                                Introducing
                              </span>
                            ) : (
                              <span className="text-xs text-slate-400">--</span>
                            )}
                            {item.is_niche_restricted ? (
                              <span className="inline-flex rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-700">Niche</span>
                            ) : null}
                          </div>
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-700">
                          <HealthBadge status={item.health_status} />
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-700">
                          <LeadPriorityBadge priority={item.lead_priority} score={item.lead_score} />
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 text-sm text-slate-700">
                          {item.latest_net_capital !== null
                            ? new Intl.NumberFormat("en-US", {
                                style: "currency",
                                currency: "USD",
                                maximumFractionDigits: 0
                              }).format(item.latest_net_capital)
                            : "N/A"}
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 text-sm">
                          {item.yoy_growth !== null ? (
                            <span className={item.yoy_growth >= 0 ? "text-success" : "text-danger"}>
                              {item.yoy_growth >= 0 ? "+" : ""}
                              {item.yoy_growth.toFixed(1)}%
                            </span>
                          ) : (
                            <span className="text-slate-500">N/A</span>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 text-sm text-slate-700">
                          {[item.city, item.state].filter(Boolean).join(", ") || "-"}
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 text-sm text-slate-700">{item.last_filing_date ?? "-"}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="flex flex-col gap-3 rounded-[24px] border border-white/80 bg-white/92 p-4 shadow-shell sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-slate-600">
              Showing {meta.total === 0 ? 0 : (meta.page - 1) * meta.limit + 1}-
              {meta.total === 0 ? 0 : Math.min(meta.page * meta.limit, meta.total)} of {meta.total.toLocaleString()}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={meta.page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                className="rounded-2xl border border-slate-200 px-4 py-2 text-sm disabled:opacity-50"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={meta.page >= meta.total_pages}
                onClick={() => setPage((current) => Math.min(meta.total_pages, current + 1))}
                className="rounded-2xl border border-slate-200 px-4 py-2 text-sm disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
