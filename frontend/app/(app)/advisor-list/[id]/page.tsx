import { AdvisorDetailClient } from "@/components/advisor-list/advisor-detail-client";
import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { getRequiredSession } from "@/lib/auth-server";
import { hasFeature, INVESTMENT_ADVISORS } from "@/lib/feature-permissions";

export default async function AdvisorDetailPage(
  props: {
    params: Promise<{ id: string }>;
  }
) {
  const params = await props.params;
  const session = await getRequiredSession();
  if (!hasFeature(session.user, INVESTMENT_ADVISORS)) {
    return <FeatureAccessDenied feature={INVESTMENT_ADVISORS} />;
  }
  return <AdvisorDetailClient advisorId={params.id} />;
}
