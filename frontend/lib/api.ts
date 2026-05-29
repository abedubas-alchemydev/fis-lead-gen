function resolveApiBaseUrl() {
  if (typeof window !== "undefined") {
    return "/api/backend";
  }

  const appBaseUrl = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";
  return `${appBaseUrl.replace(/\/$/, "")}/api/backend`;
}

export function buildApiPath(
  path: string,
  params?: Record<string, string | number | boolean | string[] | undefined>
) {
  if (!params) {
    return path;
  }

  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") {
      continue;
    }

    if (Array.isArray(value)) {
      value.forEach((item) => searchParams.append(key, item));
      continue;
    }

    searchParams.set(key, String(value));
  }

  const query = searchParams.toString();
  return query ? `${path}?${query}` : path;
}

// Thrown by apiRequest on non-2xx responses. Preserves status + parsed
// `detail` (FastAPI's standard error envelope) so phase-2 favorite-list
// callers can distinguish 400 validation from 404 not-found and surface
// the BE's message inline. Extends Error so existing callers using
// `err instanceof Error ? err.message : ...` keep working unchanged.
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail || `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${resolveApiBaseUrl()}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });

  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    if (text) {
      try {
        const parsed = JSON.parse(text) as unknown;
        if (
          parsed &&
          typeof parsed === "object" &&
          "detail" in parsed &&
          typeof (parsed as { detail: unknown }).detail === "string"
        ) {
          detail = (parsed as { detail: string }).detail;
        }
      } catch {
        // Non-JSON body — fall back to raw text.
      }
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

// ── Favorite-lists (#17 phase 1 GET, phase 2 POST/PUT/DELETE) ────────────
// Multi-list view shipped in PR #140. Phase 2 (this PR) adds writable
// surface — create, rename, delete — for the /my-favorites sidebar.
// Default-list rules are enforced by the BE (400) and mirrored in the UI
// so the kebab disables Rename/Delete for default lists.
import type {
  FavoriteList,
  FavoriteListWithMembership,
  PaginatedFavoriteListItems,
  ReportingOwnerItemAddResponse
} from "@/types/favorite-list";
import type {
  AdjacentResponse,
  AdminUserActivitiesResponse,
  AdminUserActivityFilter,
  AdminUserSavedFirmsResponse,
  ClearingMembershipDecisionResponse,
  ClearingMembershipReviewListResponse,
  LinkedProvidersResponse,
  ContactSearchResponse,
  InstitutionalInvestorListResponse,
  InstitutionalInvestorProfileResponse,
  InvestorEnrichResponse,
  InvestorListResponse,
  OutreachAdhocDraftRequest,
  OutreachAdhocSendRequest,
  OutreachAdvisorDraftRequest,
  OutreachAdvisorSendRequest,
  OutreachDraft,
  OutreachDraftRequest,
  OutreachInvestorDraftRequest,
  OutreachInvestorSendRequest,
  OutreachSendDetail,
  OutreachSendRequest,
  OutreachSendResponse,
  OutreachSendStatus,
  OutreachSendsListResponse,
  OutreachSendsScope,
  OutreachSignature,
  RecipientSearchResponse,
  FirmSearchResponse,
  FirmContactsResponse,
  FavoriteSearchResponse,
  FavoriteFirmsResponse,
  PipelineRunItem,
  PipelineStatusResponse,
  PipelineTriggerResponse,
  VaultFolder,
  VaultFolderCreate,
  VaultFolderFile,
  VaultFolderUpdate,
  WipeBdDataResponse
} from "@/lib/types";

// ── Investors tab (SEC Form 4 insider transactions) ───────────────────
export async function getInvestors(opts: {
  tab?: "buyers" | "sellers" | "all";
  ticker?: string;
  days?: number;
  minValue?: number;
  page?: number;
  limit?: number;
}): Promise<InvestorListResponse> {
  return apiRequest<InvestorListResponse>(
    buildApiPath("/api/v1/investors", {
      tab: opts.tab && opts.tab !== "all" ? opts.tab : undefined,
      ticker: opts.ticker || undefined,
      days: opts.days,
      min_value: opts.minValue,
      page: opts.page,
      limit: opts.limit
    })
  );
}

export async function enrichInvestor(
  id: number
): Promise<InvestorEnrichResponse> {
  return apiRequest<InvestorEnrichResponse>(`/api/v1/investors/${id}/enrich`, {
    method: "POST"
  });
}

export async function getFavoriteLists(): Promise<FavoriteList[]> {
  return apiRequest<FavoriteList[]>("/api/v1/favorite-lists");
}

export async function getFavoriteListItems(
  listId: number,
  page: number,
  pageSize: number
): Promise<PaginatedFavoriteListItems> {
  return apiRequest<PaginatedFavoriteListItems>(
    buildApiPath(`/api/v1/favorite-lists/${listId}/items`, {
      page,
      page_size: pageSize
    })
  );
}

export async function createFavoriteList(name: string): Promise<FavoriteList> {
  return apiRequest<FavoriteList>("/api/v1/favorite-lists", {
    method: "POST",
    body: JSON.stringify({ name })
  });
}

export async function renameFavoriteList(
  listId: number,
  name: string
): Promise<FavoriteList> {
  return apiRequest<FavoriteList>(`/api/v1/favorite-lists/${listId}`, {
    method: "PUT",
    body: JSON.stringify({ name })
  });
}

export async function deleteFavoriteList(listId: number): Promise<void> {
  await apiRequest<void>(`/api/v1/favorite-lists/${listId}`, {
    method: "DELETE"
  });
}

// ── Per-firm list membership (#17 phase 3) ────────────────────────────────
// The picker on master-list rows + the firm-detail header reads
// `getListsForFirm` to render checkboxes pre-flagged with current
// membership, then mutates via add/remove. POST/DELETE reuse the
// phase-2 items endpoints — no new BE there.

export async function getListsForFirm(
  firmId: number
): Promise<FavoriteListWithMembership[]> {
  return apiRequest<FavoriteListWithMembership[]>(
    `/api/v1/broker-dealers/${firmId}/favorite-lists`
  );
}

export async function addFirmToList(
  listId: number,
  firmId: number
): Promise<void> {
  await apiRequest<void>(`/api/v1/favorite-lists/${listId}/items`, {
    method: "POST",
    body: JSON.stringify({ broker_dealer_id: firmId })
  });
}

export async function removeFirmFromList(
  listId: number,
  firmId: number
): Promise<void> {
  await apiRequest<void>(
    `/api/v1/favorite-lists/${listId}/items/${firmId}`,
    { method: "DELETE" }
  );
}

// Bulk-add for the master-list multi-select picker. Idempotent — already-
// present ids land in `skipped_existing`; non-existent firm ids in
// `skipped_unknown` rather than aborting the batch. Capped server-side at
// 200 ids per call.
export interface AddFirmsToListBatchResponse {
  added: number;
  skipped_existing: number;
  skipped_unknown: number[];
}

export async function addFirmsToListBatch(
  listId: number,
  firmIds: number[]
): Promise<AddFirmsToListBatchResponse> {
  return apiRequest<AddFirmsToListBatchResponse>(
    `/api/v1/favorite-lists/${listId}/items/batch`,
    {
      method: "POST",
      body: JSON.stringify({ broker_dealer_ids: firmIds }),
    }
  );
}

// ── Investment-advisor variants (favorites for advisor-list) ──────────────
// Parallel to the BD-side helpers above. The BE has separate endpoints
// at /advisor-items (vs /items) so the polymorphic XOR check on
// favorite_list_item is satisfied: advisor rows write advisor_id and
// leave broker_dealer_id NULL.

export async function getListsForAdvisor(
  advisorId: number
): Promise<FavoriteListWithMembership[]> {
  return apiRequest<FavoriteListWithMembership[]>(
    `/api/v1/investment-advisors/${advisorId}/favorite-lists`
  );
}

export async function addAdvisorToList(
  listId: number,
  advisorId: number
): Promise<void> {
  await apiRequest<void>(
    `/api/v1/favorite-lists/${listId}/advisor-items`,
    {
      method: "POST",
      body: JSON.stringify({ advisor_id: advisorId }),
    }
  );
}

export async function removeAdvisorFromList(
  listId: number,
  advisorId: number
): Promise<void> {
  await apiRequest<void>(
    `/api/v1/favorite-lists/${listId}/advisor-items/${advisorId}`,
    { method: "DELETE" }
  );
}

export async function addAdvisorsToListBatch(
  listId: number,
  advisorIds: number[]
): Promise<AddFirmsToListBatchResponse> {
  return apiRequest<AddFirmsToListBatchResponse>(
    `/api/v1/favorite-lists/${listId}/advisor-items/batch`,
    {
      method: "POST",
      body: JSON.stringify({ advisor_ids: advisorIds }),
    }
  );
}

// Same-origin proxy GET; BE 302s to the latest 13F-HR primary document
// on SEC EDGAR. Use as an <a href> so right-click "Open in new tab" works.
export function getInvestmentAdvisorLatest13fPath(advisorId: number): string {
  return `/api/backend/api/v1/investment-advisors/${advisorId}/13f/latest`;
}

// ── Institutional Investor variants (favorites for /investors firm list) ──
// Parallel to BD + advisor helpers; the BE has /investor-items endpoints
// that satisfy the 3-way XOR on favorite_list_item by writing
// institutional_investor_id and leaving the other two FKs NULL.

export async function getListsForInstitutionalInvestor(
  investorId: number
): Promise<FavoriteListWithMembership[]> {
  return apiRequest<FavoriteListWithMembership[]>(
    `/api/v1/institutional-investors/${investorId}/favorite-lists`
  );
}

export async function addInstitutionalInvestorToList(
  listId: number,
  investorId: number
): Promise<void> {
  await apiRequest<void>(
    `/api/v1/favorite-lists/${listId}/investor-items`,
    {
      method: "POST",
      body: JSON.stringify({ institutional_investor_id: investorId }),
    }
  );
}

export async function removeInstitutionalInvestorFromList(
  listId: number,
  investorId: number
): Promise<void> {
  await apiRequest<void>(
    `/api/v1/favorite-lists/${listId}/investor-items/${investorId}`,
    { method: "DELETE" }
  );
}

export async function addInstitutionalInvestorsToListBatch(
  listId: number,
  investorIds: number[]
): Promise<AddFirmsToListBatchResponse> {
  return apiRequest<AddFirmsToListBatchResponse>(
    `/api/v1/favorite-lists/${listId}/investor-items/batch`,
    {
      method: "POST",
      body: JSON.stringify({ institutional_investor_ids: investorIds }),
    }
  );
}

// ── Reporting-owner (Form 4 insider) variants ─────────────────────────────
// Insiders are addressed by CIK (string), not a surrogate id: the
// /investors feed only carries the CIK and the ``reporting_owners`` row
// is lazy-created on first favorite. The membership lookup lives under
// the /investors router (the owner isn't a firm), while add/remove sit on
// /favorite-lists like the other types. ``addReportingOwnerToList``
// returns the resolved ``reporting_owner_id`` so a row that had none can
// be un-favorited (DELETE by id) without re-resolving the CIK.

export async function getListsForReportingOwner(
  cik: string
): Promise<FavoriteListWithMembership[]> {
  return apiRequest<FavoriteListWithMembership[]>(
    `/api/v1/investors/reporting-owners/${encodeURIComponent(cik)}/favorite-lists`
  );
}

export async function addReportingOwnerToList(
  listId: number,
  cik: string
): Promise<ReportingOwnerItemAddResponse> {
  return apiRequest<ReportingOwnerItemAddResponse>(
    `/api/v1/favorite-lists/${listId}/reporting-owner-items`,
    {
      method: "POST",
      body: JSON.stringify({ cik }),
    }
  );
}

export async function removeReportingOwnerFromList(
  listId: number,
  reportingOwnerId: number
): Promise<void> {
  await apiRequest<void>(
    `/api/v1/favorite-lists/${listId}/reporting-owner-items/${reportingOwnerId}`,
    { method: "DELETE" }
  );
}

// ── Cross-entity contact search ────────────────────────────────────────
// Both POST endpoints accept JSON bodies. find-by-email optionally
// triggers an Apollo /people/match fallback when ``enrich_via_apollo``
// is true and no local hit was found -- caller opts in to spend.

export async function findContactsByEmail(
  email: string,
  options?: { enrichViaApollo?: boolean }
): Promise<ContactSearchResponse> {
  return apiRequest<ContactSearchResponse>("/api/v1/contacts/find-by-email", {
    method: "POST",
    body: JSON.stringify({
      email,
      enrich_via_apollo: options?.enrichViaApollo ?? false,
    }),
  });
}

export async function findContactsByDomain(
  domain: string
): Promise<ContactSearchResponse> {
  return apiRequest<ContactSearchResponse>("/api/v1/contacts/find-by-domain", {
    method: "POST",
    body: JSON.stringify({ domain }),
  });
}

// ── Adjacent-entity navigation (Next button on detail pages) ──────────
// All three endpoints return {prev_id, next_id} walking the default
// sorted list for their respective firm type. FE detail pages call
// these as a fallback when no return-envelope reconstructs the user's
// filtered list.

export async function getAdjacentAdvisor(advisorId: number): Promise<AdjacentResponse> {
  return apiRequest<AdjacentResponse>(
    `/api/v1/investment-advisors/${advisorId}/adjacent`
  );
}

export async function getAdjacentInstitutionalInvestor(
  investorId: number
): Promise<AdjacentResponse> {
  return apiRequest<AdjacentResponse>(
    `/api/v1/institutional-investors/${investorId}/adjacent`
  );
}

// ── Polymorphic outreach (advisor + investor) ──────────────────────────
// Parallel to the BD-side outreach helpers in lib/email-extractor.ts /
// existing draft helpers. Each pair (draft + send) targets the matching
// /outreach/{advisor,investor}-{draft,send} endpoint.

export async function generateAdvisorOutreachDraft(
  payload: OutreachAdvisorDraftRequest
): Promise<OutreachDraft> {
  return apiRequest<OutreachDraft>("/api/v1/outreach/advisor-draft", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function sendAdvisorOutreach(
  payload: OutreachAdvisorSendRequest
): Promise<OutreachSendResponse> {
  return apiRequest<OutreachSendResponse>("/api/v1/outreach/advisor-send", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function generateInvestorOutreachDraft(
  payload: OutreachInvestorDraftRequest
): Promise<OutreachDraft> {
  return apiRequest<OutreachDraft>("/api/v1/outreach/investor-draft", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function sendInvestorOutreach(
  payload: OutreachInvestorSendRequest
): Promise<OutreachSendResponse> {
  return apiRequest<OutreachSendResponse>("/api/v1/outreach/investor-send", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── Institutional Investors list + profile ─────────────────────────────
// Mirrors the advisor-list helpers. Filters are query-string params; the
// list endpoint defaults to total_aum-DESC ordering, the FE workspace
// client overrides per user preference.

export async function fetchInstitutionalInvestors(
  params: {
    q?: string;
    state?: string[];
    status?: string[];
    min_total_aum?: number;
    max_total_aum?: number;
    filed_13f_after?: string;
    filed_13f_before?: string;
    only_with_advisor_link?: boolean;
    sort_by?: string;
    sort_dir?: "asc" | "desc";
    page?: number;
    limit?: number;
  } = {}
): Promise<InstitutionalInvestorListResponse> {
  return apiRequest<InstitutionalInvestorListResponse>(
    buildApiPath("/api/v1/institutional-investors", params as Record<string, string | number | boolean | string[] | undefined>)
  );
}

export async function fetchInstitutionalInvestorProfile(
  investorId: number | string
): Promise<InstitutionalInvestorProfileResponse> {
  return apiRequest<InstitutionalInvestorProfileResponse>(
    `/api/v1/institutional-investors/${investorId}/profile`
  );
}

export async function fetchInstitutionalInvestorStates(): Promise<string[]> {
  return apiRequest<string[]>("/api/v1/institutional-investors/states");
}

// ── Tier 2 pipeline triggers ──────────────────────────────────────────────
// Pairs with cli01 BE PR feature/be-pipeline-endpoints-tier2 which exposes
// admin-OR-SA-OIDC trigger endpoints for the three long-running pipelines.
// The /settings/pipelines admin UI calls these via the cookie-session path;
// Cloud Scheduler hits the same endpoints with SA OIDC for the cadence runs.
// apiRequest already sends `credentials: "include"`, so admin role is
// enforced by the BE on the cookie path.

export async function runFilingMonitor(): Promise<PipelineTriggerResponse> {
  return apiRequest<PipelineTriggerResponse>(
    "/api/v1/pipeline/run/filing-monitor",
    { method: "POST" }
  );
}

export async function runPopulateAll(): Promise<PipelineTriggerResponse> {
  return apiRequest<PipelineTriggerResponse>(
    "/api/v1/pipeline/run/populate-all",
    { method: "POST" }
  );
}

export async function runInitialLoad(): Promise<PipelineTriggerResponse> {
  return apiRequest<PipelineTriggerResponse>(
    "/api/v1/pipeline/run/initial-load",
    { method: "POST" }
  );
}

// ── Fresh Regen (cli02 FE-1) ──────────────────────────────────────────────
// POST /api/v1/pipeline/wipe-bd-data is destructive: it deletes all BD
// data inside an audited transaction and returns the affected tables +
// row count. Pairs with cli01 BE PR feature/be-pipeline-wipe-bd-data.
//
// The BE rejects the call with 400 if `confirmation` doesn't match
// `WIPE-BD-DATA-{TODAY-UTC}` (today's UTC date) and 403 for non-admin
// callers. The FE generates the expected string client-side and shows
// it in the confirmation modal; if the user's clock is off the BE
// rejection surfaces inline so the mismatch is obvious.

export async function wipeBdData(
  confirmation: string
): Promise<WipeBdDataResponse> {
  return apiRequest<WipeBdDataResponse>("/api/v1/pipeline/wipe-bd-data", {
    method: "POST",
    body: JSON.stringify({ confirmation })
  });
}

// Poll helper for the chained Fresh Regen flow: after kicking off
// initial_load or populate_all, we re-fetch /pipeline/clearing and
// look up our run by id in `recent_runs`. The BE already orders that
// list newest-first, so this scan stays cheap. Returns null when the
// run hasn't appeared yet (BE briefly delays surfacing it after
// trigger).
export async function findPipelineRun(
  runId: number
): Promise<PipelineRunItem | null> {
  const status = await apiRequest<PipelineStatusResponse>(
    "/api/v1/pipeline/clearing"
  );
  return status.recent_runs.find((run) => run.id === runId) ?? null;
}

// ── Fresh Regen Phase 0 — Files API flag flip (cli02 FE-1 follow-up) ─────
// Pairs with cli01 BE PR feature/be-pipeline-set-files-api-flag. POST
// flips LLM_USE_FILES_API at the BE Cloud Run service level and waits
// for the new revision to roll out (~60-90s). 503 means the rollout
// timed out and the FE should let the admin retry or opt out by
// unchecking the toggle. 403 means non-admin caller.
export type SetFilesApiFlagResponse = {
  previous_state: boolean;
  new_state: boolean;
  revision_name: string;
  ready_at: string;
};

export async function setFilesApiFlag(
  enabled: boolean
): Promise<SetFilesApiFlagResponse> {
  return apiRequest<SetFilesApiFlagResponse>(
    "/api/v1/pipeline/set-files-api-flag",
    {
      method: "POST",
      body: JSON.stringify({ enabled })
    }
  );
}

// ── Lazy firm-website resolver (cli02 FE-1) ──────────────────────────────
// Pairs with cli01 BE PR feature/be-firm-website-resolver. The detail page
// fires this in the background when bd.website is null; the BE walks an
// Apollo → Hunter waterfall and persists the winner. Failure surfaces as a
// non-2xx; the caller swallows it so the Google fallback stays put.
export type WebsiteSource = "finra" | "apollo" | "hunter" | "serpapi";

export type ResolveWebsiteResponse = {
  website: string | null;
  website_source: WebsiteSource | null;
  resolved_at: string | null;
  reason?: string;
};

export async function resolveWebsite(
  firmId: number
): Promise<ResolveWebsiteResponse> {
  return apiRequest<ResolveWebsiteResponse>(
    `/api/v1/broker-dealers/${firmId}/resolve-website`,
    { method: "POST" }
  );
}

// ── Per-firm refresh-all trigger + run polling ──────────────────────────
// Pairs with the BE's per-firm orchestrator endpoint that fans out to the
// four per-firm sub-pipelines (financials / health-check / resolve-website
// / enrich) but only runs the ones whose target fields are missing on the
// BD record. Cost matches the gap.
//
// Three response shapes:
//   1. 200 + status="skipped" + run_id=null — already complete, no work
//      done, no polling needed. Caller can immediately router.refresh().
//   2. 202 + status="queued" + run_id=<parent> — at least one child
//      sub-pipeline ran. Caller polls /pipeline/run/{run_id} until
//      terminal status.
//   3. 409 + detail.run_id — refresh-all already in flight. We normalize
//      this into the same success shape so callers always get a run_id
//      to poll against (or null + skipped, never an exception).
//
// 429 cooldown / 503 missing-key / 401 / 404 are surfaced as ApiError so
// the caller can render appropriate toasts.
export type RefreshFirmResponse = {
  // null when status === "skipped" (already complete, no run created).
  run_id: number | null;
  // "queued" | "skipped" | "running" (the last from 409 normalization).
  status: string;
  broker_dealer_id: number;
  // Only present on the 200 skipped path; surfaces "Already complete." or
  // similar so the caller can show a confirmation toast.
  reason?: string;
};

export type PipelineRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_errors"
  | "failed";

export type PipelineRunDetail = {
  run_id: number;
  pipeline_name: string;
  status: PipelineRunStatus;
  total_items: number;
  processed_items: number;
  success_count: number;
  failure_count: number;
  notes: string | null;
  started_at: string | null;
  completed_at: string | null;
};

type ConflictDetail = {
  run_id: number;
  status: string;
  broker_dealer_id: number;
};

function parseConflictDetail(detail: string): ConflictDetail | null {
  // apiRequest only unwraps `detail` when it's a string. FastAPI emits the
  // 409 body as `{"detail": {...}}` with detail as an object, so the raw
  // JSON body is what lands here. Try the nested .detail path first, then
  // fall back to the bare object shape just in case the BE flattens it.
  try {
    const parsed = JSON.parse(detail) as unknown;
    const candidates: unknown[] = [];
    if (parsed && typeof parsed === "object") {
      candidates.push(parsed);
      if (
        "detail" in parsed &&
        parsed !== null &&
        typeof (parsed as { detail: unknown }).detail === "object"
      ) {
        candidates.push((parsed as { detail: unknown }).detail);
      }
    }
    for (const candidate of candidates) {
      if (
        candidate &&
        typeof candidate === "object" &&
        "run_id" in candidate &&
        typeof (candidate as { run_id: unknown }).run_id === "number" &&
        "status" in candidate &&
        typeof (candidate as { status: unknown }).status === "string" &&
        "broker_dealer_id" in candidate &&
        typeof (candidate as { broker_dealer_id: unknown }).broker_dealer_id ===
          "number"
      ) {
        const obj = candidate as ConflictDetail;
        return {
          run_id: obj.run_id,
          status: obj.status,
          broker_dealer_id: obj.broker_dealer_id,
        };
      }
    }
  } catch {
    // Non-JSON detail — fall through.
  }
  return null;
}

export type RefreshScope = "all" | "list_only";

export async function refreshFirm(
  firmId: number,
  scope: RefreshScope = "all"
): Promise<RefreshFirmResponse> {
  try {
    return await apiRequest<RefreshFirmResponse>(
      `/api/v1/broker-dealers/${firmId}/refresh-all`,
      { method: "POST", body: JSON.stringify({ scope }) }
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      const conflict = parseConflictDetail(err.detail);
      if (conflict) {
        return {
          run_id: conflict.run_id,
          status: conflict.status,
          broker_dealer_id: conflict.broker_dealer_id,
        };
      }
    }
    throw err;
  }
}

// ── Per-advisor refresh-all (IA analog of refreshFirm) ──────────────────────
// Pairs with the BE endpoint POST /investment-advisors/{id}/refresh-all
// (backend/app/api/v1/endpoints/investment_advisors.py). Same response
// shape contract as refreshFirm so the FE detail client at
// frontend/components/advisor-list/advisor-detail-client.tsx can mirror
// the BD detail client's polling / 409 / skipped handling almost verbatim.
//
// The poll endpoint (getPipelineRunStatus -> /api/v1/pipeline/run/{run_id})
// is the same for both BD and IA — both write to the shared pipeline_runs
// table.
export type RefreshAdvisorResponse = {
  // null when status === "skipped" (no PipelineRun created).
  run_id: number | null;
  // "queued" | "skipped" | "running" (the last from 409 normalization).
  status: string;
  advisor_id: number;
  // Only present on the 200 skipped path.
  reason?: string;
};

type AdvisorConflictDetail = {
  run_id: number;
  status: string;
  advisor_id: number;
};

function parseAdvisorConflictDetail(detail: string): AdvisorConflictDetail | null {
  try {
    const parsed = JSON.parse(detail) as unknown;
    const candidates: unknown[] = [];
    if (parsed && typeof parsed === "object") {
      candidates.push(parsed);
      if (
        "detail" in parsed &&
        parsed !== null &&
        typeof (parsed as { detail: unknown }).detail === "object"
      ) {
        candidates.push((parsed as { detail: unknown }).detail);
      }
    }
    for (const candidate of candidates) {
      if (
        candidate &&
        typeof candidate === "object" &&
        "run_id" in candidate &&
        typeof (candidate as { run_id: unknown }).run_id === "number" &&
        "status" in candidate &&
        typeof (candidate as { status: unknown }).status === "string" &&
        "advisor_id" in candidate &&
        typeof (candidate as { advisor_id: unknown }).advisor_id === "number"
      ) {
        const obj = candidate as AdvisorConflictDetail;
        return {
          run_id: obj.run_id,
          status: obj.status,
          advisor_id: obj.advisor_id,
        };
      }
    }
  } catch {
    // Non-JSON detail — fall through.
  }
  return null;
}

export async function refreshAdvisor(
  advisorId: number,
  scope: "all" = "all"
): Promise<RefreshAdvisorResponse> {
  try {
    return await apiRequest<RefreshAdvisorResponse>(
      `/api/v1/investment-advisors/${advisorId}/refresh-all`,
      { method: "POST", body: JSON.stringify({ scope }) }
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      const conflict = parseAdvisorConflictDetail(err.detail);
      if (conflict) {
        return {
          run_id: conflict.run_id,
          status: conflict.status,
          advisor_id: conflict.advisor_id,
        };
      }
    }
    throw err;
  }
}

export async function gapFillAdvisorContacts(
  advisorId: number
): Promise<RefreshAdvisorResponse> {
  return apiRequest<RefreshAdvisorResponse>(
    `/api/v1/investment-advisors/${advisorId}/gap-fill-contacts`,
    { method: "POST" }
  );
}

export async function getPipelineRunStatus(
  runId: number
): Promise<PipelineRunDetail> {
  return apiRequest<PipelineRunDetail>(`/api/v1/pipeline/run/${runId}`, {
    method: "GET",
  });
}

// ── Vault folders + Outreach drafts (MVP) ─────────────────────────────────
// Backs the /vault folder-CRUD UI and the Outreach modal on
// /master-list/{id}. Folders are per-user (the BE filters on the session
// user); the Outreach endpoint validates the (folder, BD, contact) triple
// before calling Gemini Flash.

export async function listVaultFolders(): Promise<VaultFolder[]> {
  return apiRequest<VaultFolder[]>("/api/v1/vault/folders");
}

export async function createVaultFolder(
  payload: VaultFolderCreate
): Promise<VaultFolder> {
  return apiRequest<VaultFolder>("/api/v1/vault/folders", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function updateVaultFolder(
  folderId: number,
  payload: VaultFolderUpdate
): Promise<VaultFolder> {
  return apiRequest<VaultFolder>(`/api/v1/vault/folders/${folderId}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function deleteVaultFolder(folderId: number): Promise<void> {
  await apiRequest<void>(`/api/v1/vault/folders/${folderId}`, {
    method: "DELETE"
  });
}

