import { InstitutionalInvestorsWorkspaceClient } from "@/components/institutional-investors/institutional-investors-workspace-client";
import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { getRequiredSession } from "@/lib/auth-server";
import { hasFeature, INSTITUTIONAL_INVESTORS } from "@/lib/feature-permissions";

export default async function InstitutionalInvestorsListPage() {
  const session = await getRequiredSession();
  if (!hasFeature(session.user, INSTITUTIONAL_INVESTORS)) {
    return <FeatureAccessDenied feature={INSTITUTIONAL_INVESTORS} />;
  }
  return <InstitutionalInvestorsWorkspaceClient />;
}
