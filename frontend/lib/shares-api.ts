// Thin apiRequest wrappers for the admin DOX Share endpoints. Every route
// is admin-gated on the BE (403 for non-admin cookie sessions); failures
// surface as ApiError with .status + .detail like every other client in
// lib/api.ts, so dialogs can render the BE's message inline.

import { apiRequest } from "@/lib/api";
import type {
  CreateShareResponse,
  ListSharesResponse,
  RotatePasswordResponse,
  ShareDetailResponse,
  ShareSummary,
} from "@/types/lead-share";

export async function createShare(payload: {
  favorite_list_id: number;
  name?: string;
}): Promise<CreateShareResponse> {
  return apiRequest<CreateShareResponse>("/api/v1/shares", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listShares(): Promise<ListSharesResponse> {
  return apiRequest<ListSharesResponse>("/api/v1/shares");
}

export async function getShareDetail(
  id: number
): Promise<ShareDetailResponse> {
  return apiRequest<ShareDetailResponse>(`/api/v1/shares/${id}`);
}

// 409 when the share is revoked — unrevoke first.
export async function rotateSharePassword(
  id: number
): Promise<RotatePasswordResponse> {
  return apiRequest<RotatePasswordResponse>(
    `/api/v1/shares/${id}/rotate-password`,
    { method: "POST" }
  );
}

// 409 when already revoked.
export async function revokeShare(id: number): Promise<ShareSummary> {
  return apiRequest<ShareSummary>(`/api/v1/shares/${id}/revoke`, {
    method: "POST",
  });
}

// 409 when not revoked.
export async function unrevokeShare(id: number): Promise<ShareSummary> {
  return apiRequest<ShareSummary>(`/api/v1/shares/${id}/unrevoke`, {
    method: "POST",
  });
}

export async function deleteShare(id: number): Promise<void> {
  await apiRequest<void>(`/api/v1/shares/${id}`, { method: "DELETE" });
}

// Public URL for a share: prefer the BE-provided absolute `url`, else derive
// from the current origin + the public /share/{token} route. Every caller is
// a client component, so window exists in practice; the SSR guard returns a
// root-relative path rather than crashing if it ever runs early.
export function resolveShareUrl(
  share: Pick<ShareSummary, "url" | "token">
): string {
  if (share.url) return share.url;
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return `${origin}/share/${share.token}`;
}

// 400s from createShare carry either a plain-string detail or a structured
// {"code", "eligible", "max"} object. apiRequest only unwraps string details,
// so an object detail lands here as the raw JSON body text (same parsing
// dance as parseConflictDetail in lib/api.ts). Returns a user-facing message
// for the known share codes, or null so the caller falls back to showing the
// detail verbatim.
export function describeShareCreateErrorDetail(detail: string): string | null {
  const fromCode = (candidate: unknown): string | null => {
    if (!candidate || typeof candidate !== "object") return null;
    const code = (candidate as { code?: unknown }).code;
    if (code === "share_too_large") {
      const eligible = (candidate as { eligible?: unknown }).eligible;
      const max = (candidate as { max?: unknown }).max;
      const eligibleText =
        typeof eligible === "number" ? eligible.toLocaleString() : "too many";
      const maxText = typeof max === "number" ? max.toLocaleString() : "80";
      return `This list has ${eligibleText} shareable leads — the maximum is ${maxText}.`;
    }
    if (code === "share_source_empty") {
      return "This list has no shareable leads. Institutional investors and insiders can't be shared.";
    }
    return null;
  };

  // Bare-code string details, just in case the BE flattens them.
  if (detail === "share_source_empty") {
    return fromCode({ code: "share_source_empty" });
  }
  if (detail === "share_too_large") {
    return "This list has more shareable leads than the maximum of 80.";
  }

  try {
    const parsed = JSON.parse(detail) as unknown;
    const direct = fromCode(parsed);
    if (direct) return direct;
    if (
      parsed &&
      typeof parsed === "object" &&
      "detail" in parsed &&
      typeof (parsed as { detail: unknown }).detail === "object"
    ) {
      return fromCode((parsed as { detail: unknown }).detail);
    }
  } catch {
    // Not JSON — plain human-readable detail; caller shows it verbatim.
  }
  return null;
}
