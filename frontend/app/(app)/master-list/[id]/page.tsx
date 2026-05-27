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
  // `key` forces a fresh remount when the firm id changes so cross-firm
  // navigation (Prev/Next, breadcrumb hops) cleanly resets profile +
  // refreshState rather than briefly flashing the previous firm's data.
  return <BrokerDealerDetailClient key={params.id} brokerDealerId={params.id} />;
}
