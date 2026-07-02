// URL <-> component-state mapping for the bank-charter list workspace.
//
// Sibling of frontend/lib/advisor-list-state.ts (which is itself a sibling
// of master-list-state.ts). Same conventions: every filter, the active
// sort, and pagination live in URL search params so back-nav from
// /banks/{id} restores the user's view, share-links carry the same state,
// and a hard reload doesn't lose filters.
//
// Param-key contract intentionally aligns with the BE filter vocabulary on
// GET /api/v1/banks (search, state, charter_authority, charter_status,
// digital_assets, established_after/established_before) so a debugging
// session can copy-paste between the BE API URL and the FE URL without
// translation.

export type SortDir = "asc" | "desc";

// "All" applies no charter_authority filter; "OCC" = national charters,
// "STATE" = state charters (FDIC CHRTAGNT vocabulary, verbatim on the wire).
export type BankCharterAuthorityFilter = "All" | "OCC" | "STATE";

// "all" applies no digital_assets filter; "tagged" sends digital_assets=true
// (OCC Digital Assets Licensing Applications page matches only). The
// explicit-false ("untagged only") tri-state leg the BE supports is
// deliberately not surfaced — it has no product ask.
export type BankDigitalAssetsFilter = "all" | "tagged";

export interface BankListQueryState {
  search: string;
  // 2-letter state codes, repeat-key `?state=TX&state=NY` on the wire.
  states: string[];
  charterAuthority: BankCharterAuthorityFilter;
  // pending | approved | opened | withdrawn | rescinded (multi).
  charterStatuses: string[];
  digitalAssets: BankDigitalAssetsFilter;
  // ISO YYYY-MM-DD strings — matches what <input type="date"> emits.
  establishedAfter: string | null;
  establishedBefore: string | null;
  sortBy: string;
  sortDir: SortDir;
  page: number;
  limit: number;
}

// Defaults mirror the BE endpoint defaults (established_date desc, 25/page)
// so a filter-less load renders the same rows the raw API returns.
export const BANK_LIST_STATE_DEFAULTS: BankListQueryState = {
  search: "",
  states: [],
  charterAuthority: "All",
  charterStatuses: [],
  digitalAssets: "all",
  establishedAfter: null,
  establishedBefore: null,
  sortBy: "established_date",
  sortDir: "desc",
  page: 1,
  limit: 25,
};

const SORT_DIRS: ReadonlyArray<SortDir> = ["asc", "desc"];
const ALLOWED_LIMITS: ReadonlyArray<number> = [25, 50, 100];
const CHARTER_AUTHORITY_VALUES: ReadonlyArray<BankCharterAuthorityFilter> = [
  "All",
  "OCC",
  "STATE",
];

type SearchParamsLike = {
  get(name: string): string | null;
  getAll(name: string): string[];
};

// Repeat-key multi param (`?k=a&k=b`). Values here are short enum-ish
// tokens (state codes, status slugs) so comma-splitting would be safe,
// but we never write commas — one entry per value, like the advisor list.
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

export function fromSearchParams(sp: SearchParamsLike): BankListQueryState {
  const sortDir = sp.get("sort_dir");
  const authorityRaw = sp.get("charter_authority");
  const digitalAssetsRaw = sp.get("digital_assets");
  const limit = parseIntInRange(
    sp.get("limit"),
    BANK_LIST_STATE_DEFAULTS.limit,
    1,
  );

  return {
    search: sp.get("search") ?? BANK_LIST_STATE_DEFAULTS.search,
    states: parseMultiParam(sp, "state"),
    charterAuthority:
      authorityRaw &&
      (CHARTER_AUTHORITY_VALUES as ReadonlyArray<string>).includes(authorityRaw)
        ? (authorityRaw as BankCharterAuthorityFilter)
        : BANK_LIST_STATE_DEFAULTS.charterAuthority,
    charterStatuses: parseMultiParam(sp, "charter_status"),
    // URL carries the BE wire value (`digital_assets=true`); anything else
    // falls back to "no filter".
    digitalAssets:
      digitalAssetsRaw === "true"
        ? "tagged"
        : BANK_LIST_STATE_DEFAULTS.digitalAssets,
    establishedAfter: sp.get("established_after") || null,
    establishedBefore: sp.get("established_before") || null,
    sortBy: sp.get("sort_by") ?? BANK_LIST_STATE_DEFAULTS.sortBy,
    sortDir:
      sortDir && (SORT_DIRS as ReadonlyArray<string>).includes(sortDir)
        ? (sortDir as SortDir)
        : BANK_LIST_STATE_DEFAULTS.sortDir,
    page: parseIntInRange(sp.get("page"), BANK_LIST_STATE_DEFAULTS.page, 1),
    limit: (ALLOWED_LIMITS as ReadonlyArray<number>).includes(limit)
      ? limit
      : BANK_LIST_STATE_DEFAULTS.limit,
  };
}

