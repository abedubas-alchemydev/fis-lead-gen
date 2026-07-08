// Transport types for the PUBLIC share surface (/share/{token}).
//
// These mirror the backend's public-share contract exactly (see
// backend /api/v1/public/shares/*). The viewer is anonymous — no session,
// no user record. Unlock state lives in an HttpOnly cookie the browser
// carries; the client NEVER reads it and instead derives locked/unlocked
// purely from fetch outcomes (401 = locked, 404 = unknown/revoked link).

export type PublicShareKind = "broker_dealer" | "bank" | "advisor";

// GET /api/v1/public/shares/{token}
export interface PublicShareMeta {
  name: string;
  unlocked: boolean;
  // Only populated when unlocked; null while the share is still locked.
  item_count: number | null;
}

// One row of GET /api/v1/public/shares/{token}/items. The row is a union
// flattened into one shape: per-kind fields are null for the other kinds
// (e.g. a bank row carries null crd_number / latest_net_capital).
export interface PublicShareItemRow {
  item_id: number;
  kind: PublicShareKind;
  position: number;
  name: string;
  city: string | null;
  state: string | null;
  // Broker-dealer fields
  crd_number: string | null;
  latest_net_capital: number | null;
  health_status: string | null;
  current_clearing_partner: string | null;
  // Investment-advisor fields
  regulatory_aum: number | null;
  total_clients: number | null;
  // Bank fields
  charter_status: string | null;
  charter_type: string | null;
  asset: number | null;
}

// GET /api/v1/public/shares/{token}/items
export interface PublicShareItemsResponse {
  name: string;
  items: PublicShareItemRow[];
}

// GET /api/v1/public/shares/{token}/items/{item_id}/profile — kind-enveloped;
// exactly ONE of the payload keys is populated, matching `kind`. The payload
// shapes are owned by the read-only profile renderers' contract
// (components/share/profiles/types), so they stay `unknown` here and are
// narrowed at the dispatch site in share-profile-view.
export interface PublicShareProfileResponse {
  item_id: number;
  kind: PublicShareKind;
  broker_dealer?: unknown;
  bank?: unknown;
  advisor?: unknown;
}
