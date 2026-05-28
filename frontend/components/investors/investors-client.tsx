"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";

import {
  AlertTriangle,
  ArrowUpRight,
  Loader2,
  MailPlus,
  Phone,
  Search,
  X,
} from "lucide-react";

import { apiRequest, buildApiPath, enrichInvestor } from "@/lib/api";
import { formatCurrency, formatDate } from "@/lib/format";
import {
  INVESTORS_STATE_DEFAULTS,
  type InvestorTab,
  type InvestorsQueryState,
  buildInvestorsUrl,
  clearAllFilters,
  fromSearchParams,
  hasActiveFilters,
} from "@/lib/investors-state";
import { STATE_NAMES, stateCodeFromName } from "@/lib/states";
import { Combo } from "@/components/ui/combo";
import { ListPicker } from "@/components/list-picker/list-picker";
import { Tag } from "@/components/ui/tag";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { TransactionValueRangeFilter } from "@/components/investors/filters/transaction-value-range-filter";
import type {
  InvestorEnrichResponse,
  InvestorItem,
  InvestorListResponse,
} from "@/lib/types";

// ── Tab catalog ───────────────────────────────────────────────────────
// Deshorn (2026-05-12 meeting transcript): two lists, one for
// acquired/buyers (A) and one for disposed/sellers (D). Plus a merged
// "All" tab so the team can scan both in chronological order. Default
// lands on Buyers — that's the primary potential-investor pool.
const TAB_CATALOG: ReadonlyArray<{ value: InvestorTab; label: string }> = [
  { value: "buyers", label: "Buyers (A)" },
  { value: "sellers", label: "Sellers (D)" },
  { value: "all", label: "All" },
];

const DAYS_OPTIONS = [
  { value: 30, label: "Last 30 days" },
  { value: 90, label: "Last 90 days" },
  { value: 180, label: "Last 180 days" },
  { value: 365, label: "Last year" },
] as const;

// Sort options shown in the toolbar Combo. Backed by the BE-recognized
// sort_by keys in /api/v1/investors (whitelisted in
// services/form4_transactions.ALLOWED_SORT_FIELDS).
const SORT_OPTIONS = [
  { key: "transaction_date", label: "Transaction Date" },
  { key: "transaction_value", label: "Transaction Value" },
  { key: "shares", label: "Shares" },
  { key: "issuer_ticker", label: "Ticker" },
  { key: "reporting_owner_name", label: "Owner Name" },
] as const;

// ── Pagination helper ─────────────────────────────────────────────────
// Mirrors the master-list / advisor-list helper. Produces
// [1, 2, 3, …, last] with ellipses as string literals so React can key
// them distinctly.
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

function countActiveFilters(state: InvestorsQueryState): number {
  let n = 0;
  if (state.search !== INVESTORS_STATE_DEFAULTS.search) n += 1;
  if (state.ticker !== INVESTORS_STATE_DEFAULTS.ticker) n += 1;
  if (state.state !== INVESTORS_STATE_DEFAULTS.state) n += 1;
  if (state.days !== INVESTORS_STATE_DEFAULTS.days) n += 1;
  if (state.minValue !== null || state.maxValue !== null) n += 1;
  return n;
}

