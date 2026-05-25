import { InvestorDetailClient } from "@/components/institutional-investors/investor-detail-client";
import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { getRequiredSession } from "@/lib/auth-server";
import { hasFeature, INSTITUTIONAL_INVESTORS } from "@/lib/feature-permissions";

export default async function InstitutionalInvestorDetailPage(
  props: {
    params: Promise<{ id: string }>;
  }
) {
  const params = await props.params;
  const session = await getRequiredSession();
  if (!hasFeature(session.user, INSTITUTIONAL_INVESTORS)) {
    return <FeatureAccessDenied feature={INSTITUTIONAL_INVESTORS} />;
  }
  return <InvestorDetailClient investorId={params.id} />;
}
