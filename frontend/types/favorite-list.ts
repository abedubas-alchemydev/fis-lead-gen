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

export interface FavoriteListItem {
  broker_dealer_id: number;
  broker_dealer_name: string;
  added_at: string;
}

export interface PaginatedFavoriteListItems {
  items: FavoriteListItem[];
  total: number;
  page: number;
  page_size: number;
}
