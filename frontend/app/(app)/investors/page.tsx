import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { InvestorsClient } from "@/components/investors/investors-client";
import { getRequiredSession } from "@/lib/auth-server";
import { hasFeature, INVESTORS } from "@/lib/feature-permissions";

export default async function InvestorsPage() {
  const session = await getRequiredSession();
  if (!hasFeature(session.user, INVESTORS)) {
    return <FeatureAccessDenied feature={INVESTORS} />;
  }
  return <InvestorsClient />;
}
