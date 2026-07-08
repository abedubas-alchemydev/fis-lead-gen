import { apiRequest } from "@/lib/api";
import type {
  PublicShareItemsResponse,
  PublicShareMeta,
  PublicShareProfileResponse,
} from "@/types/public-share";

// Public (unauthenticated) share endpoints. All calls go through the
// existing BFF proxy via apiRequest, which already sends
// `credentials: "include"` — that's what carries the HttpOnly unlock
// cookie the /unlock endpoint sets. The client never reads that cookie;
// callers map ApiError statuses to phases instead:
//   401 → locked (cookie missing/expired/rotated)
//   403 → wrong password (unlock only)
//   404 → unknown or revoked link / item not in this share
//   429 → too many unlock attempts (cooldown)

function shareBasePath(token: string): string {
  return `/api/v1/public/shares/${encodeURIComponent(token)}`;
}

export async function getPublicShare(token: string): Promise<PublicShareMeta> {
  return apiRequest<PublicShareMeta>(shareBasePath(token));
}

// 200 means the cookie was set by the browser (Set-Cookie on the response);
// the body carries no state the client needs, so this resolves to void.
export async function unlockPublicShare(
  token: string,
  password: string
): Promise<void> {
  await apiRequest<unknown>(`${shareBasePath(token)}/unlock`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export async function getPublicShareItems(
  token: string
): Promise<PublicShareItemsResponse> {
  return apiRequest<PublicShareItemsResponse>(`${shareBasePath(token)}/items`);
}

export async function getPublicShareItemProfile(
  token: string,
  itemId: number
): Promise<PublicShareProfileResponse> {
  return apiRequest<PublicShareProfileResponse>(
    `${shareBasePath(token)}/items/${itemId}/profile`
  );
}
