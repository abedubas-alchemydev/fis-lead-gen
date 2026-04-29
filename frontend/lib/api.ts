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
  PaginatedFavoriteListItems
} from "@/types/favorite-list";

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
