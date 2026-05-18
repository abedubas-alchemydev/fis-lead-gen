// Frontend mirror of `backend/app/core/feature_permissions.py`.
// Keep names character-identical so cross-stack grep matches.

export const MASTER_LIST = "master_list" as const;
export const INVESTMENT_ADVISORS = "investment_advisors" as const;
export const INVESTORS = "investors" as const;
export const ALERTS = "alerts" as const;
export const EMAIL_EXTRACTOR = "email_extractor" as const;
export const SENT_OUTREACH = "sent_outreach" as const;
export const MY_FAVORITES = "my_favorites" as const;
export const VISITED_FIRMS = "visited_firms" as const;
export const DASHBOARD = "dashboard" as const;
export const SETTINGS = "settings" as const;
export const USERS = "users" as const;
export const VAULT = "vault" as const;

export const ALL_FEATURE_KEYS = [
  MASTER_LIST,
  INVESTMENT_ADVISORS,
  INVESTORS,
  ALERTS,
  EMAIL_EXTRACTOR,
  SENT_OUTREACH,
  MY_FAVORITES,
  VISITED_FIRMS,
  DASHBOARD,
  SETTINGS,
  USERS,
  VAULT,
] as const;

export type FeatureKey = (typeof ALL_FEATURE_KEYS)[number];

export const FEATURE_LABELS: Record<FeatureKey, string> = {
  master_list: "Master List",
  investment_advisors: "Investment Advisors",
  investors: "Investors",
  alerts: "Alerts",
  email_extractor: "Email Extractor",
  sent_outreach: "Sent Outreach",
  my_favorites: "My Favorites",
  visited_firms: "Visited Firms",
  dashboard: "Dashboard",
  settings: "Settings",
  users: "Users",
  vault: "Vault",
};

export const ENABLED_FEATURE_KEYS: ReadonlySet<FeatureKey> = new Set([
  MASTER_LIST,
  INVESTMENT_ADVISORS,
  INVESTORS,
  ALERTS,
  EMAIL_EXTRACTOR,
  SENT_OUTREACH,
  MY_FAVORITES,
  VISITED_FIRMS,
  DASHBOARD,
  SETTINGS,
  USERS,
  VAULT,
]);

export function hasFeature(
  user: { role?: string | null; feature_permissions?: string[] | null },
  feature: FeatureKey,
): boolean {
  if (user.role === "admin") return true;
  return Array.isArray(user.feature_permissions) && user.feature_permissions.includes(feature);
}
