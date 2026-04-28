// URL <-> component-state mapping for the master-list workspace.
//
// Lifts every filter, the active sort, and pagination into URL search
// params so that:
//   - back-nav from /master-list/{id} restores the same view
//   - hard reload preserves the user's filters
//   - share-links carry the same query state
//   - the firm-detail page can read the same shape via a `?return=`
//     envelope to walk Next/Previous Lead inside the user's filtered
//     and sorted result set
//
// Param-key contract is intentionally aligned with the existing five
// keys read by app/(app)/master-list/page.tsx (clearing_partner,
// clearing_type, lead_priority, list, types_of_business) so any
// bookmarked deep-link from PR #99 keeps working. New keys (q, state,
// health, sort_by, sort_dir, page, limit) match the backend list
// endpoint's vocabulary so a future copy-paste-from-API debugging
// session lines up cleanly.

export type ListMode = "primary" | "alternative" | "all";
export type SortDir = "asc" | "desc";

export interface MasterListQueryState {
  search: string;
  state: string;
  health: string;
  leadPriority: string;
  clearingPartner: string;
  clearingType: string;
  typesOfBusiness: string[];
  list: ListMode;
  sortBy: string;
  sortDir: SortDir;
  page: number;
  limit: number;
}

// Defaults are the same values the workspace component used as initial
// useState seeds before this file existed, so a URL with no params at
// all renders identically to the pre-PR behavior.
export const MASTER_LIST_STATE_DEFAULTS: MasterListQueryState = {
  search: "",
  state: "",
  health: "All",
  leadPriority: "All",
  clearingPartner: "",
  clearingType: "All",
  typesOfBusiness: [],
  list: "primary",
  sortBy: "name",
  sortDir: "asc",
  page: 1,
  limit: 25,
};

const LIST_MODES: ReadonlyArray<ListMode> = ["primary", "alternative", "all"];
const SORT_DIRS: ReadonlyArray<SortDir> = ["asc", "desc"];
const ALLOWED_LIMITS: ReadonlyArray<number> = [25, 50, 100];

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

export function fromSearchParams(sp: SearchParamsLike): MasterListQueryState {
  const list = sp.get("list");
  const sortDir = sp.get("sort_dir");
  const limit = parseIntInRange(
    sp.get("limit"),
    MASTER_LIST_STATE_DEFAULTS.limit,
    1,
  );

  return {
    search: sp.get("q") ?? MASTER_LIST_STATE_DEFAULTS.search,
    state: sp.get("state") ?? MASTER_LIST_STATE_DEFAULTS.state,
    health: sp.get("health") ?? MASTER_LIST_STATE_DEFAULTS.health,
    leadPriority:
      sp.get("lead_priority") ?? MASTER_LIST_STATE_DEFAULTS.leadPriority,
    clearingPartner:
      sp.get("clearing_partner") ?? MASTER_LIST_STATE_DEFAULTS.clearingPartner,
    clearingType:
      sp.get("clearing_type") ?? MASTER_LIST_STATE_DEFAULTS.clearingType,
    typesOfBusiness: parseMultiParam(sp, "types_of_business"),
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
  if (state.leadPriority !== MASTER_LIST_STATE_DEFAULTS.leadPriority) {
    sp.set("lead_priority", state.leadPriority);
  }
  if (state.clearingPartner !== MASTER_LIST_STATE_DEFAULTS.clearingPartner) {
    sp.set("clearing_partner", state.clearingPartner);
  }
  if (state.clearingType !== MASTER_LIST_STATE_DEFAULTS.clearingType) {
    sp.set("clearing_type", state.clearingType);
  }
  if (state.typesOfBusiness.length > 0) {
    state.typesOfBusiness.forEach((entry) =>
      sp.append("types_of_business", entry),
    );
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

  return sp;
}

export function buildMasterListUrl(state: MasterListQueryState): string {
  const query = toSearchParams(state).toString();
  return query ? `/master-list?${query}` : "/master-list";
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
// destination URL. Returns an empty string when the state is at its
// defaults so we don't pollute outbound URLs with a no-op envelope.
export function encodeReturnParam(state: MasterListQueryState): string {
  const query = toSearchParams(state).toString();
  if (!query) return "";
  return encodeURIComponent(`/master-list?${query}`);
}
