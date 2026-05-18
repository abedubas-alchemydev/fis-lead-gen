import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { InvestorsClient } from "@/components/investors/investors-client";
import { getRequiredSession } from "@/lib/auth-server";
import { hasFeature, INVESTORS } from "@/lib/feature-permissions";

// The SEC Form 4 insider-transaction feed lives here now. The /investors
// route was repurposed for the new firm-style Institutional Investors
// list. Feature gate stays on the legacy INVESTORS key so existing
// permissions carry over without a migration.
export default async function InsiderTransactionsPage() {
  const session = await getRequiredSession();
  if (!hasFeature(session.user, INVESTORS)) {
    return <FeatureAccessDenied feature={INVESTORS} />;
  }
  return <InvestorsClient />;
}