export function InvestorsClient() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const state = useMemo(
    () => fromSearchParams(searchParams),
    [searchParams],
  );

  const updateState = useCallback(
    (next: Partial<InvestorsQueryState>) => {
      const merged = { ...state, ...next };
      router.replace(buildInvestorsUrl(merged) as Route, { scroll: false });
    },
    [router, state],
  );

  // Local unsubmitted search/ticker draft state. URL only updates on
  // Enter (or blur) so typing doesn't churn URL/fetches every keystroke.
  const [searchInput, setSearchInput] = useState(state.search);
  const [tickerDraft, setTickerDraft] = useState(state.ticker);

  useEffect(() => {
    setSearchInput(state.search);
  }, [state.search]);
  useEffect(() => {
    setTickerDraft(state.ticker);
  }, [state.ticker]);

  const [items, setItems] = useState<InvestorItem[]>([]);
  const [meta, setMeta] = useState<InvestorListResponse["meta"]>({
    page: 1,
    limit: state.limit,
    total: 0,
    total_pages: 1,
  });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [enrichingId, setEnrichingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [stateOptions, setStateOptions] = useState<string[]>([]);
  const [tabCounts, setTabCounts] = useState<Record<InvestorTab, number | null>>(
    { buyers: null, sellers: null, all: null },
  );

  // Build query params for the main list fetch + the per-tab count fetches.
  // The per-tab fetches omit `tab` (overridden inline) and `page`/`limit`
  // (forced to 1) so we get a cheap total count without paging.
  const buildBaseParams = useCallback(
    (current: InvestorsQueryState): Record<string, string | number | string[]> => {
      const params: Record<string, string | number | string[]> = {};
      if (current.search) params.q = current.search;
      if (current.ticker) params.ticker = current.ticker;
      if (current.state) {
        // STATE_NAMES emits full names ("California"); BE expects 2-letter
        // ("CA"). stateCodeFromName falls through for already-2-letter input.
        params.state = [stateCodeFromName(current.state) ?? current.state];
      }
      if (current.days !== INVESTORS_STATE_DEFAULTS.days) params.days = current.days;
      if (current.minValue !== null) params.min_value = current.minValue;
      if (current.maxValue !== null) params.max_value = current.maxValue;
      return params;
    },
    [],
  );

  const queryPath = useMemo(() => {
    const params = buildBaseParams(state);
    if (state.tab !== "all") params.tab = state.tab;
    params.sort_by = state.sortBy;
    params.sort_dir = state.sortDir;
    params.page = state.page;
    params.limit = state.limit;
    return buildApiPath("/api/v1/investors", params);
  }, [
    buildBaseParams,
    state,
  ]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(null);

    async function load() {
      try {
        const response = await apiRequest<InvestorListResponse>(queryPath);
        if (!active) return;
        setItems(response.items);
        setMeta(response.meta);
      } catch (e) {
        if (!active) return;
        setItems([]);
        setLoadError(
          e instanceof Error ? e.message : "Unable to load investors.",
        );
      } finally {
        if (active) setLoading(false);
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, [queryPath, reloadKey]);

  // One-shot State Combo options. Form 4 stores 2-letter codes; we map
  // them to the FE's STATE_NAMES dictionary for display in the dropdown.
  useEffect(() => {
    let active = true;
    apiRequest<string[]>("/api/v1/investors/states")
      .then((codes) => {
        if (!active) return;
        // STATE_NAMES is keyed on full names. Build a code → name lookup
        // by reversing it. Codes not in the catalog (rare territories)
        // fall through as the raw code so they still appear in the list.
        const codeToName = new Map<string, string>();
        for (const name of STATE_NAMES) {
          const code = stateCodeFromName(name);
          if (code) codeToName.set(code, name);
        }
        const names = codes.map((c) => codeToName.get(c) ?? c);
        // Sort by display name so the Combo matches master-list / advisor-list.
        names.sort((a, b) => a.localeCompare(b));
        setStateOptions(names);
      })
      .catch(() => {
        if (active) setStateOptions(STATE_NAMES.slice());
      });
    return () => {
      active = false;
    };
  }, []);

  // Per-tab count badges. Mirrors the master-list list-mode pattern: a
  // light limit=1 fetch per tab, re-fired whenever the filter inputs
  // change (but NOT when only the tab itself changes, since that wouldn't
  // affect counts).
  useEffect(() => {
    let active = true;
    async function loadCounts() {
      const base = buildBaseParams(state);
      const [buyers, sellers, all] = await Promise.allSettled([
        apiRequest<InvestorListResponse>(
          buildApiPath("/api/v1/investors", { ...base, tab: "buyers", limit: 1 }),
        ),
        apiRequest<InvestorListResponse>(
          buildApiPath("/api/v1/investors", { ...base, tab: "sellers", limit: 1 }),
        ),
        apiRequest<InvestorListResponse>(
          buildApiPath("/api/v1/investors", { ...base, limit: 1 }),
        ),
      ]);
      if (!active) return;
      setTabCounts({
        buyers: buyers.status === "fulfilled" ? buyers.value.meta.total : null,
        sellers:
          sellers.status === "fulfilled" ? sellers.value.meta.total : null,
        all: all.status === "fulfilled" ? all.value.meta.total : null,
      });
    }
    void loadCounts();
    return () => {
      active = false;
    };
    // Intentionally excludes state.tab + state.sortBy + state.sortDir +
    // state.page + state.limit — counts only depend on the filter inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    buildBaseParams,
    state.search,
    state.ticker,
    state.state,
    state.days,
    state.minValue,
    state.maxValue,
  ]);

  function handleClearFilters() {
    setSearchInput("");
    setTickerDraft("");
    router.replace(buildInvestorsUrl(clearAllFilters(state)) as Route, {
      scroll: false,
    });
  }

  async function handleEnrich(id: number) {
    setEnrichingId(id);
    setError(null);
    try {
      const result: InvestorEnrichResponse = await enrichInvestor(id);
      setItems((current) =>
        current.map((row) =>
          row.id === id
            ? {
                ...row,
                enriched_phone: result.enriched_phone,
                enriched_email: result.enriched_email,
                enriched_at: result.enriched_at,
              }
            : row,
        ),
      );
      if (!result.matched) {
        setError(
          result.skip_reason === "entity_filer"
            ? "Entity filer — Apollo people-match doesn't apply to organizations."
            : "No contact match returned by Apollo for this person.",
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Enrichment failed.");
    } finally {
      setEnrichingId(null);
    }
  }

  // Group rows by ticker for the feed layout. The BE returns one row per
  // (issuer, person, ad_code); we collapse runs of identical ticker into
  // one header. Under sort_by=reporting_owner_name the grouping naturally
  // scatters — by design.
  const grouped = useMemo(() => {
    const out: Array<{ ticker: string | null; rows: InvestorItem[] }> = [];
    for (const row of items) {
      const key = row.issuer_ticker || null;
      const last = out[out.length - 1];
      if (last && last.ticker === key) {
        last.rows.push(row);
      } else {
        out.push({ ticker: key, rows: [row] });
      }
    }
    return out;
  }, [items]);

  const activeFilterCount = countActiveFilters(state);
  const filtersActive = hasActiveFilters(state);
  const daysLabel =
    DAYS_OPTIONS.find((option) => option.value === state.days)?.label ??
    `Last ${state.days} days`;

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* ── Topbar ──────────────────────────────────────────────────────── */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Investors
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Insider transactions feed
          </h1>
          <p className="mt-1 max-w-[640px] text-[12px] text-[var(--text-muted,#94a3b8)]">
            Sourced from SEC Form 4 filings. Reporting persons only — issuers
            are intentionally excluded.
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
              placeholder="Search insider, issuer, or ticker…"
              aria-label="Search insider transactions"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] outline-none placeholder:text-[var(--text-muted,#94a3b8)]"
            />
            <kbd className="rounded-[4px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-3,#dbeafe)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-dim,#475569)]">
              ⌘K
            </kbd>
          </form>
          <ThemeToggle />
        </div>
      </div>

      {/* ── Tabs (URL-backed) ───────────────────────────────────────────── */}
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <div
          role="tablist"
          aria-label="Investor list"
          className="inline-flex rounded-[12px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] p-1"
        >
          {TAB_CATALOG.map((entry) => {
            const active = state.tab === entry.value;
            const count = tabCounts[entry.value];
            return (
              <button
                key={entry.value}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => updateState({ tab: entry.value, page: 1 })}
                className={`inline-flex items-center gap-2 rounded-[10px] px-4 py-2 text-[13px] transition ${
                  active
                    ? "bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)]"
                    : "font-medium text-[var(--text-dim,#475569)] hover:bg-[var(--surface,#ffffff)] hover:text-[var(--text,#0f172a)]"
                }`}
              >
                {entry.label}
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
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          <span aria-hidden className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          {meta.total.toLocaleString()} match{meta.total === 1 ? "" : "es"}
        </span>
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
              Refine the feed
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
              Ticker
            </label>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                updateState({ ticker: tickerDraft.trim(), page: 1 });
              }}
            >
              <input
                value={tickerDraft}
                onChange={(event) => setTickerDraft(event.target.value)}
                onBlur={() => {
                  if (tickerDraft.trim() !== state.ticker) {
                    updateState({ ticker: tickerDraft.trim(), page: 1 });
                  }
                }}
                placeholder="AAPL"
                aria-label="Issuer ticker"
                className="h-[38px] w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] uppercase tracking-[0.04em] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
              />
            </form>
          </div>
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Window
            </label>
            <select
              value={state.days}
              onChange={(event) =>
                updateState({ days: Number(event.target.value), page: 1 })
              }
              className="h-[38px] w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            >
              {DAYS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Insider state
            </label>
            <Combo
              value={state.state}
              onChange={(next) => updateState({ state: next, page: 1 })}
              options={stateOptions.length > 0 ? stateOptions : STATE_NAMES}
              placeholder="Search states…"
              emptyLabel="All states"
              ariaLabel="Insider state"
            />
          </div>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <TransactionValueRangeFilter
            min={state.minValue}
            max={state.maxValue}
            onChange={(patch) =>
              updateState({
                ...(patch.min !== undefined ? { minValue: patch.min } : {}),
                ...(patch.max !== undefined ? { maxValue: patch.max } : {}),
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
            {state.ticker !== "" ? (
              <Tag
                onDismiss={() => {
                  setTickerDraft("");
                  updateState({ ticker: "", page: 1 });
                }}
              >
                Ticker: {state.ticker}
              </Tag>
            ) : null}
            {state.state !== "" ? (
              <Tag onDismiss={() => updateState({ state: "", page: 1 })}>
                State: {state.state}
              </Tag>
            ) : null}
            {state.days !== INVESTORS_STATE_DEFAULTS.days ? (
              <Tag
                onDismiss={() =>
                  updateState({
                    days: INVESTORS_STATE_DEFAULTS.days,
                    page: 1,
                  })
                }
              >
                Window: {daysLabel}
              </Tag>
            ) : null}
            {state.minValue !== null ? (
              <Tag onDismiss={() => updateState({ minValue: null, page: 1 })}>
                Value ≥ {formatCurrency(state.minValue)}
              </Tag>
            ) : null}
            {state.maxValue !== null ? (
              <Tag onDismiss={() => updateState({ maxValue: null, page: 1 })}>
                Value ≤ {formatCurrency(state.maxValue)}
              </Tag>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* ── Toolbar (search + sort + dir + page-size) ───────────────────── */}
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
              Search transactions
            </label>
            <div className="flex h-[38px] w-full items-center gap-2.5 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 transition focus-within:border-[var(--accent,#6366f1)] focus-within:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]">
              <Search
                className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
                strokeWidth={2}
              />
              <input
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="Insider, issuer, or ticker"
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
        <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {error}
        </div>
      ) : null}

      {/* ── List card ───────────────────────────────────────────────────── */}
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
              Form 4 reporting persons
            </p>
            <h3 className="mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              {state.tab === "buyers"
                ? "Acquired (buyers)"
                : state.tab === "sellers"
                  ? "Disposed (sellers)"
                  : "All transactions"}
            </h3>
          </div>
          <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
            {meta.total.toLocaleString()} row{meta.total === 1 ? "" : "s"}
          </span>
        </div>

        <div className="px-5 py-2">
          {loading ? (
            <LoadingSkeleton />
          ) : loadError ? (
            <LoadErrorCard
              message={loadError}
              onRetry={() => setReloadKey((k) => k + 1)}
            />
          ) : items.length === 0 ? (
            <EmptyState />
          ) : (
            <div>
              {grouped.map((group, idx) => (
                <div
                  key={`${group.ticker ?? "no-ticker"}-${idx}`}
                  className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-3 first:border-t-0"
                >
                  <div className="mb-2 flex items-baseline gap-2">
                    <span className="rounded-md bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[12px] font-bold tracking-[0.04em] text-[#312e81]">
                      {group.ticker ?? "—"}
                    </span>
                    <span className="text-[12px] text-[var(--text-dim,#475569)]">
                      {group.rows[0].issuer_name}
                    </span>
                    <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
                      {group.rows.length} insider
                      {group.rows.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <div className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
                    {group.rows.map((row) => (
                      <InvestorRow
                        key={row.id}
                        row={row}
                        enriching={enrichingId === row.id}
                        onEnrich={() => void handleEnrich(row.id)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Pagination (numbered) ───────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">
          Showing {meta.total === 0 ? 0 : (meta.page - 1) * meta.limit + 1}–
          {meta.total === 0 ? 0 : Math.min(meta.page * meta.limit, meta.total)}{" "}
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
    </div>
  );
}

function InvestorRow({
  row,
  enriching,
  onEnrich,
}: {
  row: InvestorItem;
  enriching: boolean;
  onEnrich: () => void;
}) {
  const adChipClass =
    row.ad_code === "A"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : "border-red-200 bg-red-50 text-red-700";
  const adLabel = row.ad_code === "A" ? "Acquired" : "Disposed";
  const address = [
    row.reporting_owner_street1,
    row.reporting_owner_street2,
    [
      row.reporting_owner_city,
      row.reporting_owner_state,
      row.reporting_owner_zip,
    ]
      .filter((s) => s && s.trim().length > 0)
      .join(", "),
  ]
    .filter((s) => s && s.trim().length > 0)
    .join(" • ");
  const hasEnrichment = !!row.enriched_at;

  return (
    <div className="grid gap-3 py-3 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.2fr)_minmax(0,160px)]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[14px] font-semibold text-[var(--text,#0f172a)]">
            {row.reporting_owner_name}
          </span>
          {row.reporting_owner_title ? (
            <span className="text-[12px] text-[var(--text-dim,#475569)]">
              {row.reporting_owner_title}
            </span>
          ) : null}
          <span
            className={`rounded-md border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] ${adChipClass}`}
          >
            {adLabel}
          </span>
        </div>
        <p className="mt-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
          {address || "No address on filing"}
        </p>
        {hasEnrichment ? (
          <div className="mt-1.5 flex flex-wrap gap-3 text-[12px] text-[var(--text-dim,#475569)]">
            {row.enriched_phone ? (
              <span className="inline-flex items-center gap-1">
                <Phone className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
                {row.enriched_phone}
              </span>
            ) : null}
            {row.enriched_email ? (
              <a
                href={`mailto:${row.enriched_email}`}
                className="inline-flex items-center gap-1 text-[#6366f1] hover:underline"
              >
                <MailPlus className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
                {row.enriched_email}
              </a>
            ) : null}
            {!row.enriched_phone && !row.enriched_email && !row.is_entity ? (
              <span className="text-[11px] italic text-[var(--text-muted,#94a3b8)]">
                Apollo returned no match
              </span>
            ) : null}
          </div>
        ) : null}
        {row.is_entity ? (
          <p className="mt-1.5 text-[11px] italic text-[var(--text-muted,#94a3b8)]">
            Entity filer — Apollo people-match doesn&apos;t apply to organizations
          </p>
        ) : null}
      </div>
      <div className="min-w-0 text-[12px] text-[var(--text-dim,#475569)]">
        <div>
          <span className="font-semibold text-[var(--text,#0f172a)]">
            {formatCurrency(row.transaction_value)}
          </span>
          <span className="ml-1 text-[var(--text-muted,#94a3b8)]">
            ({row.shares?.toLocaleString() ?? "—"} sh
            {row.txn_count === 1 && row.price_per_share
              ? ` @ ${formatCurrency(row.price_per_share)}`
              : ""}
            )
          </span>
        </div>
        <div className="text-[11px] text-[var(--text-muted,#94a3b8)]">
          {formatDate(row.transaction_date)} • {row.security_title ?? "—"}
        </div>
        {row.source_filing_url ? (
          <a
            href={row.source_filing_url}
            target="_blank"
            rel="noreferrer"
            className="mt-1 inline-flex items-center gap-1 text-[11px] font-semibold text-[#6366f1] hover:underline"
          >
            View Form 4 <ArrowUpRight className="h-3 w-3" strokeWidth={2} />
          </a>
        ) : null}
      </div>
      <div className="flex items-start justify-end gap-2">
        <ListPicker
          variant="row-heart"
          entityType="reporting_owner"
          firmId={row.reporting_owner_id ?? 0}
          reportingOwnerCik={row.reporting_owner_cik}
          initialFavorited={row.is_favorited}
        />
        <button
          type="button"
          onClick={onEnrich}
          disabled={enriching || row.is_entity}
          title={
            row.is_entity
              ? "Entity filer — Apollo people-match doesn't apply to organizations"
              : undefined
          }
          className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {enriching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
          ) : null}
          {hasEnrichment ? "Re-enrich" : "Find contact"}
        </button>
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3 py-4">
      {[0, 1, 2, 3].map((idx) => (
        <div
          key={idx}
          className="h-16 animate-pulse rounded-xl bg-[var(--surface-2,#f1f6fd)]"
        />
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="my-6 rounded-2xl border border-dashed border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <Link
        href={"/settings/pipelines" as Route}
        className="text-[14px] font-semibold text-[var(--text,#0f172a)] hover:text-[#6366f1]"
      >
        No Form 4 transactions yet
      </Link>
      <p className="mx-auto mt-2 max-w-md text-[12px] text-[var(--text-dim,#475569)]">
        Run the Form 4 watcher from Pipelines, or wait for the daily cron to
        populate the feed. Empty results may also mean no filings cleared the
        current value floor in the selected window.
      </p>
    </div>
  );
}

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
        Couldn&apos;t load investors
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        {message}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-5 inline-flex h-[34px] items-center rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-4 text-[13px] font-semibold text-[var(--text-dim,#475569)] transition hover:text-[var(--text,#0f172a)]"
      >
        Retry
      </button>
    </div>
  );
}
