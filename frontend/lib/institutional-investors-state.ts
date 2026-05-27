// URL <-> component-state mapping for the institutional-investors list workspace.
//
// Sibling of advisor-list-state.ts. Same conventions: every filter, the
// active sort, and pagination live in URL search params so back-nav from
// /institutional-investors/{id} restores the user's view, share-links carry
// the same state, and a hard reload doesn't lose filters.
//
// Param-key contract aligns with the BE filter vocabulary in
// backend/app/api/v1/endpoints/institutional_investors.py (state, status,
// min_total_aum, filed_13f_after, only_with_advisor_link) so a debugging
// session can copy-paste between the BE API URL and the FE URL without
// translation.

export type SortDir = "asc" | "desc";

export interface InstitutionalInvestorsQueryState {
  search: string;
  states: string[];
  statuses: string[];
  // BE param `only_with_advisor_link`: true narrows the universe to 13F
  // filers that also appear as registered RIAs (the "Also RIA" pin).
  onlyWithAdvisor: boolean;
  // Dollars (not cents). null when the filter is unset — keeps `0`
  // distinguishable from "no filter".
  minTotalAum: number | null;
  maxTotalAum: number | null;
  // ISO YYYY-MM-DD strings — matches what <input type="date"> emits.
  filed13fAfter: string | null;
  filed13fBefore: string | null;
  sortBy: string;
  sortDir: SortDir;
  page: number;
  limit: number;
}

// Defaults reflect the page's existing behavior: largest 13F filers first
// (total_aum DESC), 25 rows per page, no filters applied.
export const INSTITUTIONAL_INVESTORS_STATE_DEFAULTS: InstitutionalInvestorsQueryState =
  {
    search: "",
    states: [],
    statuses: [],
    onlyWithAdvisor: false,
    minTotalAum: null,
    maxTotalAum: null,
    filed13fAfter: null,
    filed13fBefore: null,
    sortBy: "total_aum",
    sortDir: "desc",
    page: 1,
    limit: 25,
  };

const SORT_DIRS: ReadonlyArray<SortDir> = ["asc", "desc"];
const ALLOWED_LIMITS: ReadonlyArray<number> = [25, 50, 100];

type SearchParamsLike = {
  get(name: string): string | null;
  getAll(name: string): string[];
};

// Repeat-key multi param (`?k=a&k=b`). Same shape as advisor-list-state's
// helper — values can contain commas (state-status labels won't, but the
// shape is consistent across the codebase).
function parseMultiParam(sp: SearchParamsLike, key: string): string[] {
  return sp
    .getAll(key)
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
}

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

export function fromSearchParams(
  sp: SearchParamsLike,
): InstitutionalInvestorsQueryState {
  const sortDir = sp.get("sort_dir");
  const limit = parseIntInRange(
    sp.get("limit"),
    INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.limit,
    1,
  );

  return {
    search: sp.get("q") ?? INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.search,
    states: parseMultiParam(sp, "state"),
    statuses: parseMultiParam(sp, "status"),
    onlyWithAdvisor: sp.get("only_with_advisor_link") === "true",
    minTotalAum: parseNonNegativeFloat(sp.get("min_total_aum")),
    maxTotalAum: parseNonNegativeFloat(sp.get("max_total_aum")),
    filed13fAfter: sp.get("filed_13f_after") || null,
    filed13fBefore: sp.get("filed_13f_before") || null,
    sortBy:
      sp.get("sort_by") ?? INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.sortBy,
    sortDir:
      sortDir && (SORT_DIRS as ReadonlyArray<string>).includes(sortDir)
        ? (sortDir as SortDir)
        : INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.sortDir,
    page: parseIntInRange(
      sp.get("page"),
      INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.page,
      1,
    ),
    limit: (ALLOWED_LIMITS as ReadonlyArray<number>).includes(limit)
      ? limit
      : INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.limit,
  };
}

// Strip default values from the URL so a filter-less load lands on plain
// /institutional-investors, not /institutional-investors?page=1&limit=25.
export function toSearchParams(
  state: InstitutionalInvestorsQueryState,
): URLSearchParams {
  const sp = new URLSearchParams();

  if (state.search !== INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.search) {
    sp.set("q", state.search);
  }
  if (state.states.length > 0) {
    state.states.forEach((entry) => sp.append("state", entry));
  }
  if (state.statuses.length > 0) {
    state.statuses.forEach((entry) => sp.append("status", entry));
  }
  if (state.onlyWithAdvisor) {
    sp.set("only_with_advisor_link", "true");
  }
  if (state.minTotalAum !== null) {
    sp.set("min_total_aum", String(state.minTotalAum));
  }
  if (state.maxTotalAum !== null) {
    sp.set("max_total_aum", String(state.maxTotalAum));
  }
  if (state.filed13fAfter !== null) {
    sp.set("filed_13f_after", state.filed13fAfter);
  }
  if (state.filed13fBefore !== null) {
    sp.set("filed_13f_before", state.filed13fBefore);
  }
  if (state.sortBy !== INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.sortBy) {
    sp.set("sort_by", state.sortBy);
  }
  if (state.sortDir !== INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.sortDir) {
    sp.set("sort_dir", state.sortDir);
  }
  if (state.page !== INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.page) {
    sp.set("page", String(state.page));
  }
  if (state.limit !== INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.limit) {
    sp.set("limit", String(state.limit));
  }

  return sp;
}

export function buildInstitutionalInvestorsUrl(
  state: InstitutionalInvestorsQueryState,
): string {
  const query = toSearchParams(state).toString();
  return query
    ? `/institutional-investors?${query}`
    : "/institutional-investors";
}

export function encodeReturnParam(
  state: InstitutionalInvestorsQueryState,
): string {
  const query = toSearchParams(state).toString();
  if (!query) return "";
  return encodeURIComponent(`/institutional-investors?${query}`);
}

// Filter keys are user-facing query controls. Sort, page, page-size are
// workspace navigation state — not filters — so the "any filters active?"
// check excludes them.
export function hasActiveFilters(
  state: InstitutionalInvestorsQueryState,
): boolean {
  return (
    state.search !== INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.search ||
    state.states.length > 0 ||
    state.statuses.length > 0 ||
    state.onlyWithAdvisor !==
      INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.onlyWithAdvisor ||
    state.minTotalAum !== null ||
    state.maxTotalAum !== null ||
    state.filed13fAfter !== null ||
    state.filed13fBefore !== null
  );
}

export function clearAllFilters(
  state: InstitutionalInvestorsQueryState,
): InstitutionalInvestorsQueryState {
  return {
    ...state,
    search: INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.search,
    states: [],
    statuses: [],
    onlyWithAdvisor: INSTITUTIONAL_INVESTORS_STATE_DEFAULTS.onlyWithAdvisor,
    minTotalAum: null,
    maxTotalAum: null,
    filed13fAfter: null,
    filed13fBefore: null,
    page: 1,
  };
}