// Strip default values from the URL so a filter-less load lands on plain
// /banks, not /banks?page=1&limit=25 — matches the sibling workspaces.
export function toSearchParams(state: BankListQueryState): URLSearchParams {
  const sp = new URLSearchParams();

  if (state.search !== BANK_LIST_STATE_DEFAULTS.search) {
    sp.set("search", state.search);
  }
  if (state.states.length > 0) {
    state.states.forEach((entry) => sp.append("state", entry));
  }
  if (state.charterAuthority !== BANK_LIST_STATE_DEFAULTS.charterAuthority) {
    sp.set("charter_authority", state.charterAuthority);
  }
  if (state.charterStatuses.length > 0) {
    state.charterStatuses.forEach((entry) => sp.append("charter_status", entry));
  }
  if (state.digitalAssets === "tagged") {
    sp.set("digital_assets", "true");
  }
  if (state.establishedAfter !== null) {
    sp.set("established_after", state.establishedAfter);
  }
  if (state.establishedBefore !== null) {
    sp.set("established_before", state.establishedBefore);
  }
  if (state.sortBy !== BANK_LIST_STATE_DEFAULTS.sortBy) {
    sp.set("sort_by", state.sortBy);
  }
  if (state.sortDir !== BANK_LIST_STATE_DEFAULTS.sortDir) {
    sp.set("sort_dir", state.sortDir);
  }
  if (state.page !== BANK_LIST_STATE_DEFAULTS.page) {
    sp.set("page", String(state.page));
  }
  if (state.limit !== BANK_LIST_STATE_DEFAULTS.limit) {
    sp.set("limit", String(state.limit));
  }

  return sp;
}

export function buildBankListUrl(state: BankListQueryState): string {
  const query = toSearchParams(state).toString();
  return query ? `/banks?${query}` : "/banks";
}

export function parseReturnParam(
  raw: string | null,
): BankListQueryState | null {
  if (!raw) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return null;
  }
  const queryIndex = decoded.indexOf("?");
  const queryString = queryIndex >= 0 ? decoded.slice(queryIndex + 1) : decoded;
  if (!queryString) return BANK_LIST_STATE_DEFAULTS;
  return fromSearchParams(new URLSearchParams(queryString));
}

export function encodeReturnParam(state: BankListQueryState): string {
  const query = toSearchParams(state).toString();
  if (!query) return "";
  return encodeURIComponent(`/banks?${query}`);
}

// Filter keys are user-facing query controls. Sort, page, and page-size are
// workspace navigation state — not filters — so the "any filters active?"
// check excludes them.
export function hasActiveFilters(state: BankListQueryState): boolean {
  return (
    state.search !== BANK_LIST_STATE_DEFAULTS.search ||
    state.states.length > 0 ||
    state.charterAuthority !== BANK_LIST_STATE_DEFAULTS.charterAuthority ||
    state.charterStatuses.length > 0 ||
    state.digitalAssets !== BANK_LIST_STATE_DEFAULTS.digitalAssets ||
    state.establishedAfter !== null ||
    state.establishedBefore !== null
  );
}

export function clearAllFilters(state: BankListQueryState): BankListQueryState {
  return {
    ...state,
    search: BANK_LIST_STATE_DEFAULTS.search,
    states: [],
    charterAuthority: BANK_LIST_STATE_DEFAULTS.charterAuthority,
    charterStatuses: [],
    digitalAssets: BANK_LIST_STATE_DEFAULTS.digitalAssets,
    establishedAfter: null,
    establishedBefore: null,
    page: 1,
  };
}
