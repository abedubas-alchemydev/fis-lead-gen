import { BrokerDealerDetailClient } from "@/components/master-list/broker-dealer-detail-client";

export default function BrokerDealerDetailPage({ params }: { params: { id: string } }) {
  return <BrokerDealerDetailClient brokerDealerId={params.id} />;
}
