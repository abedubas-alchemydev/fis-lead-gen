// URL <-> component-state mapping for the /investors workspace.
//
// Sibling of frontend/lib/master-list-state.ts and
// frontend/lib/advisor-list-state.ts. Same conventions: every filter,
// the active tab, the sort, and pagination live in URL search params so
// back-nav, share-links, and reloads all restore the same view.
//
// Param-key contract aligns with the BE filter vocabulary
// (q / tab / ticker / state / days / min_value / max_value / sort_by /
// sort_dir / page / limit) so a debugging session can copy-paste
// between the BE API URL and the FE URL without translation.

export type InvestorTab = "buyers" | "sellers" | "all";
export type InvestorSortDir = "asc" | "desc";

export interface InvestorsQueryState {
  tab: InvestorTab;
  search: string;
  ticker: string;
  state: string;
  days: number;
  // Dollars (not cents). null when the filter is unset.
  minValue: number | null;
  maxValue: number | null;
  sortBy: string;
  sortDir: InvestorSortDir;
  page: number;
  limit: number;
}

export const INVESTORS_STATE_DEFAULTS: InvestorsQueryState = {
  tab: "buyers",
  search: "",
  ticker: "",
  state: "",
  days: 90,
  minValue: null,
  maxValue: null,
  sortBy: "transaction_date",
  sortDir: "desc",
  page: 1,
  limit: 25,
};

const TAB_VALUES: ReadonlyArray<InvestorTab> = ["buyers", "sellers", "all"];
const SORT_DIRS: ReadonlyArray<InvestorSortDir> = ["asc", "desc"];
const ALLOWED_LIMITS: ReadonlyArray<number> = [25, 50, 100];
const ALLOWED_DAYS: ReadonlyArray<number> = [30, 90, 180, 365];

type SearchParamsLike = {
  get(name: string): string | null;
  getAll(name: string): string[];
};

function parseIntInRange(
  raw: string | null,
  fallback: number,
  min: number,
): number {
  if (raw === null) return fallback;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed < min) return fallback;
  return parsed;
}

function parseNonNegativeFloat(raw: string | null): number | null {
  if (raw === null || raw === "") return null;
  const parsed = Number.parseFloat(raw);
  if (!Number.isFinite(parsed) || parsed < 0) return null;
  return parsed;
}

export function fromSearchParams(sp: SearchParamsLike): InvestorsQueryState {
  const tabRaw = sp.get("tab");
  const sortDirRaw = sp.get("sort_dir");
  const daysRaw = parseIntInRange(
    sp.get("days"),
    INVESTORS_STATE_DEFAULTS.days,
    1,
  );
  const limit = parseIntInRange(
    sp.get("limit"),
    INVESTORS_STATE_DEFAULTS.limit,
    1,
  );

  return {
    tab:
      tabRaw && (TAB_VALUES as ReadonlyArray<string>).includes(tabRaw)
        ? (tabRaw as InvestorTab)
        : INVESTORS_STATE_DEFAULTS.tab,
    search: sp.get("q") ?? INVESTORS_STATE_DEFAULTS.search,
    ticker: sp.get("ticker") ?? INVESTORS_STATE_DEFAULTS.ticker,
    state: sp.get("state") ?? INVESTORS_STATE_DEFAULTS.state,
    days: (ALLOWED_DAYS as ReadonlyArray<number>).includes(daysRaw)
      ? daysRaw
      : INVESTORS_STATE_DEFAULTS.days,
    minValue: parseNonNegativeFloat(sp.get("min_value")),
    maxValue: parseNonNegativeFloat(sp.get("max_value")),
    sortBy: sp.get("sort_by") ?? INVESTORS_STATE_DEFAULTS.sortBy,
    sortDir:
      sortDirRaw && (SORT_DIRS as ReadonlyArray<string>).includes(sortDirRaw)
        ? (sortDirRaw as InvestorSortDir)
        : INVESTORS_STATE_DEFAULTS.sortDir,
    page: parseIntInRange(sp.get("page"), INVESTORS_STATE_DEFAULTS.page, 1),
    limit: (ALLOWED_LIMITS as ReadonlyArray<number>).includes(limit)
      ? limit
      : INVESTORS_STATE_DEFAULTS.limit,
  };
}

// Strip default values from the URL so a bare load lands on plain
// /investors — matches the master-list and advisor-list behavior.
export function toSearchParams(state: InvestorsQueryState): URLSearchParams {
  const sp = new URLSearchParams();

  if (state.tab !== INVESTORS_STATE_DEFAULTS.tab) sp.set("tab", state.tab);
  if (state.search !== INVESTORS_STATE_DEFAULTS.search) sp.set("q", state.search);
  if (state.ticker !== INVESTORS_STATE_DEFAULTS.ticker) {
    sp.set("ticker", state.ticker);
  }
  if (state.state !== INVESTORS_STATE_DEFAULTS.state) sp.set("state", state.state);
  if (state.days !== INVESTORS_STATE_DEFAULTS.days) sp.set("days", String(state.days));
  if (state.minValue !== null) sp.set("min_value", String(state.minValue));
  if (state.maxValue !== null) sp.set("max_value", String(state.maxValue));
  if (state.sortBy !== INVESTORS_STATE_DEFAULTS.sortBy) sp.set("sort_by", state.sortBy);
  if (state.sortDir !== INVESTORS_STATE_DEFAULTS.sortDir) {
    sp.set("sort_dir", state.sortDir);
  }
  if (state.page !== INVESTORS_STATE_DEFAULTS.page) sp.set("page", String(state.page));
  if (state.limit !== INVESTORS_STATE_DEFAULTS.limit) {
    sp.set("limit", String(state.limit));
  }

  return sp;
}

export function buildInvestorsUrl(state: InvestorsQueryState): string {
  const query = toSearchParams(state).toString();
  return query ? `/investors?${query}` : "/investors";
}

// Filter keys are user-facing query controls. Tab, sort, page, page-size
// are workspace navigation — not filters — so they're excluded.
export function hasActiveFilters(state: InvestorsQueryState): boolean {
  return (
    state.search !== INVESTORS_STATE_DEFAULTS.search ||
    state.ticker !== INVESTORS_STATE_DEFAULTS.ticker ||
    state.state !== INVESTORS_STATE_DEFAULTS.state ||
    state.days !== INVESTORS_STATE_DEFAULTS.days ||
    state.minValue !== null ||
    state.maxValue !== null
  );
}

export function clearAllFilters(
  state: InvestorsQueryState,
): InvestorsQueryState {
  return {
    ...state,
    search: INVESTORS_STATE_DEFAULTS.search,
    ticker: INVESTORS_STATE_DEFAULTS.ticker,
    state: INVESTORS_STATE_DEFAULTS.state,
    days: INVESTORS_STATE_DEFAULTS.days,
    minValue: null,
    maxValue: null,
    page: 1,
  };
}
