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
