// Typed contract for the admin DOX Share endpoints (/api/v1/shares).
// Mirrors the BE share schemas being built in parallel — keep in sync if
// the contract evolves.
//
// The share PASSWORD appears exactly once, in the create and rotate
// responses; it is hashed at rest and can never be retrieved again. Any FE
// surface holding one of these payloads is the user's only chance to save
// the password.

export interface ShareSummary {
  id: number;
  name: string;
  token: string;
  // Fully-qualified public URL when the BE knows its own origin; null when
  // the FE should derive `${window.location.origin}/share/${token}` itself
  // (see resolveShareUrl in lib/shares-api.ts).
  url: string | null;
  item_count: number;
  created_by: string;
  created_by_email: string;
  created_at: string;
  revoked_at: string | null;
  password_rotated_at: string | null;
  view_count: number;
  last_viewed_at: string | null;
  source_favorite_list_id: number;
}

// Items dropped server-side when creating a share: institutional investors
// and reporting owners (Form 4 insiders) aren't shareable.
export interface SkippedCounts {
  institutional_investors: number;
  reporting_owners: number;
}

export interface CreateShareResponse {
  share: ShareSummary;
  // Shown exactly once — never retrievable after this response is discarded.
  password: string;
  skipped: SkippedCounts;
  warnings: string[];
}

export interface RotatePasswordResponse {
  share_id: number;
  // Shown exactly once — same rule as CreateShareResponse.password.
  password: string;
  password_rotated_at: string;
}

export interface ListSharesResponse {
  items: ShareSummary[];
}

// GET /api/v1/shares/{id} — the snapshot rows recipients see. `kind` stays a
// plain string: the BE only emits shareable kinds (broker-dealer / bank /
// investment-advisor) and the admin UI renders it verbatim, so a widened BE
// enum can't break this type.
export interface ShareDetailItem {
  item_id: number;
  kind: string;
  entity_id: number;
  name: string;
  position: number;
}

export interface ShareDetailResponse {
  share: ShareSummary;
  items: ShareDetailItem[];
}
