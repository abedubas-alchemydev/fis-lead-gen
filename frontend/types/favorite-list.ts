// Typed contract for the read-only favorite-lists endpoints shipped in
// PR #140 (#17 phase 1, BE). Mirrors `app/schemas/favorite_list.py` —
// keep these in sync if the BE shape evolves.
//
// IDs are integers (BigInteger on the BE), not UUIDs — the original
// design brief sketched UUIDs but PR #140 kept the existing schema's
// BigInteger PK style for consistency across the codebase. The FE
// surfaces ids as strings in URLs (search params) and parses at the
// boundary.

export interface FavoriteList {
  id: number;
  name: string;
  is_default: boolean;
  item_count: number;
  created_at: string;
}

// #17 phase 3 — GET /api/v1/broker-dealers/{firm_id}/favorite-lists
// returns the user's lists with an extra `is_member` flag indicating
// whether THIS firm is currently in THAT list. Used by the list-picker
// dropdown on master-list rows and the firm-detail page so each
// checkbox reflects current membership in O(1) reads.
export interface FavoriteListWithMembership extends FavoriteList {
  is_member: boolean;
}

// Polymorphic item: a list can contain broker-dealers and investment
// advisors. ``entity_type`` discriminates; the opposite entity's fields
// are null. Pre-existing BD callers can still read ``broker_dealer_id`` /
// ``broker_dealer_name`` directly, but should now gate on ``entity_type``
// to avoid rendering broken links when an advisor row shows up.
export type FavoriteListEntityType = "broker_dealer" | "advisor";

export interface FavoriteListItem {
  entity_type: FavoriteListEntityType;
  broker_dealer_id: number | null;
  broker_dealer_name: string | null;
  advisor_id: number | null;
  advisor_name: string | null;
  added_at: string;
}

export interface PaginatedFavoriteListItems {
  items: FavoriteListItem[];
  total: number;
  page: number;
  page_size: number;
}
