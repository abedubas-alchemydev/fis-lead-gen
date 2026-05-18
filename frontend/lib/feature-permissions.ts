// Frontend mirror of `backend/app/core/feature_permissions.py`.
// Keep names character-identical so cross-stack grep matches.

export const MASTER_LIST = "master_list" as const;
export const INVESTMENT_ADVISORS = "investment_advisors" as const;
export const INVESTORS = "investors" as const;
export const INSTITUTIONAL_INVESTORS = "institutional_investors" as const;

export const ALL_FEATURE_KEYS = [
  MASTER_LIST,
  INVESTMENT_ADVISORS,
  INVESTORS,
  INSTITUTIONAL_INVESTORS,
] as const;

export type FeatureKey = (typeof ALL_FEATURE_KEYS)[number];

export const FEATURE_LABELS: Record<FeatureKey, string> = {
  master_list: "Master List",
  investment_advisors: "Investment Advisors",
  investors: "Insider Transactions",
  institutional_investors: "Institutional Investors",
};

export const ENABLED_FEATURE_KEYS: ReadonlySet<FeatureKey> = new Set([
  MASTER_LIST,
  INVESTMENT_ADVISORS,
  INVESTORS,
  INSTITUTIONAL_INVESTORS,
]);

export function hasFeature(
  user: { role?: string | null; feature_permissions?: string[] | null },
  feature: FeatureKey,
): boolean {
  if (user.role === "admin") return true;
  return Array.isArray(user.feature_permissions) && user.feature_permissions.includes(feature);
}