export async function generateOutreachDraft(
  payload: OutreachDraftRequest
): Promise<OutreachDraft> {
  return apiRequest<OutreachDraft>("/api/v1/outreach/draft", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

// Adhoc draft path — used by /outreach/sent?tab=create when the
// recipient is a typed-in email with no contact record. Unlike the
// contact-keyed draft endpoints, this only requires (folder_id,
// recipient_email) — Gemini works from the service folder + RAG plus
// the optional recipient name.
export async function generateAdhocOutreachDraft(
  payload: OutreachAdhocDraftRequest
): Promise<OutreachDraft> {
  return apiRequest<OutreachDraft>("/api/v1/outreach/adhoc-draft", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

// ── Send outreach via the user's chosen provider ──────────────────────────
// POST /api/v1/outreach/send transmits the (possibly user-edited) draft
// through the provider the user picked (or Gmail by default if no
// provider is set). 412 responses are recoverable by the modal — each
// provider has its own pair:
//   - "<provider>_account_not_linked" → linkSocial without the send scope
//   - "<provider>_scope_required" / "gmail_scope_required" → linkSocial WITH the send scope
// Both flows trigger the provider's incremental consent popup.
export async function sendOutreachEmail(
  payload: OutreachSendRequest
): Promise<OutreachSendResponse> {
  return apiRequest<OutreachSendResponse>("/api/v1/outreach/send", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

// Which email providers the caller has linked + whether each one
// already has the send scope. Drives the Outreach modal picker.
export async function getLinkedProviders(): Promise<LinkedProvidersResponse> {
  return apiRequest<LinkedProvidersResponse>(
    "/api/v1/outreach/linked-providers"
  );
}

// The caller's saved outreach signature (footer). Returns { signature: "" }
// when unset. Read by the compose surfaces to prefill the Footer field and
// by the account-settings editor.
export async function getOutreachSignature(): Promise<OutreachSignature> {
  return apiRequest<OutreachSignature>("/api/v1/outreach/signature");
}

// Upsert the caller's outreach signature. Empty string clears it.
export async function updateOutreachSignature(
  signature: string
): Promise<OutreachSignature> {
  return apiRequest<OutreachSignature>("/api/v1/outreach/signature", {
    method: "PUT",
    body: JSON.stringify({ signature })
  });
}

// List of outreach sends (success + failure). Body is omitted from the
// list response to keep the payload small — call getOutreachSend when
// expanding a row. ``scope`` defaults to "mine" (caller's own sends);
// admins can pass "all" to fetch every user's sends with a Sender
// column populated.
export async function listOutreachSends(opts: {
  limit?: number;
  offset?: number;
  status?: OutreachSendStatus;
  scope?: OutreachSendsScope;
}): Promise<OutreachSendsListResponse> {
  return apiRequest<OutreachSendsListResponse>(
    buildApiPath("/api/v1/outreach/sends", {
      limit: opts.limit,
      offset: opts.offset,
      status: opts.status,
      scope: opts.scope
    })
  );
}

export async function getOutreachSend(
  sendId: number,
  scope?: OutreachSendsScope
): Promise<OutreachSendDetail> {
  return apiRequest<OutreachSendDetail>(
    buildApiPath(`/api/v1/outreach/sends/${sendId}`, { scope })
  );
}

// ── /outreach/sent?tab=create surface ─────────────────────────────────
// Backs the recipient combobox (search across all three contact tables)
// and the free-form-email send path for the new Create Outreach tab.

export async function searchOutreachContacts(
  query: string,
  limit = 20
): Promise<RecipientSearchResponse> {
  return apiRequest<RecipientSearchResponse>(
    buildApiPath("/api/v1/outreach/contacts/search", { q: query, limit })
  );
}

// Firm-name autocomplete for the recipient picker. Picking a firm
// opens the firm-contacts modal; see listFirmContacts.
export async function searchOutreachFirms(
  query: string,
  limit = 20
): Promise<FirmSearchResponse> {
  return apiRequest<FirmSearchResponse>(
    buildApiPath("/api/v1/outreach/firms/search", { q: query, limit })
  );
}

export async function listFirmContacts(
  entityKind: "broker_dealer" | "advisor" | "institutional_investor",
  entityId: number
): Promise<FirmContactsResponse> {
  return apiRequest<FirmContactsResponse>(
    buildApiPath("/api/v1/outreach/firms/contacts", {
      entity_kind: entityKind,
      entity_id: entityId
    })
  );
}

// Favorite-list autocomplete + drill-down. The list-firms response is
// shaped like a firm-search response so the picker modal can reuse the
// firm-row rendering before drilling into firm-contacts.
export async function searchOutreachFavorites(
  query: string,
  limit = 20
): Promise<FavoriteSearchResponse> {
  return apiRequest<FavoriteSearchResponse>(
    buildApiPath("/api/v1/outreach/favorites/search", { q: query, limit })
  );
}

export async function listFavoriteFirms(
  listId: number
): Promise<FavoriteFirmsResponse> {
  return apiRequest<FavoriteFirmsResponse>(
    `/api/v1/outreach/favorites/${listId}/firms`
  );
}

export async function sendAdhocOutreach(
  payload: OutreachAdhocSendRequest
): Promise<OutreachSendResponse> {
  return apiRequest<OutreachSendResponse>("/api/v1/outreach/adhoc-send", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

// Admin-only flat view of every firm a target user has saved across all
// their favorite lists. Backend gates with role === "admin"; a non-admin
// caller will receive 403 from apiRequest as an ApiError.
export async function getUserSavedFirms(
  userId: string,
  opts?: { limit?: number; offset?: number; listId?: number }
): Promise<AdminUserSavedFirmsResponse> {
  return apiRequest<AdminUserSavedFirmsResponse>(
    buildApiPath(`/api/v1/users/${userId}/saved-firms`, {
      limit: opts?.limit,
      offset: opts?.offset,
      list_id: opts?.listId
    })
  );
}

// Admin-only unified activity feed for one user (logins/logouts, firm
// views, saves, outreach sends). `type` collapses login + logout under
// one filter chip BE-side; the row's own event_type preserves the
// discriminator for the FE glyph. 403 if the caller isn't an admin.
export async function getUserActivities(
  userId: string,
  opts?: {
    limit?: number;
    offset?: number;
    type?: AdminUserActivityFilter | undefined;
  }
): Promise<AdminUserActivitiesResponse> {
  return apiRequest<AdminUserActivitiesResponse>(
    buildApiPath(`/api/v1/users/${userId}/activities`, {
      limit: opts?.limit,
      offset: opts?.offset,
      type: opts?.type
    })
  );
}

// ── Vault folder file uploads ─────────────────────────────────────────────
// Multipart upload of one file per call. Async-processed server-side; the
// caller polls listVaultFiles() until each row's processing_status is
// "ready" or "failed". 10MB / 20 files-per-folder / 100MB-per-user caps
// surface as 413 / 409 respectively.
export async function uploadVaultFile(
  folderId: number,
  file: File
): Promise<VaultFolderFile> {
  const formData = new FormData();
  formData.append("file", file, file.name);

  // apiRequest sets Content-Type: application/json by default — strip it
  // here so the browser can set the multipart boundary itself.
  const url = `${resolveApiBaseUrl()}/api/v1/vault/folders/${folderId}/files`;
  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    body: formData
  });

  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    if (text) {
      try {
        const parsed = JSON.parse(text) as unknown;
        if (
          parsed &&
          typeof parsed === "object" &&
          "detail" in parsed &&
          typeof (parsed as { detail: unknown }).detail === "string"
        ) {
          detail = (parsed as { detail: string }).detail;
        }
      } catch {
        // Non-JSON body — fall back to raw text.
      }
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as VaultFolderFile;
}

export async function listVaultFiles(
  folderId: number
): Promise<VaultFolderFile[]> {
  return apiRequest<VaultFolderFile[]>(
    `/api/v1/vault/folders/${folderId}/files`
  );
}

export async function deleteVaultFile(
  folderId: number,
  fileId: number
): Promise<void> {
  await apiRequest<void>(
    `/api/v1/vault/folders/${folderId}/files/${fileId}`,
    { method: "DELETE" }
  );
}

// Download is a same-origin proxy GET that the BE 302s to a signed GCS URL.
// Returning the path lets the FE either window.open() or use an <a download>.
export function getVaultFileDownloadPath(
  folderId: number,
  fileId: number
): string {
  return `/api/backend/api/v1/vault/folders/${folderId}/files/${fileId}/download`;
}

export async function retryVaultFile(
  folderId: number,
  fileId: number
): Promise<VaultFolderFile> {
  return apiRequest<VaultFolderFile>(
    `/api/v1/vault/folders/${folderId}/files/${fileId}/retry`,
    { method: "POST" }
  );
}

// ── Clearing-membership admin review queue ────────────────────────────
// Surfaces `status='needs_review'` rows from the directory importer (the
// safety path for ambiguous name matches) so an admin can approve the
// correct candidate or reject a wrong one. Approve flips the row to
// `match_method='manual'` server-side so re-imports preserve the decision.

export async function getClearingMembershipReviewQueue(opts: {
  limit?: number;
  offset?: number;
} = {}): Promise<ClearingMembershipReviewListResponse> {
  return apiRequest<ClearingMembershipReviewListResponse>(
    buildApiPath("/api/v1/clearing-memberships/review", {
      limit: opts.limit,
      offset: opts.offset,
    })
  );
}

export async function approveClearingMembership(
  membershipId: number
): Promise<ClearingMembershipDecisionResponse> {
  return apiRequest<ClearingMembershipDecisionResponse>(
    `/api/v1/clearing-memberships/${membershipId}/approve`,
    { method: "POST" }
  );
}

export async function rejectClearingMembership(
  membershipId: number
): Promise<ClearingMembershipDecisionResponse> {
  return apiRequest<ClearingMembershipDecisionResponse>(
    `/api/v1/clearing-memberships/${membershipId}/reject`,
    { method: "POST" }
  );
}

// ── Doxie chatbot (in-app Gemini-backed assistant) ────────────────────────
// Backs the ChatbotWidget. The endpoint expects a non-empty conversation
// history terminated by a user message; the BE folds the optional
// page-context into the system prompt so Doxie can reason about where
// the user is in the app without per-route summary fetches.

export type DoxieChatRole = "user" | "assistant";

export interface DoxieChatMessage {
  role: DoxieChatRole;
  content: string;
}

export interface DoxiePageContext {
  path?: string;
  title?: string;
}

export async function sendDoxieMessage(
  messages: DoxieChatMessage[],
  pageContext?: DoxiePageContext
): Promise<string> {
  const body: { messages: DoxieChatMessage[]; page_context?: DoxiePageContext } = {
    messages
  };
  if (pageContext && (pageContext.path || pageContext.title)) {
    body.page_context = pageContext;
  }
  const response = await apiRequest<{ reply: string }>(
    "/api/v1/chatbot/messages",
    {
      method: "POST",
      body: JSON.stringify(body)
    }
  );
  return response.reply;
}

// Persisted history for the user's active conversation. Empty messages
// array on first open (BE creates the conversation lazily).
export interface DoxieHistoryMessage {
  id: number;
  role: DoxieChatRole;
  content: string;
  created_at: string;
}

export interface DoxieHistoryResponse {
  conversation_id: number;
  messages: DoxieHistoryMessage[];
}

export async function loadDoxieHistory(): Promise<DoxieHistoryResponse> {
  return apiRequest<DoxieHistoryResponse>("/api/v1/chatbot/messages", {
    method: "GET"
  });
}

// Archive the current Doxie conversation and start a fresh one. The
// returned conversation_id is informational — the FE just needs to know
// the archive succeeded so it can clear the message list.
export async function startNewDoxieChat(): Promise<number> {
  const response = await apiRequest<{ conversation_id: number }>(
    "/api/v1/chatbot/conversations/new",
    { method: "POST" }
  );
  return response.conversation_id;
}

// ── Doxie streaming chat (SSE) ────────────────────────────────────────────
// Events emitted by the BE stream endpoint. Mirrors the dicts yielded by
// `ChatbotService.chat_stream` (see backend/app/services/chatbot.py).
export type DoxieStreamEvent =
  | { type: "text_delta"; text: string }
  | { type: "tool_call"; name: string }
  | { type: "tool_result"; name: string; error?: string | null }
  | { type: "done"; reply: string }
  | { type: "error"; code: string; message: string };

export interface StreamDoxieMessageOptions {
  messages: DoxieChatMessage[];
  pageContext?: DoxiePageContext;
  signal?: AbortSignal;
  onEvent: (event: DoxieStreamEvent) => void;
}

// Native EventSource is GET-only and can't carry a request body, so we
// roll our own POST → SSE reader with `fetch` + a streaming reader. SSE
// framing: events are separated by a blank line; ``data:`` lines carry
// the JSON payload. The BE sets `Cache-Control: no-cache` +
// `X-Accel-Buffering: no` so Cloud Run / Next.js don't buffer.
export async function streamDoxieMessage({
  messages,
  pageContext,
  signal,
  onEvent
}: StreamDoxieMessageOptions): Promise<void> {
  const body: { messages: DoxieChatMessage[]; page_context?: DoxiePageContext } = {
    messages
  };
  if (pageContext && (pageContext.path || pageContext.title)) {
    body.page_context = pageContext;
  }

  const url = `${resolveApiBaseUrl()}/api/v1/chatbot/messages/stream`;
  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream"
    },
    body: JSON.stringify(body),
    signal
  });

  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text) as unknown;
      if (
        parsed &&
        typeof parsed === "object" &&
        "detail" in parsed &&
        typeof (parsed as { detail: unknown }).detail === "string"
      ) {
        detail = (parsed as { detail: string }).detail;
      }
    } catch {
      // Non-JSON body — fall back to raw text.
    }
    throw new ApiError(response.status, detail);
  }

  if (!response.body) {
    throw new ApiError(0, "Doxie stream returned no body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  // Buffer carries incomplete chunks across reads — SSE events can be
  // split arbitrarily across TCP segments.
  let buffer = "";

  function dispatchBuffered(): void {
    // Process every complete event (blank-line terminated) in the buffer.
    // Anything after the last delimiter is a partial event for the next
    // read to complete.
    let separatorIdx = buffer.indexOf("\n\n");
    while (separatorIdx !== -1) {
      const rawEvent = buffer.slice(0, separatorIdx);
      buffer = buffer.slice(separatorIdx + 2);
      separatorIdx = buffer.indexOf("\n\n");

      const dataLines: string[] = [];
      for (const line of rawEvent.split("\n")) {
        if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trimStart());
        }
        // Ignore comment lines (": keep-alive") and event-type lines —
        // the BE only emits `data:` events.
      }
      if (dataLines.length === 0) continue;
      const payload = dataLines.join("\n");
      try {
        const event = JSON.parse(payload) as DoxieStreamEvent;
        onEvent(event);
      } catch {
        // A malformed event shouldn't kill the stream; skip and continue.
      }
    }
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    dispatchBuffered();
  }
  // Flush any final bytes left in the decoder + dispatch any trailing
  // complete events (rare — the BE always terminates with a blank line).
  buffer += decoder.decode();
  dispatchBuffered();
}
