import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { BrokerDealerDetailClient } from "@/components/master-list/broker-dealer-detail-client";
import { getRequiredSession } from "@/lib/auth-server";
import { hasFeature, MASTER_LIST } from "@/lib/feature-permissions";

export default async function BrokerDealerDetailPage(props: { params: Promise<{ id: string }> }) {
  const params = await props.params;
  const session = await getRequiredSession();
  if (!hasFeature(session.user, MASTER_LIST)) {
    return <FeatureAccessDenied feature={MASTER_LIST} />;
  }
  return <BrokerDealerDetailClient brokerDealerId={params.id} />;
}
