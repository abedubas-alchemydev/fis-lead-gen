import { AdvisorDetailClient } from "@/components/advisor-list/advisor-detail-client";

export default function AdvisorDetailPage({
  params,
}: {
  params: { id: string };
}) {
  return <AdvisorDetailClient advisorId={params.id} />;
}
