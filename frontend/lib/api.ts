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
  PaginatedFavoriteListItems
} from "@/types/favorite-list";
import type {
  PipelineRunItem,
  PipelineStatusResponse,
  PipelineTriggerResponse,
  WipeBdDataResponse
} from "@/lib/types";

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
