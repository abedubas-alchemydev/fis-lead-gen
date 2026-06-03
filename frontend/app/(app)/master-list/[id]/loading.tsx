// Mirrors the in-component `!profile` fallback inside BrokerDealerDetailClient
// via the shared DetailPageSkeleton — keeps the route-level Suspense fallback
// visually identical to the in-component fallback so the user never sees a
// spinner→skeleton handoff while navigating to a firm.
import { DetailPageSkeleton } from "@/components/ui/detail-page-skeleton";

export default function BrokerDealerDetailLoading() {
  return <DetailPageSkeleton />;
}
