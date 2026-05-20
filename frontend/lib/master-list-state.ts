// URL <-> component-state mapping for the master-list workspace.
//
// Lifts every filter, the active sort, and pagination into URL search
// params so that:
//   - back-nav from /master-list/{id} restores the same view
//   - hard reload preserves the user's filters
//   - share-links carry the same query state
//   - the firm-detail page can read the same shape via a `?return=`
//     envelope to walk Next/Previous Prospect inside the user's
//     filtered and sorted result set
//
// Param-key contract was originally aligned with the BE filter vocabulary
// (clearing_partner, clearing_type, prospect_priority, list,
// types_of_business). The "prospect_priority" URL key replaces the
// previous "lead_priority" key — existing bookmarks with the old key
// silently fall back to default. New keys (q, state, health, sort_by,
// sort_dir, page, limit) match the backend list endpoint's vocabulary
// so a future copy-paste-from-API debugging session lines up cleanly.

export type ListMode = "primary" | "alternative" | "all";
export type SortDir = "asc" | "desc";
// Sprint 6 task #29: which workspace the user came from when they
// landed on /master-list/{id}. Drives the detail-page Next-Prospect
// walker (which list to step through) and the breadcrumb back-link copy
// + href. "master-list" is the existing behavior; "favorites" and
// "visited" are added in this PR.
export type DetailSource = "master-list" | "favorites" | "visited";

export interface MasterListQueryState {
  search: string;
  state: string;
  health: string;
  prospectPriority: string;
  clearingPartner: string[];
  clearingType: string;
  typesOfBusiness: string[];
  // Sprint 3 task #15: net-capital range. Dollars (not cents). null when
  // the filter is unset — keeps `0` distinguishable from "no filter".
  minNetCapital: number | null;
  maxNetCapital: number | null;
  // Sprint 3 task #16: SEC registration date range. ISO `YYYY-MM-DD`
  // strings — the format native <input type="date"> emits and the BE's
  // FastAPI date validator accepts.
  registeredAfter: string | null;
  registeredBefore: string | null;
  list: ListMode;
  sortBy: string;
  sortDir: SortDir;
  page: number;
  limit: number;
  source: DetailSource;
}

// Defaults are the same values the workspace component used as initial
// useState seeds before this file existed, so a URL with no params at
// all renders identically to the pre-PR behavior.
export const MASTER_LIST_STATE_DEFAULTS: MasterListQueryState = {
  search: "",
  state: "",
  health: "All",
  prospectPriority: "All",
  clearingPartner: [],
  clearingType: "All",
  typesOfBusiness: [],
  minNetCapital: null,
  maxNetCapital: null,
  registeredAfter: null,
  registeredBefore: null,
  list: "primary",
  sortBy: "latest_net_capital",
  sortDir: "desc",
  page: 1,
  limit: 25,
  source: "master-list",
};

const LIST_MODES: ReadonlyArray<ListMode> = ["primary", "alternative", "all"];
const SORT_DIRS: ReadonlyArray<SortDir> = ["asc", "desc"];
const ALLOWED_LIMITS: ReadonlyArray<number> = [25, 50, 100];
const DETAIL_SOURCES: ReadonlyArray<DetailSource> = [
  "master-list",
  "favorites",
  "visited",
];

// Minimal interface so callers can pass either a real URLSearchParams
// or Next.js's ReadonlyURLSearchParams (which exposes the same surface
// without being assignable to URLSearchParams).
type SearchParamsLike = {
  get(name: string): string | null;
  getAll(name: string): string[];
};

// Splits CSV values inside a single param entry too (`?k=a,b`) so a
// link copy-pasted from a hand-edited URL behaves the same as the
// repeat-key form (`?k=a&k=b`). Mirrors the existing helper in
// app/(app)/master-list/page.tsx so the two parsers don't diverge.
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

// Returns null for a missing or unparseable value so the field reads as
// "no filter applied" rather than coercing into 0 or NaN. Negative values
// are rejected — the BE rejects them too (ge=0).
function parseNonNegativeFloat(raw: string | null): number | null {
  if (raw === null || raw === "") return null;
  const parsed = Number.parseFloat(raw);
  if (!Number.isFinite(parsed) || parsed < 0) return null;
  return parsed;
}

