// URL <-> component-state mapping for the investment-advisor list workspace.
//
// Sibling of frontend/lib/master-list-state.ts. Same conventions: every
// filter, the active sort, and pagination live in URL search params so
// back-nav from /advisor-list/{id} restores the user's view, share-links
// carry the same state, and a hard reload doesn't lose filters.
//
// Param-key contract intentionally aligns with the BE filter vocabulary
// (advisory_activities, client_types, regulatory_aum) so a debugging
// session can copy-paste between the BE API URL and the FE URL without
// translation. Where a BE concept doesn't exist for advisors (clearing
// arrangement, prospect priority, list mode), the param is dropped.

export type SortDir = "asc" | "desc";
// Where the user came from when they clicked into /advisor-list/{id}.
// Drives the breadcrumb back-link copy and the Next/Previous walker once
// PR 3 wires it up. PR 1 only ships "advisor-list" — favorites/visited
// gain advisor support in PR 4.
export type AdvisorDetailSource = "advisor-list" | "favorites" | "visited";

export interface AdvisorListQueryState {
  search: string;
  state: string;
  advisoryActivities: string[];
  clientTypes: string[];
  // Dollars (not cents). null when the filter is unset — keeps `0`
  // distinguishable from "no filter".
  minRegulatoryAum: number | null;
  maxRegulatoryAum: number | null;
  // ISO YYYY-MM-DD strings — matches what <input type="date"> emits.
  registeredAfter: string | null;
  registeredBefore: string | null;
  sortBy: string;
  sortDir: SortDir;
  page: number;
  limit: number;
  source: AdvisorDetailSource;
}

// Defaults reflect the hard 13F-only scope: the advisor list is sorted
// by regulatory AUM descending so the largest filers surface first, with
// 25 rows per page (same as the BD master list).
export const ADVISOR_LIST_STATE_DEFAULTS: AdvisorListQueryState = {
  search: "",
  state: "",
  advisoryActivities: [],
  clientTypes: [],
  minRegulatoryAum: null,
  maxRegulatoryAum: null,
  registeredAfter: null,
  registeredBefore: null,
  sortBy: "regulatory_aum",
  sortDir: "desc",
  page: 1,
  limit: 25,
  source: "advisor-list",
};

const SORT_DIRS: ReadonlyArray<SortDir> = ["asc", "desc"];
const ALLOWED_LIMITS: ReadonlyArray<number> = [25, 50, 100];
const DETAIL_SOURCES: ReadonlyArray<AdvisorDetailSource> = [
  "advisor-list",
  "favorites",
  "visited",
];

type SearchParamsLike = {
  get(name: string): string | null;
  getAll(name: string): string[];
};

