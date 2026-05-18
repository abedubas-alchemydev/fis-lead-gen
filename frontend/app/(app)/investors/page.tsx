import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { InstitutionalInvestorsWorkspaceClient } from "@/components/institutional-investors/institutional-investors-workspace-client";
import { getRequiredSession } from "@/lib/auth-server";
import { hasFeature, INSTITUTIONAL_INVESTORS } from "@/lib/feature-permissions";

// /investors is now the firm-style Institutional Investors list (13F
// filers). The legacy SEC Form 4 insider-transaction feed moved to
// /insider-transactions and gates on the legacy INVESTORS permission.
export default async function InvestorsPage() {
  const session = await getRequiredSession();
  if (!hasFeature(session.user, INSTITUTIONAL_INVESTORS)) {
    return <FeatureAccessDenied feature={INSTITUTIONAL_INVESTORS} />;
  }
  return <InstitutionalInvestorsWorkspaceClient />;
}