export function fromSearchParams(sp: SearchParamsLike): MasterListQueryState {
  const list = sp.get("list");
  const sortDir = sp.get("sort_dir");
  const source = sp.get("source");
  const limit = parseIntInRange(
    sp.get("limit"),
    MASTER_LIST_STATE_DEFAULTS.limit,
    1,
  );

  return {
    search: sp.get("q") ?? MASTER_LIST_STATE_DEFAULTS.search,
    state: sp.get("state") ?? MASTER_LIST_STATE_DEFAULTS.state,
    health: sp.get("health") ?? MASTER_LIST_STATE_DEFAULTS.health,
    prospectPriority:
      sp.get("prospect_priority") ?? MASTER_LIST_STATE_DEFAULTS.prospectPriority,
    clearingPartner: parseMultiParam(sp, "clearing_partner"),
    clearingType:
      sp.get("clearing_type") ?? MASTER_LIST_STATE_DEFAULTS.clearingType,
    typesOfBusiness: parseMultiParam(sp, "types_of_business"),
    // `min_net_capital=0` is a no-op for non-negative net-capital values, so
    // collapse it to null at parse time. Keeps legacy share-links from
    // surfacing a "Net capital ≥ $0" chip after we normalize on commit.
    minNetCapital: (() => {
      const raw = parseNonNegativeFloat(sp.get("min_net_capital"));
      return raw === 0 ? null : raw;
    })(),
    maxNetCapital: parseNonNegativeFloat(sp.get("max_net_capital")),
    registeredAfter: sp.get("registered_after") || null,
    registeredBefore: sp.get("registered_before") || null,
    list:
      list && (LIST_MODES as ReadonlyArray<string>).includes(list)
        ? (list as ListMode)
        : MASTER_LIST_STATE_DEFAULTS.list,
    sortBy: sp.get("sort_by") ?? MASTER_LIST_STATE_DEFAULTS.sortBy,
    sortDir:
      sortDir && (SORT_DIRS as ReadonlyArray<string>).includes(sortDir)
        ? (sortDir as SortDir)
        : MASTER_LIST_STATE_DEFAULTS.sortDir,
    page: parseIntInRange(
      sp.get("page"),
      MASTER_LIST_STATE_DEFAULTS.page,
      1,
    ),
    limit: (ALLOWED_LIMITS as ReadonlyArray<number>).includes(limit)
      ? limit
      : MASTER_LIST_STATE_DEFAULTS.limit,
    source:
      source && (DETAIL_SOURCES as ReadonlyArray<string>).includes(source)
        ? (source as DetailSource)
        : MASTER_LIST_STATE_DEFAULTS.source,
  };
}

// Strips defaults so the URL stays clean. A user with no filters
// applied lands on plain /master-list, not /master-list?list=primary&page=1&limit=25
// which would otherwise nag them on every share-link.
export function toSearchParams(state: MasterListQueryState): URLSearchParams {
  const sp = new URLSearchParams();

  if (state.search !== MASTER_LIST_STATE_DEFAULTS.search) {
    sp.set("q", state.search);
  }
  if (state.state !== MASTER_LIST_STATE_DEFAULTS.state) {
    sp.set("state", state.state);
  }
  if (state.health !== MASTER_LIST_STATE_DEFAULTS.health) {
    sp.set("health", state.health);
  }
  if (state.prospectPriority !== MASTER_LIST_STATE_DEFAULTS.prospectPriority) {
    sp.set("prospect_priority", state.prospectPriority);
  }
  if (state.clearingPartner.length > 0) {
    state.clearingPartner.forEach((entry) =>
      sp.append("clearing_partner", entry),
    );
  }
  if (state.clearingType !== MASTER_LIST_STATE_DEFAULTS.clearingType) {
    sp.set("clearing_type", state.clearingType);
  }
  if (state.typesOfBusiness.length > 0) {
    state.typesOfBusiness.forEach((entry) =>
      sp.append("types_of_business", entry),
    );
  }
  if (state.minNetCapital !== null) {
    sp.set("min_net_capital", String(state.minNetCapital));
  }
  if (state.maxNetCapital !== null) {
    sp.set("max_net_capital", String(state.maxNetCapital));
  }
  if (state.registeredAfter !== null) {
    sp.set("registered_after", state.registeredAfter);
  }
  if (state.registeredBefore !== null) {
    sp.set("registered_before", state.registeredBefore);
  }
  if (state.list !== MASTER_LIST_STATE_DEFAULTS.list) {
    sp.set("list", state.list);
  }
  if (state.sortBy !== MASTER_LIST_STATE_DEFAULTS.sortBy) {
    sp.set("sort_by", state.sortBy);
  }
  if (state.sortDir !== MASTER_LIST_STATE_DEFAULTS.sortDir) {
    sp.set("sort_dir", state.sortDir);
  }
  if (state.page !== MASTER_LIST_STATE_DEFAULTS.page) {
    sp.set("page", String(state.page));
  }
  if (state.limit !== MASTER_LIST_STATE_DEFAULTS.limit) {
    sp.set("limit", String(state.limit));
  }
  if (state.source !== MASTER_LIST_STATE_DEFAULTS.source) {
    sp.set("source", state.source);
  }

  return sp;
}

