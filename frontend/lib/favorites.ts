// Typed wrappers for the per-user favorite + visit endpoints added in the
// backend PR (#71). All calls flow through the BFF (`/api/backend/...`) with
// `credentials: 'include'` — see plans/favorites-and-visits-2026-04-24.md §2
// for the full contract.

import { apiRequest, buildApiPath } from "@/lib/api";

// ── Shared row shape ──────────────────────────────────────────────────────
// Mirrors `BrokerDealerSummary` in backend/app/schemas/broker_dealer.py —
// the 12-field slim projection used by both /favorites and /visits.
export interface BrokerDealerSummary {
  id: number;
  name: string;
  city: string | null;
  state: string | null;
  lead_score: number | null;
  lead_priority: string | null;
  current_clearing_partner: string | null;
  health_status: string | null;
  is_deficient: boolean;
  last_filing_date: string | null;
  latest_net_capital: number | null;
  yoy_growth: number | null;
}

export interface FavoriteListItem extends BrokerDealerSummary {
  favorited_at: string;
}

export interface VisitListItem extends BrokerDealerSummary {
  last_visited_at: string;
  visit_count: number;
}

export interface FavoriteListResponse {
  items: FavoriteListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface VisitListResponse {
  items: VisitListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface FavoriteResponse {
  favorited: boolean;
  favorited_at: string;
}

// ── No-content helper ─────────────────────────────────────────────────────
// DELETE /favorite and POST /visit both return 204. `apiRequest` always
// parses JSON, so these two cases use a thin direct-fetch variant that
// mirrors the same BFF base + credentials behavior.
function resolveBffBase(): string {
  if (typeof window !== "undefined") {
    return "/api/backend";
  }
  const appBaseUrl = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";
  return `${appBaseUrl.replace(/\/$/, "")}/api/backend`;
}

async function sendNoContent(path: string, method: "POST" | "DELETE"): Promise<void> {
  const response = await fetch(`${resolveBffBase()}${path}`, {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" }
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
}

// ── Wrappers ──────────────────────────────────────────────────────────────

export async function addFavorite(bdId: number): Promise<FavoriteResponse> {
  return apiRequest<FavoriteResponse>(`/api/v1/broker-dealers/${bdId}/favorite`, {
    method: "POST"
  });
}

export async function removeFavorite(bdId: number): Promise<void> {
  return sendNoContent(`/api/v1/broker-dealers/${bdId}/favorite`, "DELETE");
}

export async function recordVisit(bdId: number): Promise<void> {
  return sendNoContent(`/api/v1/broker-dealers/${bdId}/visit`, "POST");
}

export async function listFavorites(
  params: { limit?: number; offset?: number } = {}
): Promise<FavoriteListResponse> {
  return apiRequest<FavoriteListResponse>(
    buildApiPath("/api/v1/favorites", {
      limit: params.limit,
      offset: params.offset
    })
  );
}

export async function listVisits(
  params: { limit?: number; offset?: number } = {}
): Promise<VisitListResponse> {
  return apiRequest<VisitListResponse>(
    buildApiPath("/api/v1/visits", {
      limit: params.limit,
      offset: params.offset
    })
  );
}
