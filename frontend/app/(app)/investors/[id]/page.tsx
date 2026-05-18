import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { InstitutionalInvestorDetailClient } from "@/components/institutional-investors/institutional-investor-detail-client";
import { getRequiredSession } from "@/lib/auth-server";
import { hasFeature, INSTITUTIONAL_INVESTORS } from "@/lib/feature-permissions";

export default async function InstitutionalInvestorDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const session = await getRequiredSession();
  if (!hasFeature(session.user, INSTITUTIONAL_INVESTORS)) {
    return <FeatureAccessDenied feature={INSTITUTIONAL_INVESTORS} />;
  }
  return <InstitutionalInvestorDetailClient investorId={params.id} />;
}