export function buildMasterListUrl(state: MasterListQueryState): string {
  const query = toSearchParams(state).toString();
  return query ? `/master-list?${query}` : "/master-list";
}

// Resolves the user's source workspace URL — the page they were on
// before clicking into /master-list/{id}. Used by the detail-page
// breadcrumb back-link so /my-favorites and /visited-firms users land
// back on the right page (and not on /master-list, which is the bug
// task #29 fixes).
//
// For "favorites" and "visited" the URL is bare today — those pages
// don't expose URL-backed sort/page state because the BE pins the sort
// (created_at DESC / last_visited_at DESC). When sort support lands,
// this is the single place to add the query string.
export function buildSourceListUrl(state: MasterListQueryState): string {
  switch (state.source) {
    case "favorites":
      return "/my-favorites";
    case "visited":
      return "/visited-firms";
    default:
      return buildMasterListUrl(state);
  }
}

// Parses a `return` envelope param produced by encodeReturnParam below.
// Returns null when the input is missing/malformed so callers can fall
// back to defaults. Accepts either a full path (`/master-list?...`) or
// a bare query string.
export function parseReturnParam(raw: string | null): MasterListQueryState | null {
  if (!raw) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return null;
  }
  const queryIndex = decoded.indexOf("?");
  const queryString = queryIndex >= 0 ? decoded.slice(queryIndex + 1) : decoded;
  if (!queryString) return MASTER_LIST_STATE_DEFAULTS;
  return fromSearchParams(new URLSearchParams(queryString));
}

// Encodes a state object as the value of a `return` query param so the
// caller can append `?return=<encodeReturnParam(state)>` to any
// destination URL. Always emits a non-empty envelope — even when state
// is at defaults — so the detail page never falls through to its bare
// "/master-list" fallback (broker-dealer-detail-client.tsx). The cost is
// ~22 extra bytes on row hrefs at the default state; the win is no
// silent filter-loss when the user round-trips through a firm detail.
export function encodeReturnParam(state: MasterListQueryState): string {
  const query = toSearchParams(state).toString();
  const path = query ? `/master-list?${query}` : "/master-list";
  return encodeURIComponent(path);
}

// True when at least one filter key differs from its default. Filter
// keys are the user-facing query controls (search, state, health,
// prospect priority, clearing partner / type, types of business,
// net-capital range, registration-date range). Sort, list mode, page
// size, page, and source are workspace/navigation state — not filters
// — so they don't count toward "is anything filtered?".
export function hasActiveFilters(state: MasterListQueryState): boolean {
  return (
    state.search !== MASTER_LIST_STATE_DEFAULTS.search ||
    state.state !== MASTER_LIST_STATE_DEFAULTS.state ||
    state.health !== MASTER_LIST_STATE_DEFAULTS.health ||
    state.prospectPriority !== MASTER_LIST_STATE_DEFAULTS.prospectPriority ||
    state.clearingPartner.length > 0 ||
    state.clearingType !== MASTER_LIST_STATE_DEFAULTS.clearingType ||
    state.typesOfBusiness.length > 0 ||
    state.minNetCapital !== null ||
    state.maxNetCapital !== null ||
    state.registeredAfter !== null ||
    state.registeredBefore !== null
  );
}

// Returns a new state with every filter key reset to its default and
// the page reset to 1. Preserves sortBy, sortDir, list, limit, and
// source — those are workspace preferences (which list mode you're
// on, how things are sorted, how many rows per page, where you came
// from), not filters. The expectation behind a one-click reset is
// "show me an unfiltered view of this same list, sorted the way I
// already had it."
export function clearAllFilters(
  state: MasterListQueryState,
): MasterListQueryState {
  return {
    ...state,
    search: MASTER_LIST_STATE_DEFAULTS.search,
    state: MASTER_LIST_STATE_DEFAULTS.state,
    health: MASTER_LIST_STATE_DEFAULTS.health,
    prospectPriority: MASTER_LIST_STATE_DEFAULTS.prospectPriority,
    clearingPartner: [],
    clearingType: MASTER_LIST_STATE_DEFAULTS.clearingType,
    typesOfBusiness: [],
    minNetCapital: null,
    maxNetCapital: null,
    registeredAfter: null,
    registeredBefore: null,
    page: 1,
  };
}