function parseMultiParam(sp: SearchParamsLike, key: string): string[] {
  return sp
    .getAll(key)
    .flatMap((entry) => entry.split(","))
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
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

export function fromSearchParams(sp: SearchParamsLike): AdvisorListQueryState {
  const sortDir = sp.get("sort_dir");
  const source = sp.get("source");
  const limit = parseIntInRange(
    sp.get("limit"),
    ADVISOR_LIST_STATE_DEFAULTS.limit,
    1,
  );

  return {
    search: sp.get("q") ?? ADVISOR_LIST_STATE_DEFAULTS.search,
    state: sp.get("state") ?? ADVISOR_LIST_STATE_DEFAULTS.state,
    advisoryActivities: parseMultiParam(sp, "advisory_activities"),
    clientTypes: parseMultiParam(sp, "client_types"),
    minRegulatoryAum: parseNonNegativeFloat(sp.get("min_regulatory_aum")),
    maxRegulatoryAum: parseNonNegativeFloat(sp.get("max_regulatory_aum")),
    registeredAfter: sp.get("registered_after") || null,
    registeredBefore: sp.get("registered_before") || null,
    sortBy: sp.get("sort_by") ?? ADVISOR_LIST_STATE_DEFAULTS.sortBy,
    sortDir:
      sortDir && (SORT_DIRS as ReadonlyArray<string>).includes(sortDir)
        ? (sortDir as SortDir)
        : ADVISOR_LIST_STATE_DEFAULTS.sortDir,
    page: parseIntInRange(sp.get("page"), ADVISOR_LIST_STATE_DEFAULTS.page, 1),
    limit: (ALLOWED_LIMITS as ReadonlyArray<number>).includes(limit)
      ? limit
      : ADVISOR_LIST_STATE_DEFAULTS.limit,
    source:
      source && (DETAIL_SOURCES as ReadonlyArray<string>).includes(source)
        ? (source as AdvisorDetailSource)
        : ADVISOR_LIST_STATE_DEFAULTS.source,
  };
}

// Strip default values from the URL so a filter-less load lands on plain
// /advisor-list, not /advisor-list?page=1&limit=25 — matches the BD
// master-list behavior.
export function toSearchParams(state: AdvisorListQueryState): URLSearchParams {
  const sp = new URLSearchParams();

  if (state.search !== ADVISOR_LIST_STATE_DEFAULTS.search) {
    sp.set("q", state.search);
  }
  if (state.state !== ADVISOR_LIST_STATE_DEFAULTS.state) {
    sp.set("state", state.state);
  }
  if (state.advisoryActivities.length > 0) {
    state.advisoryActivities.forEach((entry) =>
      sp.append("advisory_activities", entry),
    );
  }
  if (state.clientTypes.length > 0) {
    state.clientTypes.forEach((entry) => sp.append("client_types", entry));
  }
  if (state.minRegulatoryAum !== null) {
    sp.set("min_regulatory_aum", String(state.minRegulatoryAum));
  }
  if (state.maxRegulatoryAum !== null) {
    sp.set("max_regulatory_aum", String(state.maxRegulatoryAum));
  }
  if (state.registeredAfter !== null) {
    sp.set("registered_after", state.registeredAfter);
  }
  if (state.registeredBefore !== null) {
    sp.set("registered_before", state.registeredBefore);
  }
  if (state.sortBy !== ADVISOR_LIST_STATE_DEFAULTS.sortBy) {
    sp.set("sort_by", state.sortBy);
  }
  if (state.sortDir !== ADVISOR_LIST_STATE_DEFAULTS.sortDir) {
    sp.set("sort_dir", state.sortDir);
  }
  if (state.page !== ADVISOR_LIST_STATE_DEFAULTS.page) {
    sp.set("page", String(state.page));
  }
  if (state.limit !== ADVISOR_LIST_STATE_DEFAULTS.limit) {
    sp.set("limit", String(state.limit));
  }
  if (state.source !== ADVISOR_LIST_STATE_DEFAULTS.source) {
    sp.set("source", state.source);
  }

  return sp;
}

export function buildAdvisorListUrl(state: AdvisorListQueryState): string {
  const query = toSearchParams(state).toString();
  return query ? `/advisor-list?${query}` : "/advisor-list";
}

// Used by the detail page back-link so /my-favorites and /visited-firms
// users land back on the right page once those workspaces grow advisor
// support (PR 4). Until then, source defaults to "advisor-list" and we
// rebuild the list URL with the user's filters/sort.
export function buildSourceListUrl(state: AdvisorListQueryState): string {
  switch (state.source) {
    case "favorites":
      return "/my-favorites";
    case "visited":
      return "/visited-firms";
    default:
      return buildAdvisorListUrl(state);
  }
}

export function parseReturnParam(
  raw: string | null,
): AdvisorListQueryState | null {
  if (!raw) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return null;
  }
  const queryIndex = decoded.indexOf("?");
  const queryString = queryIndex >= 0 ? decoded.slice(queryIndex + 1) : decoded;
  if (!queryString) return ADVISOR_LIST_STATE_DEFAULTS;
  return fromSearchParams(new URLSearchParams(queryString));
}

export function encodeReturnParam(state: AdvisorListQueryState): string {
  const query = toSearchParams(state).toString();
  if (!query) return "";
  return encodeURIComponent(`/advisor-list?${query}`);
}

// Filter keys are user-facing query controls. Sort, page, page-size, and
// source are workspace navigation state — not filters — so the "any
// filters active?" check excludes them.
export function hasActiveFilters(state: AdvisorListQueryState): boolean {
  return (
    state.search !== ADVISOR_LIST_STATE_DEFAULTS.search ||
    state.state !== ADVISOR_LIST_STATE_DEFAULTS.state ||
    state.advisoryActivities.length > 0 ||
    state.clientTypes.length > 0 ||
    state.minRegulatoryAum !== null ||
    state.maxRegulatoryAum !== null ||
    state.registeredAfter !== null ||
    state.registeredBefore !== null
  );
}

export function clearAllFilters(
  state: AdvisorListQueryState,
): AdvisorListQueryState {
  return {
    ...state,
    search: ADVISOR_LIST_STATE_DEFAULTS.search,
    state: ADVISOR_LIST_STATE_DEFAULTS.state,
    advisoryActivities: [],
    clientTypes: [],
    minRegulatoryAum: null,
    maxRegulatoryAum: null,
    registeredAfter: null,
    registeredBefore: null,
    page: 1,
  };
}
